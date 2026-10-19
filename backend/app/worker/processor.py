"""Processing of one scrape job: fetch, parse, record, match. Never raises; outcomes go to the job row."""
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Competitor, CompetitorProduct, CurrentPrice, ScrapeJob, ScrapeRun
from app.scraping.fetcher import Fetcher, FetchError
from app.scraping.parsers.base import PageContext, ParseError
from app.scraping.parsers.registry import get_parser
from app.services import alerts, matching, pricing
from app.worker import queue

log = logging.getLogger("worker")
RETRY_BASE_SECONDS = 60       # 1st retry after ~1 min, then 2, 4, 8 … minutes
RETRY_MAX_SECONDS = 3600


class Skip(Exception):
    """The job must not run (e.g. the competitor was paused); it is cancelled, not failed."""


class Processor:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self._last_request: dict[str, float] = {}   # domain -> monotonic time of the last request
        self._blocked_until: dict[str, float] = {}  # domain -> monotonic time (HTTP 429 Retry-After)

    # ---------- politeness ----------

    def _wait_turn(self, domain: str, min_interval: float) -> None:
        """Requests to one domain go one after another, at least `min_interval` seconds apart."""
        blocked = self._blocked_until.get(domain, 0) - time.monotonic()
        if blocked > 0:
            raise FetchError(f"Web požiadal o pauzu (HTTP 429), ďalšia požiadavka najskôr o {int(blocked)} s.",
                             "temporary", 429, blocked)
        wait = self._last_request.get(domain, 0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request[domain] = time.monotonic()

    # ---------- main entry ----------

    def process(self, db: Session, job: ScrapeJob) -> None:
        competitor = db.get(Competitor, job.competitor_id)
        offer = db.get(CompetitorProduct, job.competitor_product_id) if job.competitor_product_id else None
        try:
            self._check_allowed(job, competitor, offer)
            if job.kind == "manual" and job.run_id is None:
                run = ScrapeRun(competitor_id=competitor.id, trigger="manual", status="running", total_count=1)
                db.add(run)
                db.flush()
                job.run_id = run.id
            result = self._scrape(db, job, competitor, offer)
            queue.finish(db, job, "succeeded", result=result)
            queue.account_run(db, job, success=True, changed=bool(result.get("change_types")))
            if job.kind == "test":
                self._test_status(competitor, "succeeded")
        except Skip as exc:
            queue.finish(db, job, "cancelled", error=str(exc), error_kind="permanent")
            queue.account_run(db, job, success=None)
        except (FetchError, ParseError) as exc:
            kind = exc.kind if isinstance(exc, FetchError) else "permanent"
            retry_after = exc.retry_after if isinstance(exc, FetchError) else None
            if isinstance(exc, FetchError) and exc.status == 429 and retry_after:
                domain = urlsplit(job.url or offer.product_url).hostname or ""
                self._blocked_until[domain] = time.monotonic() + retry_after
            self._failed(db, job, competitor, offer, str(exc), kind, retry_after)
        except Exception:
            log.exception("Unexpected error in job %s", job.id)
            message = "Neočakávaná chyba pri spracovaní (podrobnosti sú v logu servera)."
            self._failed(db, job, competitor, offer, message, "temporary", None)

    def _failed(self, db: Session, job: ScrapeJob, competitor: Competitor | None, offer: CompetitorProduct | None,
                error: str, kind: str, retry_after: float | None) -> None:
        """Retry temporary errors of scheduled checks with exponential backoff; otherwise fail the job."""
        now = datetime.now(UTC)
        max_retries = competitor.scrape_config.max_retries if competitor else 0
        will_retry = job.kind == "scheduled" and kind == "temporary" and job.attempt_count <= max_retries
        if offer is not None and job.kind != "test":
            pricing.record_failure(db, offer, competitor, error, now, final=not will_retry)
            db.flush()
            alerts.evaluate_offer(db, offer)
        if will_retry:
            delay = min(RETRY_BASE_SECONDS * 2 ** (job.attempt_count - 1), RETRY_MAX_SECONDS)
            delay = max(delay * random.uniform(0.8, 1.2), retry_after or 0)
            queue.retry_later(db, job, now + timedelta(seconds=delay), error)
            return
        queue.finish(db, job, "failed", result=self._partial(job), error=error, error_kind=kind)
        queue.account_run(db, job, success=False, error=error)
        if job.kind == "test" and competitor is not None:
            self._test_status(competitor, "failed")

    # ---------- steps ----------

    @staticmethod
    def _check_allowed(job: ScrapeJob, competitor: Competitor | None, offer: CompetitorProduct | None) -> None:
        if competitor is None:
            raise Skip("Konkurent neexistuje.")
        if competitor.status == "archived":
            raise Skip("Konkurent je archivovaný, kontroly sa nevykonávajú.")
        if job.kind != "test" and offer is None:
            raise Skip("Ponuka neexistuje.")
        if job.kind == "scheduled":
            if competitor.status != "active":
                raise Skip("Konkurent je pozastavený, automatická kontrola sa preskočila.")
            if not competitor.scrape_config.enabled:
                raise Skip("Scraping je pre tohto konkurenta vypnutý.")
            if offer.status != "active":
                raise Skip("Ponuka nie je aktívna, automatická kontrola sa preskočila.")

    def _scrape(self, db: Session, job: ScrapeJob, competitor: Competitor, offer: CompetitorProduct | None) -> dict:
        config = competitor.scrape_config
        spec = get_parser(config.parser_type)
        url = job.url or offer.product_url
        variant = (job.result or {}).get("variant", "") if job.kind == "test" else offer.variant
        domain = urlsplit(url).hostname or ""

        if not self.fetcher.robots_cached(url):
            self._wait_turn(domain, config.min_interval_seconds)
        if not self.fetcher.robots_allowed(url):
            raise FetchError("Súbor robots.txt webu zakazuje načítanie tejto stránky.", "permanent")
        current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id)) \
            if offer is not None else None
        # Conditional requests (ETag / Last-Modified) only for scheduled checks; manual ones always reload.
        conditional = job.kind == "scheduled" and current is not None and current.last_success_at is not None
        self._wait_turn(domain, config.min_interval_seconds)
        fetched = self.fetcher.fetch(url, timeout=config.timeout_seconds,
                                     etag=current.etag if conditional else None,
                                     last_modified=current.last_modified if conditional else None)
        observed_at = datetime.now(UTC)
        result = {"url": url, "final_url": fetched.url, "http_status": fetched.status, "elapsed_ms": fetched.elapsed_ms,
                  "parser": spec.code_version, "parser_config_version": config.parser_version, "variant": variant}

        if fetched.not_modified:
            pricing.record_not_modified(db, offer, observed_at)
            db.flush()
            alerts.evaluate_offer(db, offer)
            return {**result, "not_modified": True, "change_types": []}

        data = spec.parse(PageContext(html=fetched.text or "", url=fetched.url, config=config.parser_config,
                                      variant=variant))
        if data.price is None:
            raise ParseError("Parser nevrátil cenu.")
        result["extracted"] = data.as_dict()
        if job.kind == "test":
            return result
        outcome = pricing.record_success(db, offer, data, observed_at, default_vat_mode=config.prices_vat_mode,
                                         etag=fetched.etag, last_modified=fetched.last_modified)
        db.flush()
        matching.auto_match(db, offer)
        db.flush()
        alerts.evaluate_offer(db, offer, alerts.Event(outcome.change_types, outcome.previous_price))
        return {**result, "change_types": outcome.change_types}

    @staticmethod
    def _partial(job: ScrapeJob) -> dict | None:
        return {"variant": job.result.get("variant", "")} if job.kind == "test" and job.result else None

    @staticmethod
    def _test_status(competitor: Competitor, status: str) -> None:
        competitor.scrape_config.last_test_status = status
        competitor.scrape_config.last_test_at = datetime.now(UTC)
