"""Automatic discovery of competitor products (docs/AUTO_DISCOVERY_PLAN.md).

The worker calls `step()` on every loop. One call makes at most one request to a competitor's website, and
only when that is allowed: discovery is on, the website's terms were confirmed, the night window is open
(unless an administrator asked for a run now), the nightly request budget is not used up and enough time
has passed since the last request to that domain (at least `spacing_seconds`, robots.txt Crawl-delay and
the competitor's own minimal interval). A run is stored in the database, so it continues after a restart
or on the next night.

Discovery run (weekly):  detect source (once) -> list pages -> compare with the catalogue (new / gone)
                         -> read product pages of new items -> match -> track (automatic mode only)
Sources: an adapter, a sitemap, a feed, listing pages, or the guided crawler ("crawl", docs/
SCRAPING_ROBUSTNESS_PLAN.md) that follows internal links from entry pages, reads product pages on the way
and learns which URL templates lead to products. When detection finds no sitemap, or a source returns no
products, the run falls back to the crawler instead of failing.
Prices run (daily):      list pages only, to update offers whose price is read from the listing.

Safety: robots.txt is respected; HTTP 403/429 or a bot challenge stops discovery of the competitor for
7 days, as do 3 failed requests in a row; when a source suddenly returns far fewer products, nothing is
marked as gone and the run stops with a warning.
"""
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CatalogItem,
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    DiscoveryRun,
    DiscoveryUrl,
    ScrapeConfig,
    ScrapeRun,
)
from app.scraping import catalog, crawl
from app.scraping.discover import discover_links, query_says_product
from app.scraping.fetcher import Fetcher, FetchError
from app.scraping.parsers.base import Extracted, PageContext, ParseError
from app.scraping.parsers.registry import get_parser
from app.scraping.urlnorm import safe_canonical, template
from app.services import alerts, pricing, scheduling
from app.services.comparable import OwnCatalogue, plain
from app.services.urls import InvalidUrl, normalize_url

log = logging.getLogger("discovery")
LOCK_ID = 727_004
PAUSE_DAYS = 7
ERRORS_TO_STOP = 3
SHADOW_RUNS_TO_PROMOTE = 2
SHADOW_RETRY_HOURS = 24          # the second shadow run comes a day after the first, not a week
MIN_PRICED_SHARE = 0.9           # quality needed to leave the shadow mode
MAX_COUNT_CHANGE = 0.2           # … and a stable number of products between runs
SUSPICIOUS_DROP = 0.5            # fewer than half of last run's products: stop instead of marking them gone
DIAGNOSIS_DAYS = 30              # the chosen methods are tried anew after this
CLEANUP_EVERY = 20               # requests between corrections of duplicates and boilerplate in a long run
BOILERPLATE_PAGES = 3            # the same title and price on this many pages of one type: not products
FORBIDDEN_TO_STOP = 3            # HTTP 403 on this many different pages in a row: the website blocks us
ENRICH_WINDOW = 500              # items considered when choosing the next product page to read
EARLY_SAMPLE = 15                # pages of a sitemap read before it is judged (no product: next method)
# When the result of a method is sufficient (docs/SCRAPING_ROBUSTNESS_PLAN.md, part 6)
MIN_PRICED = 0.8
MIN_TITLED = 0.95
MIN_PLAUSIBLE = 0.9
FEW_PRODUCTS = 5
PLAUSIBLE_PRICE = (Decimal("1"), Decimal("20000"))
SHOP_HINT_LINKS = 10             # links from the home page to another domain that suggest a shop there
MIN_LISTING_LINKS = 12           # a page with at least this many product links is a product listing
MAX_LIST_PAGES = 300
MAX_DETECT_CANDIDATES = 3
METHOD_NAMES = {"adapter": "adaptér webu", "feed": "produktový feed", "sitemap": "sitemap",
                "listing": "zoznam produktov", "crawl": "prechádzanie webu"}
CATALOG_WORDS = ("shop", "eshop", "e-shop", "obchod", "katalog", "produkty", "products", "ucebnice", "knihy",
                 "sortiment", "nabidka", "ponuka", "vsechny", "vsetky")


# ---------- settings ----------

@dataclass
class SourceSpec:
    type: str
    urls: list[str]
    pattern: str | None = None
    adapter: str | None = None


class DiscoverySource(BaseModel):
    type: Literal["sitemap", "feed", "listing", "adapter", "crawl"]
    urls: list[str] = Field(min_length=1, max_length=20)
    pattern: str | None = Field(default=None, max_length=300)  # regular expression on the URL path
    adapter: str | None = None
    detected: bool = True  # found automatically (False = entered by an administrator)

    @field_validator("urls")
    @classmethod
    def absolute(cls, urls: list[str]) -> list[str]:
        try:
            return [normalize_url(u) for u in urls]
        except InvalidUrl as exc:
            raise ValueError(str(exc)) from None

    @field_validator("pattern")
    @classmethod
    def regex(cls, value: str | None) -> str | None:
        if not value:
            return None
        import re
        try:
            re.compile(value)
        except re.error:
            raise ValueError("Neplatný regulárny výraz.") from None
        return value


class DiscoverySettings(BaseModel):
    mode: Literal["off", "shadow", "auto"] = "off"
    terms_confirmed_at: datetime | None = None
    terms_confirmed_by: str | None = None
    interval_days: int = Field(default=7, ge=1, le=60)
    budget_per_night: int = Field(default=120, ge=10, le=1000)
    spacing_seconds: int = Field(default=10, ge=5, le=300)
    source: DiscoverySource | None = None
    # crawler limits and site rules (docs/SCRAPING_ROBUSTNESS_PLAN.md, 5.4)
    max_pages: int = Field(default=400, ge=10, le=20000)   # pages read by one run
    max_depth: int = Field(default=4, ge=1, le=10)         # link steps from an entry page
    max_run_days: int = Field(default=14, ge=1, le=60)     # a run spread over nights ends after this
    # recipe of the website, entered by an administrator when automatic detection is not enough (part 5.4)
    strategy: Literal["auto", "adapter", "feed", "sitemap", "crawl"] = "auto"  # forced method
    feed_url: str | None = Field(default=None, max_length=2048)
    entry_urls: list[str] = Field(default_factory=list, max_length=20)    # where the crawler starts
    extra_domains: list[str] = Field(default_factory=list, max_length=10)  # e.g. a shop on another domain
    ignored_domains: list[str] = Field(default_factory=list, max_length=20)  # never read nor suggested
    include_patterns: list[str] = Field(default_factory=list, max_length=20)  # regex: only these URLs
    exclude_patterns: list[str] = Field(default_factory=list, max_length=20)  # regex: never these URLs
    product_pattern: str | None = Field(default=None, max_length=300)       # regex: product pages
    drop_params: list[str] = Field(default_factory=list, max_length=50)   # URL parameters to ignore
    learned_templates: dict[str, dict[str, int]] = Field(default_factory=dict)  # yield per URL template
    # diagnosis: which methods were tried, with what quality, which ones are used (part 6)
    diagnosis: dict[str, Any] = Field(default_factory=dict)
    # state, kept by the runner
    run_requested: bool = False
    next_run_at: datetime | None = None
    next_prices_at: datetime | None = None
    last_run_id: int | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    paused_until: datetime | None = None
    shadow_ok_runs: int = 0
    temporary_pauses: int = 0  # pauses after temporary errors in a row (1, 2, 4, 7 days)
    budget_date: str | None = None
    budget_used: int = 0


def _regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error:
        raise ValueError(f"Neplatný regulárny výraz: {value}") from None
    return value


