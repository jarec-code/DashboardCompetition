"""Job worker: takes jobs from the PostgreSQL queue and processes them one at a time.

Runs either as its own process (`python -m app.worker`, for a separate Railway service) or as a
background thread of the web process (EMBEDDED_WORKER=true, the default on the Free plan).
State lives in the database only, so a restart loses nothing: abandoned jobs are recovered.
"""
import logging
import signal
import threading
import time

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.scraping.fetcher import Fetcher
from app.worker import queue
from app.worker.processor import Processor

log = logging.getLogger("worker")
RECOVERY_INTERVAL_SECONDS = 60


class Worker:
    def __init__(self, settings: Settings | None = None, fetcher: Fetcher | None = None):
        self.settings = settings or get_settings()
        self.fetcher = fetcher or Fetcher(self.settings)
        self.processor = Processor(self.fetcher)
        self._last_recovery = 0.0

    def run_once(self) -> int:
        """Process at most one job. Returns how many jobs were processed (0 or 1)."""
        with SessionLocal() as db:
            if time.monotonic() - self._last_recovery > RECOVERY_INTERVAL_SECONDS:
                self._last_recovery = time.monotonic()
                if recovered := queue.recover_abandoned(db, self.settings.job_lease_seconds):
                    log.warning("Recovered %d abandoned job(s)", recovered)
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
