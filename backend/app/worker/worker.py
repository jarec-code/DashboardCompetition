"""Job worker: takes jobs from the PostgreSQL queue and processes them one at a time. It also runs the
scheduler tick (SCHEDULER_INTERVAL_SECONDS) and records a heartbeat shown on the Updates page.

Runs either as its own process (`python -m app.worker`, for a separate Railway service) or as a
background thread of the web process (EMBEDDED_WORKER=true, the default on the Free plan).
State lives in the database only, so a restart loses nothing: abandoned jobs are recovered.
"""
import logging
import signal
import threading
import time
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.scraping.fetcher import Fetcher
from app.services import alerts, catalog_sync, housekeeping, scheduling
from app.worker import queue, scheduler
from app.worker.processor import Processor

log = logging.getLogger("worker")
RECOVERY_INTERVAL_SECONDS = 60
ALERTS_INTERVAL_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 6 * 3600
CATALOG_CHECK_SECONDS = 30  # how often to look whether the own catalogue feed is due (or was requested)


class Worker:
    def __init__(self, settings: Settings | None = None, fetcher: Fetcher | None = None):
        self.settings = settings or get_settings()
        self.fetcher = fetcher or Fetcher(self.settings)
        self.processor = Processor(self.fetcher)
        self._last_recovery = 0.0
        self._last_tick = 0.0
        self._last_alerts = 0.0
        self._last_cleanup = 0.0
        self._last_catalog = 0.0

    def run_once(self) -> int:
        """Housekeeping when due, then process at most one job. Returns the number of processed jobs."""
        with SessionLocal() as db:
            if time.monotonic() - self._last_recovery > RECOVERY_INTERVAL_SECONDS:
                self._last_recovery = time.monotonic()
                if recovered := queue.recover_abandoned(db, self.settings.job_lease_seconds):
                    log.warning("Recovered %d abandoned job(s)", recovered)
                scheduling.set_heartbeat(db, "worker", datetime.now(UTC))
                db.commit()
            if time.monotonic() - self._last_cleanup > CLEANUP_INTERVAL_SECONDS:
                self._last_cleanup = time.monotonic()
                removed = housekeeping.cleanup(db)
                db.commit()
                if any(removed.values()):
                    log.info("Removed old diagnostic data: %s", removed)
            if time.monotonic() - self._last_alerts > ALERTS_INTERVAL_SECONDS:
                self._last_alerts = time.monotonic()
                alerts.evaluate_all(db)  # e.g. prices that became stale without any check
                db.commit()
            if time.monotonic() - self._last_catalog > CATALOG_CHECK_SECONDS:
                self._last_catalog = time.monotonic()
                catalog_sync.run_if_due(db, self.fetcher)
            if self.settings.scheduler_in_worker and \
                    time.monotonic() - self._last_tick > self.settings.scheduler_interval_seconds:
                self._last_tick = time.monotonic()
                scheduler.tick(db)
            jobs = queue.claim(db, limit=1)
            for job in jobs:
                self.processor.process(db, job)
                db.commit()
                log.info("Job %s (%s) finished: %s", job.id, job.kind, job.status)
            return len(jobs)

    def run_until(self, stop: threading.Event) -> None:
        log.info("Worker started")
        while not stop.is_set():
            try:
                processed = self.run_once()
            except Exception:  # database down etc.: keep the worker alive and try again later
                log.exception("Worker iteration failed")
                processed = 0
            if not processed:
                stop.wait(self.settings.worker_poll_seconds)
        self.fetcher.close()
        log.info("Worker stopped")


def start_embedded(settings: Settings | None = None) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(target=Worker(settings).run_until, args=(stop,), name="embedded-worker", daemon=True)
    thread.start()
    return thread, stop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Worker().run_until(stop)


if __name__ == "__main__":
    main()