class Recipe(BaseModel):
    """The editable part of a website's recipe (validated input of the API)."""
    strategy: Literal["auto", "adapter", "feed", "sitemap", "crawl"] = "auto"
    feed_url: str | None = Field(default=None, max_length=2048)
    entry_urls: list[str] = Field(default_factory=list, max_length=20)
    extra_domains: list[str] = Field(default_factory=list, max_length=10)
    ignored_domains: list[str] = Field(default_factory=list, max_length=20)
    include_patterns: list[str] = Field(default_factory=list, max_length=20)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=20)
    product_pattern: str | None = Field(default=None, max_length=300)
    drop_params: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("feed_url")
    @classmethod
    def feed(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        try:
            return normalize_url(value)
        except InvalidUrl as exc:
            raise ValueError(str(exc)) from None

    @field_validator("entry_urls")
    @classmethod
    def entries(cls, values: list[str]) -> list[str]:
        try:
            return [normalize_url(v) for v in values if v.strip()]
        except InvalidUrl as exc:
            raise ValueError(str(exc)) from None

    @field_validator("extra_domains", "ignored_domains")
    @classmethod
    def domains(cls, values: list[str]) -> list[str]:
        result = []
        for value in (v.strip().lower() for v in values if v.strip()):
            host = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
            if "." not in host:
                raise ValueError(f"Neplatná doména: {value}")
            result.append(host.removeprefix("www."))
        return result

    @field_validator("include_patterns", "exclude_patterns")
    @classmethod
    def patterns(cls, values: list[str]) -> list[str]:
        return [_regex(v) for v in values if v.strip()]

    @field_validator("product_pattern")
    @classmethod
    def product(cls, value: str | None) -> str | None:
        return _regex(value) if value and value.strip() else None

    @field_validator("drop_params")
    @classmethod
    def params(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values if v.strip()]

    @model_validator(mode="after")
    def no_conflict(self) -> "Recipe":
        both = set(self.extra_domains) & set(self.ignored_domains)
        if both:
            raise ValueError(f"Doména nemôže byť naraz ďalšia aj nepoužívaná: {', '.join(sorted(both))}")
        return self


RECIPE_FIELDS = tuple(Recipe.model_fields)
# Changing these finds and judges the methods anew; ignoring a domain does not (a running run goes on).
REDETECT_FIELDS = tuple(f for f in RECIPE_FIELDS if f != "ignored_domains")


def is_ignored(text: str, ignored: list[str]) -> bool:
    return any(d in text.lower() for d in ignored)


def load(config: ScrapeConfig) -> DiscoverySettings:
    return DiscoverySettings.model_validate(config.discovery or {})


def save(config: ScrapeConfig, settings: DiscoverySettings) -> None:
    config.discovery = settings.model_dump(mode="json")  # a new object, so the JSONB change is saved


# ---------- the worker entry point ----------

class Politeness:
    """Shares the per-domain request times with the scrape job processor, so both respect one spacing."""

    def __init__(self, last_request: dict[str, float], blocked_until: dict[str, float]):
        self.last_request, self.blocked_until = last_request, blocked_until

    def ready(self, domain: str, spacing: float) -> bool:
        now = time.monotonic()
        return self.blocked_until.get(domain, 0) <= now and self.last_request.get(domain, 0) + spacing <= now

    def mark(self, domain: str) -> None:
        self.last_request[domain] = time.monotonic()


def step(db: Session, fetcher: Fetcher, politeness: Politeness, now: datetime | None = None) -> bool:
    """Advance discovery by at most one request. Returns True when a request was made. Commits."""
    now = now or datetime.now(UTC)
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK_ID))):
        db.rollback()
        return False
    _start_due_runs(db, now)
    made = False
    for run in db.scalars(select(DiscoveryRun).where(DiscoveryRun.status == "running").order_by(DiscoveryRun.id)):
        if Runner(db, run, fetcher, politeness, now).advance():
            made = True
            break  # one request per call
    db.commit()
    return made


def _start_due_runs(db: Session, now: datetime) -> None:
    schedule = scheduling.load_settings(db)
    open_runs = set(db.scalars(select(DiscoveryRun.competitor_id).where(DiscoveryRun.status == "running")))
    for competitor in db.scalars(select(Competitor).where(Competitor.status == "active")):
        config = competitor.scrape_config
        if config is None or competitor.id in open_runs:
            continue
        settings = load(config)
        if settings.mode == "off" or settings.terms_confirmed_at is None or not config.enabled:
            continue
        if settings.paused_until and settings.paused_until > now and not settings.run_requested:
            continue
        in_window = scheduling.in_window(schedule, now) and schedule.scheduler_enabled
        manual = settings.run_requested
        if manual or (in_window and (settings.next_run_at is None or settings.next_run_at <= now)):
            kind = "discovery"
        elif in_window and settings.mode == "auto" and settings.source and _has_listing_offers(db, competitor.id) \
                and (settings.next_prices_at is None or settings.next_prices_at <= now):
            kind = "prices"
        else:
            continue
        if manual:
            settings.paused_until, settings.run_requested = None, False
        source = settings.source
        # A valid diagnosis (or a source entered by hand) is used as it is; otherwise the methods are found anew.
        ready = source is not None and (kind == "prices" or not source.detected or diagnosis_valid(settings, now))
        plan, sources = [], {}
        if ready and source.detected and kind == "discovery":
            plan = list(settings.diagnosis.get("chosen") or [source.type])
            sources = dict(settings.diagnosis.get("sources") or {})
        if ready and (not plan or source.type not in sources):
            plan = plan or [source.type]
            sources = {**sources, source.type: {"urls": source.urls, "pattern": source.pattern,
                                                "adapter": source.adapter}}
        if ready and plan[0] != source.type:  # the run starts with the first chosen method
            first = sources[plan[0]]
            source = DiscoverySource(type=plan[0], urls=first["urls"], pattern=first.get("pattern"),
                                     adapter=first.get("adapter"), detected=True)
            settings.source = source
        run = DiscoveryRun(competitor_id=competitor.id, kind=kind, mode=settings.mode, status="running",
                           phase="list" if ready else "detect",
                           stats={"manual": manual, "plan": plan, "sources": sources,
                                  "current": source.type if ready else None},
                           source_type=source.type if ready else None,
                           queue=list(source.urls) if ready else [], started_at=now)
        db.add(run)
        db.flush()
        settings.last_run_id = run.id if kind == "discovery" else settings.last_run_id
        save(config, settings)
        log.info("Discovery run %s (%s) started for competitor %s", run.id, kind, competitor.id)


def _has_listing_offers(db: Session, competitor_id: int) -> bool:
    return bool(db.scalar(select(func.count(CompetitorProduct.id)).where(
        CompetitorProduct.competitor_id == competitor_id, CompetitorProduct.price_source == "listing",
        CompetitorProduct.status == "active")))


def request_run(config: ScrapeConfig) -> None:
    settings = load(config)
    settings.run_requested = True
    save(config, settings)


# ---------- one run ----------

class Stop(Exception):
    """Stop the run (and pause discovery of the competitor when `pause` is set)."""

    def __init__(self, message: str, pause: bool = False, status: str = "stopped", days: int = PAUSE_DAYS):
        super().__init__(message)
        self.pause, self.status, self.days = pause, status, days


