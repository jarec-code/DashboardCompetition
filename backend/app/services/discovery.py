"""Automatic discovery of competitor products (docs/AUTO_DISCOVERY_PLAN.md).

The worker calls `step()` on every loop. One call makes at most one request to a competitor's website, and
only when that is allowed: discovery is on, the website's terms were confirmed, the night window is open
(unless an administrator asked for a run now), the nightly request budget is not used up and enough time
has passed since the last request to that domain (at least `spacing_seconds`, robots.txt Crawl-delay and
the competitor's own minimal interval). A run is stored in the database, so it continues after a restart
or on the next night.

Discovery run (weekly):  detect source (once) -> list pages -> compare with the catalogue (new / gone)
                         -> read product pages of new items -> match -> track (automatic mode only)
Prices run (daily):      list pages only, to update offers whose price is read from the listing.

Safety: robots.txt is respected; HTTP 403/429 or a bot challenge stops discovery of the competitor for
7 days, as do 3 failed requests in a row; when a source suddenly returns far fewer products, nothing is
marked as gone and the run stops with a warning.
"""
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CatalogItem, Competitor, CompetitorProduct, CurrentPrice, DiscoveryRun, ScrapeConfig, ScrapeRun
from app.scraping import catalog
from app.scraping.discover import discover_links
from app.scraping.fetcher import Fetcher, FetchError
from app.scraping.parsers.base import Extracted, PageContext, ParseError
from app.scraping.parsers.registry import get_parser
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
MIN_LISTING_LINKS = 12           # a page with at least this many product links is a product listing
MAX_LIST_PAGES = 300
MAX_DETECT_CANDIDATES = 3
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
    type: Literal["sitemap", "feed", "listing", "adapter"]
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
    # state, kept by the runner
    run_requested: bool = False
    next_run_at: datetime | None = None
    next_prices_at: datetime | None = None
    last_run_id: int | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    paused_until: datetime | None = None
    shadow_ok_runs: int = 0
    budget_date: str | None = None
    budget_used: int = 0


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
        run = DiscoveryRun(competitor_id=competitor.id, kind=kind, mode=settings.mode, status="running",
                           phase="list" if settings.source else "detect", stats={"manual": manual},
                           source_type=settings.source.type if settings.source else None,
                           queue=list(settings.source.urls) if settings.source else [], started_at=now)
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

    def __init__(self, message: str, pause: bool = False, status: str = "stopped"):
        super().__init__(message)
        self.pause, self.status = pause, status


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
        delay = self.fetcher.crawl_delay(url) if self.fetcher.robots_cached(url) else None
        return max(self.settings.spacing_seconds, self.config.min_interval_seconds, delay or 0)

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
            self._stop(str(exc), exc.pause, exc.status)
            return True

    def _next_url(self) -> str | None:
        phase = self.run.phase
        if phase == "detect":
            return self._detect_url()
        if phase == "list":
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
            elif self.run.phase == "list":
                self._list_page(url)
            else:
                self._enrich(url)
            self.run.errors_in_row = 0
        except FetchError as exc:
            self._request_failed(url, exc)

    def _without_request(self) -> None:
        if self.run.phase == "list":
            if self.run.kind == "prices":
                self._finish_prices()
            else:
                self._reconcile()
                self.run.phase = "enrich"
        elif self.run.phase == "enrich":
            self.run.phase = "done"
            self._match()
            if self.settings.mode == "auto":
                self._track()
            self._finish_discovery()
        elif self.run.phase == "detect":
            raise Stop("Zdroj produktov sa nepodarilo nájsť automaticky. V nastaveniach objavovania zadajte adresu "
                       "sitemap, feedu alebo stránky so zoznamom produktov.", status="failed")

    def _request_failed(self, url: str, exc: FetchError) -> None:
        blocked = exc.status in (403, 429) or "botom" in str(exc)
        if blocked:
            if exc.status == 429 and exc.retry_after:
                self.politeness.blocked_until[urlsplit(url).hostname or ""] = time.monotonic() + exc.retry_after
            raise Stop(f"Web obmedzil prístup ({exc}). Objavovanie sa pozastavuje na {PAUSE_DAYS} dní.", pause=True)
        if self.run.phase == "enrich" and exc.status in (404, 410):
            item = self._next_to_enrich()
            if item is not None:
                self._enriched(item, error=str(exc))
            return
        self.run.errors_in_row += 1
        self._set_stats(last_error=str(exc)[:300])
        if self.run.errors_in_row >= ERRORS_TO_STOP:
            raise Stop(f"{ERRORS_TO_STOP} neúspešné požiadavky za sebou ({exc}). Objavovanie sa pozastavuje na "
                       f"{PAUSE_DAYS} dní.", pause=True)
        if self.run.phase == "list" and self.run.queue and exc.kind == "permanent":
            self.run.queue = self.run.queue[1:]  # a broken page of the source: skip it

    def _stop(self, message: str, pause: bool, status: str) -> None:
        self.run.status, self.run.error, self.run.finished_at = status, message[:2000], self.now
        self.settings.last_error = message[:500]
        if pause:
            self.settings.paused_until = self.now + timedelta(days=PAUSE_DAYS)
            self.settings.shadow_ok_runs = 0
        if self.run.kind == "discovery":
            self.settings.next_run_at = self.now + timedelta(days=self.settings.interval_days)
        else:
            self.settings.next_prices_at = self.now + timedelta(days=1)
        self._save()
        log.warning("Discovery run %s stopped: %s", self.run.id, message)

    # ----- B: detecting the source -----

    def _detect_state(self) -> dict:
        return dict(self.run.stats.get("detect") or {"step": 0})

    def _detect_url(self) -> str | None:
        state = self._detect_state()
        base = self.competitor.base_url
        adapter = catalog.adapter_for(self.competitor.domain)
        if adapter is not None and state["step"] == 0:
            self._use_source(SourceSpec("adapter", [urljoin(base, u) for u in adapter.start_urls],
                                        adapter=adapter.name))
            return self._next_url()
        # step 0 reads the sitemaps announced in robots.txt (already loaded, no request of its own)
        steps = [base, urljoin(base, "/sitemap.xml"), base] + list(state.get("candidates", []))
        index = state["step"]
        return steps[index] if index < len(steps) else None

    def _use_source(self, spec: SourceSpec) -> None:
        self.settings.source = DiscoverySource(type=spec.type, urls=spec.urls, pattern=spec.pattern,
                                               adapter=spec.adapter, detected=True)
        self.run.source_type, self.run.phase, self.run.queue = spec.type, "list", list(spec.urls)
        self._set_stats(detected=spec.type)
        self._save()

    def _detect_with(self, url: str) -> None:
        state = self._detect_state()
        step = state["step"]
        state["step"] = step + 1
        self._set_stats(detect=state)
        domain = self.competitor.domain
        if step == 0:  # sitemaps announced in robots.txt
            sitemaps = [s for s in self.fetcher.sitemaps(self.competitor.base_url) if catalog._belongs(s, domain)]
            if sitemaps:
                self._use_source(SourceSpec("sitemap", sitemaps[:5]))
            return
        try:
            response = self._fetch(url, max_bytes=5 * 1024 * 1024)
        except FetchError as exc:
            if exc.status in (404, 410):
                return  # this candidate does not exist; try the next one
            raise
        text = response.text or ""
        if step == 1:  # /sitemap.xml
            try:
                result = catalog.read_sitemap(text, url, domain)
            except ValueError:
                return
            if result.items or result.next_urls:
                self._use_source(SourceSpec("sitemap", [response.url]))
            return
        links = [c for c in discover_links(text, response.url, domain, set()) if c["score"] >= 2]
        if len(links) >= MIN_LISTING_LINKS:
            self._use_source(SourceSpec("listing", [response.url]))
            return
        if step == 2:  # the home page: remember links that look like the catalogue
            state["candidates"] = _catalogue_links(text, response.url, domain)
            self._set_stats(detect=state)

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
        item.title = (found.title or item.title or "")[:500] or None
        if found.price is not None:
            item.price, item.currency = found.price, found.currency or item.currency
        item.price_from_listing = found.price_from_listing or item.price_from_listing
        if found.identifiers:
            item.identifiers = {**item.identifiers, **found.identifiers}
        if found.hints:
            item.hints = {**item.hints, **found.hints}
        item.last_seen_at, item.missing_runs, item.status = self.now, 0, "active"
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
        previous = self.db.scalar(select(DiscoveryRun).where(
            DiscoveryRun.competitor_id == self.competitor.id, DiscoveryRun.kind == "discovery",
            DiscoveryRun.status == "succeeded", DiscoveryRun.id != self.run.id).order_by(DiscoveryRun.id.desc()))
        before = (previous.stats or {}).get("found") if previous else None
        if found == 0:
            raise Stop("Zdroj nevrátil žiadne produkty. Nič sa neoznačilo ako zmiznuté; skontrolujte nastavenie "
                       "zdroja alebo či web nezmenil štruktúru.")
        if before and found < before * SUSPICIOUS_DROP:
            raise Stop(f"Zdroj vrátil výrazne menej produktov ({found} oproti {before} minule). Nič sa neoznačilo "
                       "ako zmiznuté; skontrolujte, či web nezmenil štruktúru.")
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
        return self.db.scalar(select(CatalogItem).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.price_from_listing.is_(False), CatalogItem.enriched_at.is_(None),
            CatalogItem.match_status == "new").order_by(CatalogItem.id))

    def _enrich(self, url: str) -> None:
        item = self._next_to_enrich()
        response = self._fetch(url)
        spec = get_parser(self.config.parser_type)
        try:
            data = spec.parse(PageContext(html=response.text or "", url=response.url, config=self.config.parser_config))
        except ParseError as exc:
            self._enriched(item, error=str(exc))
            return
        if data.price is None:
            self._enriched(item, error="Stránka nemá cenu (nejde o produkt alebo ho parser nepozná).")
            return
        item.title = (data.title or item.title or "")[:500] or None
        item.price, item.currency = data.price, data.currency
        item.identifiers = {**item.identifiers, **(data.identifiers or {})}
        self._enriched(item)

    def _enriched(self, item: CatalogItem, error: str | None = None) -> None:
        item.enriched_at, item.enrich_error = self.now, (error or None) and error[:500]
        self._stat("enriched")
        if error:
            item.match_status = "unrelated"
            self._stat("not_product")

    # ----- D: matching -----

    def _match(self) -> None:
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
        self.run.status, self.run.finished_at = "succeeded", self.now
        self.settings.last_success_at, self.settings.last_error = self.now, None
        would_track = self.db.scalar(select(func.count(CatalogItem.id)).where(
            CatalogItem.competitor_id == self.competitor.id, CatalogItem.status == "active",
            CatalogItem.match_status == "matched", CatalogItem.offer_id.is_(None)))
        self._set_stats(would_track=would_track)
        interval = timedelta(days=self.settings.interval_days)
        if self.settings.mode == "shadow":
            ok, why = self._quality()
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
            return False, f"Počet produktov sa zmenil o viac ako {MAX_COUNT_CHANGE:.0%} ({before} → {stats['found']})."
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
        text = plain(urlsplit(url).path + " " + a.get_text(" ", strip=True))
        if any(word in text for word in CATALOG_WORDS):
            found.append(url)
    return found[:MAX_DETECT_CANDIDATES]