class Runner:
    def __init__(self, db: Session, run: DiscoveryRun, fetcher: Fetcher, politeness: Politeness, now: datetime):
        self.db, self.run, self.fetcher, self.politeness, self.now = db, run, fetcher, politeness, now
        self.competitor = db.get(Competitor, run.competitor_id)
        self.config = self.competitor.scrape_config
        self.settings = load(self.config)

    # ----- bookkeeping -----

    def _stat(self, key: str, add: int = 1) -> None:
        self.run.stats = {**self.run.stats, key: self.run.stats.get(key, 0) + add}

    def _set_stats(self, **values: Any) -> None:
        self.run.stats = {**self.run.stats, **values}

    def _spacing(self, url: str) -> float:
        delay = self.fetcher.min_interval(url) if self.fetcher.robots_cached(url) else 0  # Crawl-delay, Request-rate
        return max(self.settings.spacing_seconds, self.config.min_interval_seconds, delay)

    def _budget_left(self) -> bool:
        today = datetime.now(UTC).astimezone(ZoneInfo(get_settings().timezone)).date().isoformat()
        if self.settings.budget_date != today:
            self.settings.budget_date, self.settings.budget_used = today, 0
        return self.settings.budget_used < self.settings.budget_per_night

    def _may_request(self, url: str) -> bool:
        if not self.run.stats.get("manual"):
            schedule = scheduling.load_settings(self.db)
            if not (schedule.scheduler_enabled and scheduling.in_window(schedule, self.now)):
                return False  # continues in the next night window
        if not self._budget_left():
            return False
        return self.politeness.ready(urlsplit(url).hostname or "", self._spacing(url))

    def _count_request(self, url: str) -> None:
        self.politeness.mark(urlsplit(url).hostname or "")
        self.settings.budget_used += 1
        self.run.requests += 1

    def _fetch(self, url: str, max_bytes: int | None = None):
        """One request. robots.txt is already loaded (a separate step), so this is the only request."""
        if not self.fetcher.robots_allowed(url):
            raise FetchError("Súbor robots.txt webu zakazuje načítanie tejto stránky.", "permanent", 403)
        self._count_request(url)
        return self.fetcher.fetch(url, timeout=self.config.timeout_seconds, max_bytes=max_bytes)

    def _save(self) -> None:
        save(self.config, self.settings)

    # ----- driver -----

    def advance(self) -> bool:
        """Do the next piece of work. Returns True when a request was made."""
        try:
            for _ in range(10):  # steps without a request (compare, match, finish) follow directly
                if self.run.status != "running":
                    return False
                url = self._next_url()
                if url is None:
                    self._without_request()
                    continue
                if not self._may_request(url):
                    self._save()
                    return False
                self._with_request(url)
                self._save()
                return True
            return False
        except Stop as exc:
            self._stop(str(exc), exc.pause, exc.status, exc.days)
            return True

    def _next_url(self) -> str | None:
        phase = self.run.phase
        if phase == "detect":
            url = self._detect_url()
            return url if url is not None or self.run.phase == "detect" else self._next_url()
        if phase == "list":
            if self.settings.source and self.settings.source.type == "crawl":
                row = self._crawl_next()
                return row.url if row else None
            return self.run.queue[0] if self.run.queue else None
        if phase == "enrich":
            item = self._next_to_enrich()
            return item.url if item else None
        return None

    def _with_request(self, url: str) -> None:
        try:
            if not self.fetcher.robots_cached(url):  # robots.txt first, as a request of its own
                self._count_request(url)
                self.fetcher.robots_allowed(url)
                return
            if self.run.phase == "detect":
                self._detect_with(url)
            elif self.run.phase == "list" and self.settings.source.type == "crawl":  # the crawler
                self._crawl_page(url)
            elif self.run.phase == "list":
                self._list_page(url)
            else:
                self._enrich(url)
            self.run.errors_in_row = 0
            if self.run.kind == "discovery" and self.run.requests % CLEANUP_EVERY == 0:
                self._drop_boilerplate()  # long runs: wrong products are corrected on the way, not only at the end
            if self.run.stats.get("forbidden_in_row"):
                self.run.stats = {**self.run.stats, "forbidden_in_row": 0}
        except FetchError as exc:
            self._request_failed(url, exc)

    def _without_request(self) -> None:
        if self.run.phase == "list":
            if self.run.kind == "prices":
                self._finish_prices()
            else:
                self.run.phase = "enrich"
        elif self.run.phase == "enrich":
            if self._evaluate_method():
                return  # the next method of the plan continues
            self._reconcile()
            self.run.phase = "done"
            self._match()
            if self.settings.mode == "auto":
                self._track()
            self._finish_discovery()
        elif self.run.phase == "detect":
            self._make_plan()

    def _request_failed(self, url: str, exc: FetchError) -> None:
        """A failed request. The whole website is treated as blocking us only on HTTP 429, a bot challenge,
        403 on the home page or entry pages, or 403 on several different pages in a row; a single page that
        is closed (403, e.g. a members' forum) or missing (404) is recorded as a failure of that page."""
        forbidden = exc.status == 403 and "botom" not in str(exc)
        if forbidden:
            self.run.stats = {**self.run.stats, "forbidden_in_row": self.run.stats.get("forbidden_in_row", 0) + 1}
            self._stat("forbidden_urls")
        page_level = forbidden and self.run.phase in ("list", "enrich") \
            and self.run.stats["forbidden_in_row"] < FORBIDDEN_TO_STOP and not self._is_entry(url)
        if (exc.status in (403, 429) or "botom" in str(exc)) and not page_level:
            if exc.status == 429 and exc.retry_after:
                self.politeness.blocked_until[urlsplit(url).hostname or ""] = time.monotonic() + exc.retry_after
            raise Stop(f"Web obmedzil prístup ({exc}, {url}). Objavovanie sa pozastavuje na {PAUSE_DAYS} dní.",
                       pause=True)
        if self.run.phase == "enrich" and exc.status in (403, 404, 410):
            item = self._next_to_enrich()
            if item is not None:
                self._enriched(item, error=f"{exc} ({url})", status="failed")
                self._count_template(url)
            return
        crawling = self.run.phase == "list" and self.run.stats.get("current") == "crawl"
        if crawling:
            row = self._row(url)
            if row is not None:
                row.status, row.http_status, row.error, row.fetched_at = "failed", exc.status, str(exc)[:500], self.now
            if exc.status in (404, 410):
                self._stat("broken_links")  # a dead link on the website, not a gap in what was read
                return
            if page_level:
                self._count_template(url)
                return  # one closed page: recorded, the crawl goes on
            self._stat("failed_urls")
        self.run.errors_in_row += 1
        self._set_stats(last_error=str(exc)[:300])
        if self.run.errors_in_row >= ERRORS_TO_STOP:
            if exc.kind == "temporary":  # timeouts, server errors: the website may be down for a while
                days = min(PAUSE_DAYS, 2 ** self.settings.temporary_pauses)
                self.settings.temporary_pauses += 1
                raise Stop(f"{ERRORS_TO_STOP} neúspešné požiadavky za sebou ({exc}). Web je zrejme dočasne "
                           f"nedostupný; objavovanie pokračuje o {days} {'deň' if days == 1 else 'dni'}.",
                           pause=True, days=days)
            raise Stop(f"{ERRORS_TO_STOP} neúspešné požiadavky za sebou ({exc}). Objavovanie sa pozastavuje na "
                       f"{PAUSE_DAYS} dní.", pause=True)
        if self.run.phase == "list" and self.run.queue and exc.kind == "permanent":
            self.run.queue = self.run.queue[1:]  # a broken page of the source: skip it

    def _stop(self, message: str, pause: bool, status: str, days: int = PAUSE_DAYS) -> None:
        self.run.status, self.run.error, self.run.finished_at = status, message[:2000], self.now
        self.settings.last_error = message[:500]
        if pause:
            self.settings.paused_until = self.now + timedelta(days=days)
            self.settings.shadow_ok_runs = 0
        if self.run.kind == "discovery":
            self.settings.next_run_at = self.now + timedelta(days=self.settings.interval_days)
        else:
            self.settings.next_prices_at = self.now + timedelta(days=1)
        self._save()
        log.warning("Discovery run %s stopped: %s", self.run.id, message)

    # ----- B: detecting the methods (docs/SCRAPING_ROBUSTNESS_PLAN.md, part 6) -----

    def _detect_state(self) -> dict:
        return dict(self.run.stats.get("detect") or {"step": 0})

    def _detect_url(self) -> str | None:
        """Requests of the detection: (robots.txt) → /sitemap.xml → home page; steps that are not needed are
        skipped. Returns None when the detection is complete (then the plan of methods is made)."""
        state = self._detect_state()
        base = self.competitor.base_url
        strategy = self.settings.strategy
        while True:
            step = state["step"]
            if step == 0:
                adapter = catalog.adapter_for(self.competitor.domain)
                if strategy == "adapter" or (strategy == "auto" and adapter is not None):
                    self._make_plan()
                    return None
                if strategy in ("feed", "crawl"):
                    state["step"] = 2 if strategy == "crawl" else 3
                    self._set_stats(detect=state)
                    continue
                return base  # robots.txt (loaded first) and its sitemaps
            if step == 1:
                if state.get("sitemaps") or strategy not in ("auto", "sitemap"):
                    state["step"] = 2
                    self._set_stats(detect=state)
                    continue
                return urljoin(base, "/sitemap.xml")
            if step == 2:
                if strategy == "sitemap":
                    state["step"] = 3
                    self._set_stats(detect=state)
                    continue
                return base
            return None

    def _make_plan(self) -> None:
        """The methods to try, cheapest first: adapter or feed, sitemap, then the crawler (always last).
        A method of the recipe ("strategy") is used alone."""
        state = self._detect_state()
        strategy, base = self.settings.strategy, self.competitor.base_url
        sources: dict[str, dict] = {}
        adapter = catalog.adapter_for(self.competitor.domain)
        if adapter is not None and strategy in ("auto", "adapter"):
            sources["adapter"] = {"urls": [urljoin(base, u) for u in adapter.start_urls], "adapter": adapter.name}
        elif strategy == "adapter":
            raise Stop("Pre tento web nie je adaptér. V recepte zvoľte iný spôsob.", status="failed")
        else:
            if self.settings.feed_url and strategy in ("auto", "feed"):
                sources["feed"] = {"urls": [self.settings.feed_url]}
            if state.get("sitemaps") and strategy in ("auto", "sitemap"):
                sources["sitemap"] = {"urls": state["sitemaps"][:5], "pattern": self.settings.product_pattern}
            if strategy in ("auto", "crawl"):
                entries = self.settings.entry_urls + list(state.get("candidates", [])) + [base]
                sources["crawl"] = {"urls": list(dict.fromkeys(entries))[:20]}
        if not sources:
            missing = {"feed": "adresu feedu v recepte", "sitemap": "sitemap (web ju nemá)"}.get(strategy, "zdroj")
            raise Stop(f"Zvolený spôsob „{strategy}“ nemožno použiť: chýba {missing}.", status="failed")
        plan = list(sources)
        self._set_stats(plan=plan, sources=sources)
        self._attempt("plán", " → ".join(METHOD_NAMES[m] for m in plan))
        self._start_method(plan[0])

    def _start_method(self, name: str) -> None:
        source = self.run.stats["sources"][name]
        self.settings.source = DiscoverySource(type=name, urls=source["urls"], pattern=source.get("pattern"),
                                               adapter=source.get("adapter"), detected=True)
        self.run.source_type, self.run.phase, self.run.queue = name, "list", list(source["urls"])
        self._set_stats(current=name, detected=self.run.stats.get("detected") or name)
        self._save()

    def _evaluate_method(self) -> bool:
        """Quality of the method just finished; when it is not sufficient, start the next one of the plan.
        Returns True when another method continues."""
        name = self.run.stats.get("current") or (self.settings.source.type if self.settings.source else None)
        if name is None:
            return False
        seen = set(self.run.seen_keys)
        items = [i for i in self.db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.found_by == name)) if i.key in seen]
        result = quality_of(items)
        results = {**(self.run.stats.get("methods") or {}), name: result}
        self._set_stats(methods=results)
        plan = list(self.run.stats.get("plan") or [name])
        following = plan[plan.index(name) + 1:] if name in plan else []
        if result["sufficient"] or not following:
            self._attempt(METHOD_NAMES.get(name, name), _describe_quality(result))
            return False
        self._attempt(METHOD_NAMES.get(name, name),
                      f"{_describe_quality(result)}; pokračuje {METHOD_NAMES[following[0]]}")
        self._start_method(following[0])
        return True

    def _attempt(self, what: str, result: str) -> None:
        """Diagnostics: what was tried to find the products, and with what result."""
        attempts = list(self.run.stats.get("attempts") or [])
        attempts.append({"what": what, "result": result[:300]})
        self._set_stats(attempts=attempts[-30:])

    def _detect_with(self, url: str) -> None:
        state = self._detect_state()
        step = state["step"]
        state["step"] = step + 1
        self._set_stats(detect=state)
        domain = self.competitor.domain
        if step == 0:  # sitemaps announced in robots.txt
            sitemaps = [s for s in self.fetcher.sitemaps(self.competitor.base_url) if catalog._belongs(s, domain)]
            self._attempt("robots.txt", f"sitemap: {', '.join(sitemaps)}" if sitemaps else "bez sitemap")
            state["sitemaps"] = sitemaps
            self._set_stats(detect=state)
            return
        try:
            response = self._fetch(url, max_bytes=5 * 1024 * 1024)
        except FetchError as exc:
            if exc.status in (404, 410):
                self._attempt(urlsplit(url).path or "/", f"neexistuje (HTTP {exc.status})")
                return  # this candidate does not exist; try the next one
            raise
        text = response.text or ""
        if step == 1:  # /sitemap.xml
            try:
                result = catalog.read_sitemap(text, url, domain)
            except ValueError:
                self._attempt("/sitemap.xml", "nie je platná sitemap")
                return
            if result.items or result.next_urls:
                self._attempt("/sitemap.xml", f"{len(result.items) or len(result.next_urls)} záznamov")
                state["sitemaps"] = [response.url]
                self._set_stats(detect=state)
            return
        # The home page: links that look like the catalogue become entry pages of the crawler; many links to
        # another domain suggest that the shop is there (the administrator decides, see the recipe).
        links = [c for c in discover_links(text, response.url, domain, set()) if c["score"] >= 2]
        state["candidates"] = _catalogue_links(text, response.url, domain)
        self._set_stats(detect=state)
        self._attempt("domovská stránka", f"{len(links)} produktových odkazov, katalóg: "
                                          f"{', '.join(state['candidates']) or 'nenájdený'}")
        shop = _shop_domain_hint(text, response.url, self._domains() | set(self.settings.ignored_domains))
        if shop:
            self._set_stats(suggestions=[f"Domovská stránka má {shop[1]} odkazov na doménu {shop[0]}. Ak je tam obchod "
                                         "konkurenta, pridajte ju v recepte medzi ďalšie domény a ako vstupnú stránku "
                                         f"zadajte https://{shop[0]}/ (podmienky toho webu treba tiež skontrolovať)."])

    # ----- C: the guided crawler (source "crawl") -----

    def _domains(self) -> set[str]:
        domains = {self.competitor.domain, *(d.lower().removeprefix("www.") for d in self.settings.extra_domains)}
        return domains - set(self.settings.ignored_domains)

    def _drop(self) -> set[str]:
        return set(self.settings.drop_params)

    def _template_stats(self) -> dict[str, dict[str, int]]:
        return crawl.merged(self.settings.learned_templates, self.run.stats.get("templates") or {})

    def _count_template(self, url: str, product: bool = False, listing: bool = False) -> None:
        templates = {k: dict(v) for k, v in (self.run.stats.get("templates") or {}).items()}
        row = templates.setdefault(template(url), {"pages": 0, "products": 0, "listings": 0})
        row["pages"] += 1
        row["products"] += int(product)
        row["listings"] += int(listing)
        self._set_stats(templates=templates)

    def _row(self, url: str) -> DiscoveryUrl | None:
        return self.db.scalar(select(DiscoveryUrl).where(DiscoveryUrl.competitor_id == self.competitor.id,
                                                         DiscoveryUrl.url == url))

    def _seed(self) -> None:
        """Start of a crawl run: entry pages, and listings of earlier runs read again (new products appear there).
        Product pages read before are not read again; failed pages get one more try."""
        drop = self._drop()
        entries = [u for u in (safe_canonical(u, drop) for u in self.settings.source.urls) if u]
        self._enqueue(entries, depth=0, priority=crawl.PRIORITY_ENTRY)
        self.db.execute(update(DiscoveryUrl).where(
            DiscoveryUrl.competitor_id == self.competitor.id, DiscoveryUrl.run_id != self.run.id,
            or_(DiscoveryUrl.kind.in_(("listing", "category")), DiscoveryUrl.status == "failed"),
        ).values(status="queued", run_id=self.run.id, error=None))
        self._set_stats(seeded=True)

    def _crawl_next(self) -> DiscoveryUrl | None:
        if not self.run.stats.get("seeded"):
            self._seed()
        if self.run.stats.get("limit_hit"):
            return None
        stats = self._template_stats()
        while True:
            row = self.db.scalar(select(DiscoveryUrl).where(
                DiscoveryUrl.competitor_id == self.competitor.id, DiscoveryUrl.run_id == self.run.id,
                DiscoveryUrl.status == "queued").order_by(DiscoveryUrl.priority, DiscoveryUrl.depth, DiscoveryUrl.id))
            if row is None:
                return None
            if row.depth == 0 or not crawl.dead(row.template, stats):
                break
            # Queued before its page type proved to have no products (e.g. "how to order" pages with a product
            # parameter): skipped without a request.
            row.status, row.error = "skipped", "Preskočené: stránky tohto typu nie sú produkty ani zoznamy."
            self._stat("dead_links")
        limit = None
        if self.run.stats.get("pages", 0) >= self.settings.max_pages:
            limit = f"dosiahnutý limit {self.settings.max_pages} stránok na beh"
        elif self.now - self.run.started_at > timedelta(days=self.settings.max_run_days):
            limit = f"beh trval dlhšie ako {self.settings.max_run_days} dní"
        if limit:
            skipped = self.db.execute(update(DiscoveryUrl).where(
                DiscoveryUrl.competitor_id == self.competitor.id, DiscoveryUrl.run_id == self.run.id,
                DiscoveryUrl.status == "queued").values(status="skipped", error=limit)).rowcount
            self._set_stats(limit_hit=limit, skipped_urls=self.run.stats.get("skipped_urls", 0) + skipped)
            return None
        return row

    def _enqueue(self, urls: list[str], depth: int, priority: int | None = None) -> None:
        """Queue pages for this run; known product pages only count as seen; dead templates are left out."""
        if not urls:
            return
        stats = self._template_stats()
        existing = {r.url: r for r in self.db.scalars(select(DiscoveryUrl).where(
            DiscoveryUrl.competitor_id == self.competitor.id, DiscoveryUrl.url.in_(urls)))}
        seen = set(self.run.seen_keys)
        for url in urls:
            tpl = template(url)
            row = existing.get(url)
            if row is not None:
                if row.run_id == self.run.id:
                    continue
                if row.kind == "product" and row.status == "done":
                    seen.add(url)  # read before: still listed, so not gone
                    continue
            if depth > 0 and crawl.dead(tpl, stats):
                self._stat("dead_links")
                continue
            rank = priority if priority is not None else crawl.priority(url, stats, self.settings.product_pattern)
            if row is None:
                self.db.add(DiscoveryUrl(competitor_id=self.competitor.id, run_id=self.run.id, url=url[:2048],
                                         template=tpl[:600], depth=depth, priority=rank, status="queued",
                                         kind="unknown", links_found=0, products_found=0))
            else:
                row.run_id, row.status, row.depth, row.priority, row.error = self.run.id, "queued", depth, rank, None
        self.run.seen_keys = sorted(seen)
        self.db.flush()

    def _crawl_page(self, url: str) -> None:
        row = self._row(url)
        response = self._fetch(url)
        html = response.text or ""
        links = crawl.page_links(html, response.url, self._domains(), self._drop(), self.settings.include_patterns,
                                 self.settings.exclude_patterns)
        if row.depth == 0 and not links and crawl.looks_like_js_app(html):
            self._set_stats(js_app=True)
            self._attempt(urlsplit(url).path or "/", "stránka bez odkazov a textu, obsah vykresľuje JavaScript")
        stats = self._template_stats()
        product_links = [link for link in links if crawl.product_like(link, stats, self.settings.product_pattern)]
        data, _ = self._parse_product(html, response.url)
        if data is not None and self._crawled_product(url, data, html):
            kind = "product"
        elif len(product_links) >= crawl.LISTING_MIN_PRODUCT_LINKS:
            kind = "listing"
        elif any(stats.get(template(link), {}).get("listings") for link in links):
            kind = "category"
        else:
            kind = "info"
        self._count_template(url, product=kind == "product", listing=kind == "listing")
        row.kind, row.status, row.http_status, row.error = kind, "done", response.status, None
        row.links_found, row.products_found, row.fetched_at = len(links), len(product_links), self.now
        self._stat("pages")
        if row.depth < self.settings.max_depth:
            follow = product_links if kind == "product" else links  # from a product page: other products only
            self._enqueue(follow, depth=row.depth + 1)

    def _crawled_product(self, url: str, data: Extracted, html: str) -> bool:
        """A product page found by the crawler: one catalogue item per page (no second request needed).
        The same title under another URL (same ISBN/EAN) is not added twice."""
        same = [i for i in self.db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.key != url, CatalogItem.price == data.price))
            if plain(i.title) == plain(data.title) and template(i.url) == template(url)]
        if len(same) + 1 >= BOILERPLATE_PAGES:  # the same page shown under many addresses: not a product
            self._drop_boilerplate_now(url, same)
            return False
        codes = {v for k, v in (data.identifiers or {}).items() if k in ("isbn", "ean") and v}
        if codes:
            twin = next((i for i in self.db.scalars(select(CatalogItem).where(
                CatalogItem.competitor_id == self.competitor.id, CatalogItem.key != url))
                if codes & {i.identifiers.get("isbn"), i.identifiers.get("ean")}), None)
            if twin is not None:
                self.run.seen_keys = sorted(set(self.run.seen_keys) | {twin.key})
                self._stat("duplicates")
                return True
        self._upsert(catalog.FoundItem(key=url, url=url, title=data.title, price=data.price, currency=data.currency,
                                       identifiers=data.identifiers or {}))
        item = self.db.scalar(select(CatalogItem).where(CatalogItem.competitor_id == self.competitor.id,
                                                        CatalogItem.key == url))
        self._apply_details(item, data, html)
        item.enriched_at, item.enrich_error = self.now, None
        if item.match_status == "unrelated" and item.match_method is None:
            item.match_status = "new"  # was not a product before (e.g. no price), now it is
        self.run.seen_keys = sorted(set(self.run.seen_keys) | {url})
        self._stat("products_read")
        self._set_stats(found=len(self.run.seen_keys))
        return True

    # ----- C: listing the source -----

    def _list_page(self, url: str) -> None:
        source = self.settings.source
        max_bytes = 5 * 1024 * 1024 if source.type in ("sitemap", "feed") else None
        response = self._fetch(url, max_bytes=max_bytes)
        domain = self.competitor.domain
        try:
            if source.type == "sitemap":
                result = catalog.read_sitemap(response.text or "", response.url, domain, source.pattern)
            elif source.type == "feed":
                result = catalog.read_feed((response.text or "").encode("utf-8"), response.url, domain,
                                           _currency(self.competitor))
            elif source.type == "adapter":
                adapter = catalog.adapter_by_name(source.adapter)
                result = adapter.read(response.text or "", response.url, domain)
            else:
                result = catalog.read_listing(response.text or "", response.url, domain, source.pattern)
        except ValueError as exc:
            raise FetchError(f"Zdroj sa nedá prečítať: {exc}", "permanent") from None
        self.run.queue = self.run.queue[1:]
        done = list(self.run.done_urls) + [url]
        self.run.done_urls = done
        queued = set(self.run.queue) | set(done)
        additions = [u for u in result.next_urls if u not in queued]
        if len(done) + len(self.run.queue) + len(additions) <= MAX_LIST_PAGES:
            self.run.queue = self.run.queue + additions
        self._stat("pages")
        seen = set(self.run.seen_keys)
        for found in result.items:
            if found.key in seen:
                continue
            seen.add(found.key)
            self._upsert(found)
        self.run.seen_keys = sorted(seen)
        self._set_stats(found=len(seen))

    def _upsert(self, found: "catalog.FoundItem") -> None:
        item = self.db.scalar(select(CatalogItem).where(CatalogItem.competitor_id == self.competitor.id,
                                                        CatalogItem.key == found.key))
        if item is None:
            if self.run.kind == "prices":
                return  # new products are added by the weekly discovery run
            item = CatalogItem(competitor_id=self.competitor.id, key=found.key, url=found.url, first_seen_at=self.now,
                               match_status="new", status="active", identifiers={}, hints={}, attributes={},
                               candidates=[], missing_runs=0, price_from_listing=False)
            self.db.add(item)
            self._stat("new")
        item.url = found.url
        if self.settings.source and item.found_by is None:
            item.found_by = self.settings.source.type
        if found.hints.get("category"):
            item.category = str(found.hints["category"])[:300]
        item.title = (found.title or item.title or "")[:500] or None
        if found.price is not None:
            item.price, item.currency = found.price, found.currency or item.currency
        item.price_from_listing = found.price_from_listing or item.price_from_listing
        if found.identifiers:
            item.identifiers = {**item.identifiers, **found.identifiers}
        if found.hints:
            item.hints = {**item.hints, **found.hints}
        item.last_seen_at, item.missing_runs, item.status = self.now, 0, "active"
        item.completeness = completeness(item)
        self.db.flush()
        if item.offer_id and found.price is not None and found.price_from_listing:
            self._record_listing_price(item)

    def _record_listing_price(self, item: CatalogItem) -> None:
        offer = self.db.get(CompetitorProduct, item.offer_id)
        if offer is None or offer.price_source != "listing" or offer.status != "active":
            return
        data = Extracted(price=item.price, currency=item.currency, title=item.title, source="listing")
        outcome = pricing.record_success(self.db, offer, data, self.now, default_vat_mode=self.config.prices_vat_mode)
        self.db.flush()
        alerts.evaluate_offer(self.db, offer, alerts.Event(outcome.change_types, outcome.previous_price))
        self._stat("prices_read")
        if outcome.changed and outcome.change_types != ["initial"]:
            self._stat("price_changes")

    def _reconcile(self) -> None:
        found = len(self.run.seen_keys)
        self._set_stats(found=found)
        previous = self.db.scalar(select(DiscoveryRun).where(
            DiscoveryRun.competitor_id == self.competitor.id, DiscoveryRun.kind == "discovery",
            DiscoveryRun.status == "succeeded", DiscoveryRun.id != self.run.id).order_by(DiscoveryRun.id.desc()))
        before = (previous.stats or {}).get("found") if previous else None
        if found == 0 and self.run.stats.get("js_app"):
            raise Stop("Web vykresľuje obsah až JavaScriptom v prehliadači (stránka bez odkazov a textu), preto ho "
                       "nemožno načítať ani prechádzaním odkazov. Produkty by bolo možné získať iba vykreslením v "
                       "prehliadači (etapa 5 plánu) alebo z iného zdroja (feed). Nič sa neoznačilo ako zmiznuté.",
                       status="failed")
        if found == 0:
            raise Stop("Zdroj nevrátil žiadne produkty. Nič sa neoznačilo ako zmiznuté; skontrolujte nastavenie "
                       "zdroja alebo či web nezmenil štruktúru.")
        if before and found < before * SUSPICIOUS_DROP:
            raise Stop(f"Zdroj vrátil výrazne menej produktov ({found} oproti {before} minule). Nič sa neoznačilo "
                       "ako zmiznuté; skontrolujte, či web nezmenil štruktúru.")
        if self.run.stats.get("limit_hit") or self.run.stats.get("failed_urls"):
            self._set_stats(previous_found=before, gone_check="vynechané: web sa neprešiel celý")
            return  # products not reached this time are not gone
        seen = set(self.run.seen_keys)
        for item in self.db.scalars(select(CatalogItem).where(CatalogItem.competitor_id == self.competitor.id,
                                                              CatalogItem.status == "active")):
            if item.key in seen:
                continue
            item.missing_runs += 1
            if item.missing_runs >= 2:
                item.status = "gone"
                self._stat("gone")
                offer = self.db.get(CompetitorProduct, item.offer_id) if item.offer_id else None
                if offer is not None and offer.status == "active" and self.settings.mode == "auto":
                    offer.status = "archived"
                    self._stat("archived")
        self._set_stats(previous_found=before)

    # ----- C: reading product pages of new items -----

    def _next_to_enrich(self) -> CatalogItem | None:
        """The next item whose page is to be read. Items of URL templates that gave no product after several
        pages (e.g. articles in a sitemap of the whole website) are skipped without a request."""
        stats = self._template_stats()
        current = self.run.stats.get("current")
        if current == "sitemap" and self.run.stats.get("reads_sitemap", 0) >= EARLY_SAMPLE \
                and not self.run.stats.get("products_sitemap") and len(self.run.stats.get("plan") or []) > 1:
            self._abandon_unread("sitemap", "sitemap nevedie na produkty (vzorka bez produktu)")
            return None
        for _ in range(50):
            batch = list(self.db.scalars(select(CatalogItem).where(
                CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
                CatalogItem.price_from_listing.is_(False), CatalogItem.enriched_at.is_(None),
                CatalogItem.match_status == "new").order_by(CatalogItem.id).limit(ENRICH_WINDOW)))
            if not batch:
                return None
            dead = [i for i in batch if crawl.dead(template(i.url), stats)]
            if dead:
                for item in dead:
                    self._enriched(item, error="Preskočené: stránky tohto typu (šablóna URL) nie sú produkty.",
                                   status="skipped")
                    self._stat("skipped_urls")
                continue
            # Page types that gave products first, unknown types next, the rest last: the budget goes to
            # products, not to the order of the source (a sitemap often starts with information pages).
            return min(batch, key=lambda i: (_enrich_rank(template(i.url), stats, self.settings.product_pattern, i.url),
                                             i.id))
        return None

    def _is_entry(self, url: str) -> bool:
        base = self.competitor.base_url.rstrip("/")
        entries = {u.rstrip("/") for u in (self.settings.source.urls if self.settings.source else [])}
        return url.rstrip("/") in entries | {base}

    def _abandon_unread(self, method: str, reason: str) -> None:
        skipped = 0
        for item in self.db.scalars(select(CatalogItem).where(
                CatalogItem.competitor_id == self.competitor.id, CatalogItem.found_by == method,
                CatalogItem.enriched_at.is_(None), CatalogItem.match_status == "new")):
            self._enriched(item, error=f"Preskočené: {reason}.", status="skipped")
            skipped += 1
        self._stat("skipped_urls", skipped)

    def _enrich(self, url: str) -> None:
        item = self._next_to_enrich()
        response = self._fetch(url)
        data, error = self._parse_product(response.text or "", response.url)
        self._count_template(url, product=data is not None)
        current = self.run.stats.get("current") or "?"
        self._stat(f"reads_{current}")
        if data is not None:
            self._stat(f"products_{current}")
        if data is None:
            self._enriched(item, error=error)
            return
        self._apply_details(item, data, response.text or "")
        self._enriched(item)
        self._dedupe_by_identifier(item)

    def _dedupe_by_identifier(self, item: CatalogItem) -> None:
        """Tabs of one product page ("…/obsah", "…/ukazky") show the same ISBN/EAN: one product."""
        if {v for k, v in (item.identifiers or {}).items() if k in ("isbn", "ean") and v}:
            self._stat("duplicates", _dedupe(self.db, self.competitor.id))

    def _parse_product(self, html: str, url: str) -> tuple[Extracted | None, str]:
        """Product details with the competitor's parser; when that fails, the automatic one (structured data,
        then page text). A page without a price or a title is not a product."""
        names = [self.config.parser_type] + (["auto"] if self.config.parser_type != "auto" else [])
        error = ""
        for name in names:
            config = self.config.parser_config if name == self.config.parser_type else {}
            try:
                data = get_parser(name).parse(PageContext(html=html, url=url, config=config))
            except ParseError as exc:
                error = str(exc)
                continue
            if data.price is not None and data.title:
                return data, ""
            error = "Stránka nemá cenu alebo názov (nejde o produkt alebo ho parser nepozná)."
        return None, error

    def _apply_details(self, item: CatalogItem, data: Extracted, html: str) -> None:
        item.title = (data.title or item.title or "")[:500] or None
        item.price, item.currency = data.price, data.currency
        item.identifiers = {**item.identifiers, **(data.identifiers or {})}
        item.parsed_by, item.fetched_at, item.fetch_status = (data.source or self.config.parser_type)[:32], \
            self.now, "ok"
        canonical = _canonical_link(html, item.url)
        if canonical:
            item.canonical_url = canonical
        item.completeness = completeness(item)

    def _enriched(self, item: CatalogItem, error: str | None = None, status: str | None = None) -> None:
        item.enriched_at, item.enrich_error = self.now, (error or None) and error[:500]
        self._stat("enriched")
        if error:
            item.match_status = "unrelated"
            item.fetch_status, item.fetched_at = status or "not_product", self.now
            self._stat("not_product" if (status or "not_product") == "not_product" else f"{status}_items")

    # ----- D: matching -----

    def _drop_boilerplate_now(self, url: str, same: list[CatalogItem]) -> None:
        for item in same:
            item.match_status, item.match_method, item.product_id = "unrelated", None, None
            item.fetch_status = "not_product"
            item.enrich_error = (f"Nie je produkt: rovnaký názov a cena na {len(same) + 1} stránkach tohto typu "
                                 "(napr. návod na objednávanie).")
        self._stat("boilerplate", len(same) + 1)
        _forget_template(self.db, self.competitor, self.settings, template(url))
        current = {k: dict(v) for k, v in (self.run.stats.get("templates") or {}).items()}
        current[template(url)] = {"pages": crawl.DEAD_AFTER_PAGES, "products": 0, "listings": 0}
        self._set_stats(templates=current)

    def _drop_boilerplate(self) -> int:
        """Duplicates and shared pages are not products (see clean_catalog); learned page types are updated."""
        result = clean_catalog(self.db, self.competitor, self.settings)
        if result["templates"]:
            current = {k: dict(v) for k, v in (self.run.stats.get("templates") or {}).items()}
            for tpl in result["templates"]:
                current[tpl] = {**current.get(tpl, {"listings": 0}), "products": 0,
                                "pages": max(current.get(tpl, {}).get("pages", 0), crawl.DEAD_AFTER_PAGES)}
            self._set_stats(templates=current)
        for key in ("boilerplate", "duplicates"):
            if result[key]:
                self._stat(key, result[key])
        return result["boilerplate"]

    def _match(self) -> None:
        self._drop_boilerplate()
        own = OwnCatalogue(self.db)
        existing = self._existing_offers()
        counts = {"matched": 0, "review": 0, "unrelated": 0}
        items = self.db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.match_status.in_(("new", "review", "unrelated", "matched")),
            CatalogItem.enrich_error.is_(None)))
        for item in items:
            if item.match_method in ("manual", "existing") or (item.match_status == "matched" and item.offer_id):
                continue
            if not item.price_from_listing and item.enriched_at is None:
                continue  # not read yet (budget): next run
            offer = existing.get(item.url) or existing.get(plain(item.title))
            if offer is not None:
                item.offer_id, item.product_id = offer.id, offer.product_id
                item.match_status, item.match_method = "matched", "existing"
                counts["matched"] += 1
                continue
            decision, profile = own.decide(item.title, item.hints, item.identifiers)
            item.attributes = {**profile.as_dict(), "note": decision.note}
            item.candidates = decision.candidates
            item.match_status, item.match_method = decision.status, decision.method
            item.product_id = decision.product_id
            counts[decision.status] += 1
        self._set_stats(**counts)

    def _existing_offers(self) -> dict[str, CompetitorProduct]:
        """Offers added before discovery (by URL and by their scraped title), so nothing is tracked twice."""
        found: dict[str, CompetitorProduct] = {}
        rows = self.db.execute(select(CompetitorProduct, CurrentPrice.title).outerjoin(
            CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id).where(
            CompetitorProduct.competitor_id == self.competitor.id))
        for offer, title in rows:
            found[offer.product_url] = offer
            if title:
                found.setdefault(plain(title), offer)
        return found

    # ----- tracking matched items (automatic mode) -----

    def _track(self) -> int:
        created = 0
        items = self.db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.match_status == "matched", CatalogItem.offer_id.is_(None),
            CatalogItem.product_id.is_not(None)))
        for item in items:
            offer = CompetitorProduct(
                competitor_id=self.competitor.id, product_url=item.url, variant="", product_id=item.product_id,
                matching_method=item.match_method if item.match_method in ("identifier", "attributes") else "manual",
                match_status="matched", match_score=Decimal("1"), status="active",
                price_source="listing" if item.price_from_listing else "page",
                external_product_id=item.key.split(":", 1)[1][:128] if item.key.startswith("polozka:") else None)
            self.db.add(offer)
            self.db.flush()
            item.offer_id = offer.id
            created += 1
            if item.price_from_listing and item.price is not None:
                self._record_listing_price(item)
        self._stat("tracked_new", created)
        return created

    # ----- finishing -----

    def _finish_discovery(self) -> None:
        stats = self.run.stats
        incomplete = []
        if stats.get("limit_hit"):
            incomplete.append(stats["limit_hit"])
        if stats.get("failed_urls"):
            incomplete.append(f"{stats['failed_urls']} stránok sa nepodarilo načítať")
        priced = self.db.scalar(select(func.count(CatalogItem.id)).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.price.is_not(None)))
        seen = set(self.run.seen_keys)
        final = quality_of([i for i in self.db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id)) if i.key in seen])
        if not final["sufficient"]:
            incomplete.append(f"nedostatočná kvalita: {final['reason']}")
        self._set_stats(quality=final)
        self._save_diagnosis(final)
        self._set_stats(priced=priced, incomplete=incomplete,
                        templates=_top_templates(stats.get("templates") or {}))
        self.run.status, self.run.finished_at = ("partial" if incomplete else "succeeded"), self.now
        self.settings.last_success_at, self.settings.last_error = self.now, None
        self.settings.temporary_pauses = 0
        self.settings.learned_templates = crawl.merged(self.settings.learned_templates, stats.get("templates") or {})
        would_track = self.db.scalar(select(func.count(CatalogItem.id)).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.match_status == "matched", CatalogItem.offer_id.is_(None)))
        self._set_stats(would_track=would_track)
        interval = timedelta(days=self.settings.interval_days)
        if self.settings.mode == "shadow":
            ok, why = self._quality()
            if ok and stats.get("limit_hit"):
                ok, why = False, f"Web sa neprešiel celý ({stats['limit_hit']})."
            self.settings.shadow_ok_runs = self.settings.shadow_ok_runs + 1 if ok else 0
            self._set_stats(quality_ok=ok, quality_note=why)
            if self.settings.shadow_ok_runs >= SHADOW_RUNS_TO_PROMOTE:
                self.settings.mode = "auto"
                self._set_stats(promoted=True)
                self._track()
            elif ok:
                interval = timedelta(hours=SHADOW_RETRY_HOURS)
        self.settings.next_run_at = self.now + interval
        self.settings.next_prices_at = self.now + timedelta(days=1)
        self._save()
        log.info("Discovery run %s finished: %s", self.run.id, {k: v for k, v in stats.items() if k != "detect"})

    def _save_diagnosis(self, final: dict[str, Any]) -> None:
        """Remember the methods that brought products (up to the first sufficient one) for the next runs."""
        methods = self.run.stats.get("methods") or {}
        plan = self.run.stats.get("plan") or []
        chosen: list[str] = []
        for name in (m for m in plan if m in methods):
            if methods[name]["found"]:
                chosen.append(name)
            if methods[name]["sufficient"]:
                break
        chosen = chosen or [m for m in plan if m in methods][-1:] or plan[:1]
        previous = self.settings.diagnosis or {}
        detected_now = bool(self.run.stats.get("detect")) or not previous.get("at")
        self.settings.diagnosis = {
            "at": self.now.isoformat() if detected_now else previous["at"], "chosen": chosen, "plan": plan,
            "sources": self.run.stats.get("sources") or previous.get("sources") or {},
            "methods": {**(previous.get("methods") or {}), **methods},
            "suggestions": [t for t in self.run.stats.get("suggestions") or previous.get("suggestions") or []
                            if not is_ignored(t, self.settings.ignored_domains)],
            "quality": final, "stale": not final["sufficient"],
        }

    def _quality(self) -> tuple[bool, str]:
        stats = self.run.stats
        seen = list(self.db.scalars(select(CatalogItem).where(CatalogItem.competitor_id == self.competitor.id,
                                                               CatalogItem.status == "active")))
        with_data = [i for i in seen if i.price_from_listing or (i.enriched_at and not i.enrich_error)]
        read = [i for i in seen if i.price_from_listing or i.enriched_at]
        pending = self.db.scalar(select(func.count(CatalogItem.id)).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.price_from_listing.is_(False), CatalogItem.enriched_at.is_(None)))
        if pending:
            return False, f"{pending} produktov ešte nebolo načítaných (rozpočet), pokračuje sa ďalší beh."
        priced = [i for i in with_data if i.price is not None]
        share = len(priced) / len(read) if read else 0
        if share < MIN_PRICED_SHARE and self.settings.source and self.settings.source.type != "sitemap":
            return False, f"Cenu má iba {share:.0%} produktov (potrebných {MIN_PRICED_SHARE:.0%})."
        before = stats.get("previous_found")
        if before and abs(stats.get("found", 0) - before) / before > MAX_COUNT_CHANGE:
            return False, (f"Počet produktov sa zmenil o viac ako {MAX_COUNT_CHANGE:.0%} "
                           f"({before} → {stats.get('found', 0)}).")
        return True, "Kvalita v poriadku."

    def _finish_prices(self) -> None:
        seen = set(self.run.seen_keys)
        offers = list(self.db.execute(select(CompetitorProduct, CatalogItem.key).join(
            CatalogItem, CatalogItem.offer_id == CompetitorProduct.id).where(
            CompetitorProduct.competitor_id == self.competitor.id, CompetitorProduct.price_source == "listing",
            CompetitorProduct.status == "active")))
        failures = 0
        for offer, key in offers:
            if key not in seen:
                pricing.record_failure(self.db, offer, self.competitor, "Produkt sa v zozname nenašiel.", self.now)
                failures += 1
        stats = self.run.stats
        self.db.add(ScrapeRun(competitor_id=self.competitor.id, trigger="scheduled", started_at=self.run.started_at,
                              finished_at=self.now, total_count=len(offers), success_count=len(offers) - failures,
                              failure_count=failures, price_change_count=stats.get("price_changes", 0),
                              status="succeeded" if not failures else "partial" if failures < len(offers) else "failed",
                              error_summary="Niektoré produkty sa v zozname nenašli." if failures else None))
        self.run.status, self.run.phase, self.run.finished_at = "succeeded", "done", self.now
        self.settings.next_prices_at = self.now + timedelta(days=1)
        self._save()


def _currency(competitor: Competitor) -> str | None:
    from app.schemas.products import MARKET_CURRENCY

    return MARKET_CURRENCY.get(competitor.market or competitor.country or "")


def _catalogue_links(html: str, page_url: str, domain: str) -> list[str]:
    """Links of the home page that look like the product catalogue (shop, katalog, učebnice, …)."""
    from bs4 import BeautifulSoup

    found: list[str] = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        try:
            url = normalize_url(urljoin(page_url, a["href"]))
        except InvalidUrl:
            continue
        if not catalog._belongs(url, domain) or url in found or url.rstrip("/") == page_url.rstrip("/"):
            continue
        if query_says_product(urlsplit(url).query):
            continue  # a product detail, not the catalogue
        image = a.find("img")
        label = " ".join(filter(None, [a.get_text(" ", strip=True), a.get("title"),
                                       image.get("alt") if image else None, image.get("title") if image else None]))
        parts = urlsplit(url)
        text = plain(parts.path + " " + parts.query.replace("=", " ").replace("&", " ") + " " + label)
        if any(word in text for word in CATALOG_WORDS):
            found.append(url)
    return found[:MAX_DETECT_CANDIDATES]


def completeness(item: CatalogItem) -> int:
    """0–100: title 30, price with currency 40, identifier 15, category 15."""
    score = 30 if item.title else 0
    score += 40 if item.price is not None and item.currency else 0
    score += 15 if (item.identifiers or {}).get("isbn") or (item.identifiers or {}).get("ean") else 0
    score += 15 if item.category or (item.hints or {}).get("grade") or (item.hints or {}).get("category") else 0
    return score


def _canonical_link(html: str, page_url: str) -> str | None:
    match = re.search(r"<link[^>]+rel=[\"']?canonical[\"']?[^>]*>", html[:200_000], re.I)
    href = re.search(r"href=[\"']?([^\"' >]+)", match.group(0), re.I) if match else None
    return safe_canonical(urljoin(page_url, href.group(1))) if href else None


def _top_templates(templates: dict[str, dict[str, int]], limit: int = 15) -> dict[str, dict[str, int]]:
    ranked = sorted(templates.items(), key=lambda kv: (-kv[1].get("pages", 0), kv[0]))
    return dict(ranked[:limit])


def quality_of(items: list[CatalogItem]) -> dict[str, Any]:
    """Quality of a method's result: products found, shares with price, title and a plausible price.

    Sufficient when something was found and at least 80 % of the products read have a price, 95 % a title
    and 90 % a plausible price (1–20 000; the same price for every product means a wrong selector).
    """
    read = [i for i in items if i.price_from_listing or i.fetch_status in ("ok", "no_price")]
    found = len([i for i in read if i.price is not None])
    if not read:
        return {"found": 0, "read": 0, "priced": 0.0, "titled": 0.0, "plausible": 0.0, "sufficient": False,
                "reason": "nenašiel sa žiadny produkt"}
    priced = found / len(read)
    titled = sum(1 for i in read if i.title) / len(read)
    prices = [i.price for i in read if i.price is not None]
    plausible = sum(1 for p in prices if PLAUSIBLE_PRICE[0] <= p <= PLAUSIBLE_PRICE[1]) / len(prices) if prices else 0
    if len(prices) >= FEW_PRODUCTS and len(set(prices)) == 1:
        plausible = 0.0
    reasons = []
    if priced < MIN_PRICED:
        reasons.append(f"cenu má iba {priced:.0%} produktov")
    if titled < MIN_TITLED:
        reasons.append(f"názov má iba {titled:.0%} produktov")
    if plausible < MIN_PLAUSIBLE:
        reasons.append("ceny nie sú vierohodné (mimo rozsahu alebo všetky rovnaké)")
    if found < FEW_PRODUCTS and not reasons:
        reasons_note = f"iba {found} produktov"
    else:
        reasons_note = None
    return {"found": found, "read": len(read), "priced": round(priced, 3), "titled": round(titled, 3),
            "plausible": round(plausible, 3), "sufficient": not reasons and found > 0,
            "reason": "; ".join(reasons) or reasons_note}


def diagnosis_valid(settings: DiscoverySettings, now: datetime) -> bool:
    d = settings.diagnosis or {}
    if not d.get("chosen") or d.get("stale") or not d.get("at"):
        return False
    return now - datetime.fromisoformat(d["at"]) < timedelta(days=DIAGNOSIS_DAYS)


def _describe_quality(result: dict[str, Any]) -> str:
    if not result["read"]:
        return "nenašiel žiadny produkt"
    text = (f"{result['found']} produktov s cenou z {result['read']} načítaných "
            f"({'dostatočné' if result['sufficient'] else 'nedostatočné'})")
    return f"{text}: {result['reason']}" if result.get("reason") else text


def _shop_domain_hint(html: str, page_url: str, domains: set[str]) -> tuple[str, int] | None:
    """Another domain the home page links to many times (e.g. spn.cz → naseucebnice.cz): maybe the shop."""
    from bs4 import BeautifulSoup

    counts: dict[str, int] = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        host = (urlsplit(urljoin(page_url, a["href"])).hostname or "").lower().removeprefix("www.")
        if host and "." in host and not crawl.belongs(f"https://{host}/", domains):
            counts[host] = counts.get(host, 0) + 1
    if not counts:
        return None
    host, count = max(counts.items(), key=lambda kv: kv[1])
    social = ("facebook.", "youtube.", "instagram.", "twitter.", "x.com", "linkedin.", "google.", "mapy.")
    return (host, count) if count >= SHOP_HINT_LINKS and not host.startswith(social) else None


def _enrich_rank(tpl: str, stats: dict[str, dict[str, int]], pattern: str | None, url: str) -> int:
    if pattern:
        return 0 if crawl.product_like(url, stats, pattern) else 2
    known = stats.get(tpl)
    if known and known["products"]:
        return 0
    if not known or not known["pages"]:
        return 1 + (0 if urlsplit(url).path.count("/") >= 2 else 1)  # deeper paths are more often products
    return 3


# ---------- catalogue clean-up (runs and the worker's housekeeping) ----------

def _not_product(item: CatalogItem, reason: str) -> None:
    item.match_status, item.match_method, item.product_id = "unrelated", None, None
    item.fetch_status, item.enrich_error = "not_product", reason[:500]


def _dedupe(db: Session, competitor_id: int) -> int:
    """Pages with the same ISBN/EAN are one product: the shortest address stays, the others are duplicates
    (tabs like "…/obsah", "…/ukazky", "…/ke_stazeni" of a product page)."""
    groups: dict[str, list[CatalogItem]] = {}
    for item in db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == competitor_id, CatalogItem.status == "active",
            CatalogItem.fetch_status == "ok", CatalogItem.price_from_listing.is_(False))):
        for code in {v for k, v in (item.identifiers or {}).items() if k in ("isbn", "ean") and v}:
            groups.setdefault(code, []).append(item)
    marked = 0
    for items in groups.values():
        if len(items) < 2:
            continue
        keep = next((i for i in items if i.match_method == "manual"), None) or \
            min(items, key=lambda i: (len(urlsplit(i.url).path), i.url))
        for other in items:
            if other is not keep and other.match_method != "manual" and other.fetch_status == "ok":
                _not_product(other, f"Duplicitná stránka produktu {keep.url} (rovnaké ISBN/EAN).")
                marked += 1
    return marked


def _forget_template(db: Session, competitor: Competitor, settings: DiscoverySettings, tpl: str) -> None:
    """A page type believed to be products is not: no product count, so it is not followed any more."""
    learned = {k: dict(v) for k, v in settings.learned_templates.items()}
    learned[tpl] = {**learned.get(tpl, {"listings": 0}), "products": 0,
                    "pages": max(learned.get(tpl, {}).get("pages", 0), crawl.DEAD_AFTER_PAGES)}
    settings.learned_templates = learned
    db.execute(update(DiscoveryUrl).where(
        DiscoveryUrl.competitor_id == competitor.id, DiscoveryUrl.template == tpl[:600],
        DiscoveryUrl.kind == "product").values(kind="info"))


def clean_catalog(db: Session, competitor: Competitor, settings: DiscoverySettings) -> dict[str, Any]:
    """Wrong products in a competitor's catalogue: duplicates of one product (same ISBN/EAN) and pages that
    repeat one title and price on several addresses of one page type (a "how to order" page shown for every
    product). Decisions made by a person are kept. Does not commit."""
    duplicates = _dedupe(db, competitor.id)
    groups: dict[tuple[str, str, Decimal | None], list[CatalogItem]] = {}
    for item in db.scalars(select(CatalogItem).where(
            CatalogItem.competitor_id == competitor.id, CatalogItem.status == "active",
            CatalogItem.fetch_status == "ok", CatalogItem.price_from_listing.is_(False),
            CatalogItem.title.is_not(None), CatalogItem.match_method.is_distinct_from("manual"))):
        groups.setdefault((template(item.url), plain(item.title), item.price), []).append(item)
    boilerplate, templates = 0, []
    for (tpl, _, _), items in groups.items():
        if len(items) < BOILERPLATE_PAGES:
            continue
        for item in items:
            _not_product(item, f"Nie je produkt: rovnaký názov a cena na {len(items)} stránkach tohto typu "
                               "(napr. návod na objednávanie).")
            boilerplate += 1
        _forget_template(db, competitor, settings, tpl)
        templates.append(tpl)
    return {"duplicates": duplicates, "boilerplate": boilerplate, "templates": templates}


def clean_all(db: Session) -> dict[str, int]:
    """Worker housekeeping: clean the catalogues of all competitors (also paused ones). Commits."""
    total = {"duplicates": 0, "boilerplate": 0}
    for competitor in db.scalars(select(Competitor).where(Competitor.status != "archived")):
        if competitor.scrape_config is None:
            continue
        settings = load(competitor.scrape_config)
        result = clean_catalog(db, competitor, settings)
        if result["templates"]:
            save(competitor.scrape_config, settings)
        for key in total:
            total[key] += result[key]
    db.commit()
    return total
