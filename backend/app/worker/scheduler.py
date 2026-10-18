"""Scheduler: turns offers whose update interval has passed into scrape jobs.

One tick (every SCHEDULER_INTERVAL_SECONDS, run by the worker loop, or `python -m app.worker.scheduler`
once, e.g. from a cron service):
  1. takes a transaction-level PostgreSQL advisory lock, so parallel schedulers never plan twice,
  2. selects active offers of active competitors (scraping enabled, product tracking not paused)
     with next_check_at in the past or never checked, without an open job,
  3. creates one scrape run per competitor and its jobs, spaced by the competitor's minimal interval,
  4. moves next_check_at forward by the offer's interval (with a small random jitter).
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Competitor, CompetitorProduct, Product, ScrapeConfig, ScrapeJob, ScrapeRun
from app.services import scheduling
from app.worker import queue

log = logging.getLogger("scheduler")
LOCK_ID = 727_002


@dataclass
class TickResult:
    status: str  # planned, disabled, outside_window, locked
    jobs: int = 0
    runs: int = 0


def due_offers_query(now: datetime):
    open_job = exists().where(ScrapeJob.competitor_product_id == CompetitorProduct.id,
                              ScrapeJob.status.in_(("pending", "running")))
    return (select(CompetitorProduct)
            .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
            .join(ScrapeConfig, ScrapeConfig.competitor_id == Competitor.id)
            .outerjoin(Product, Product.id == CompetitorProduct.product_id)
            .where(CompetitorProduct.status == "active", Competitor.status == "active", ScrapeConfig.enabled.is_(True),
                   or_(Product.id.is_(None), Product.tracking_status == "active"),
                   or_(CompetitorProduct.next_check_at.is_(None), CompetitorProduct.next_check_at <= now),
                   ~open_job))


def tick(db: Session, now: datetime | None = None) -> TickResult:
    """Plan due checks. Commits."""
    now = now or datetime.now(UTC)
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK_ID))):
        db.rollback()
        return TickResult("locked")
    scheduling.set_heartbeat(db, "scheduler", now)
    settings = scheduling.load_settings(db)
    if not settings.scheduler_enabled:
        db.commit()
        return TickResult("disabled")
    if not scheduling.in_window(settings, now):
        db.commit()
        return TickResult("outside_window")

    offers = list(db.scalars(due_offers_query(now)
                             .order_by(CompetitorProduct.next_check_at.asc().nulls_first(), CompetitorProduct.id)
                             .limit(settings.batch_size).with_for_update(of=CompetitorProduct, skip_locked=True)))
    by_competitor: dict[int, list[CompetitorProduct]] = defaultdict(list)
    for offer in offers:
        by_competitor[offer.competitor_id].append(offer)

    for competitor_id, group in by_competitor.items():
        competitor = db.get(Competitor, competitor_id)
        run = ScrapeRun(competitor_id=competitor_id, trigger="scheduled", status="running", total_count=len(group),
                        started_at=now)
        db.add(run)
        db.flush()
        spacing = competitor.scrape_config.min_interval_seconds
        for index, offer in enumerate(group):
            queue.enqueue(db, competitor_id, "scheduled", offer_id=offer.id, run_id=run.id,
                          scheduled_at=now + timedelta(seconds=index * spacing))
            offer.next_check_at = scheduling.next_check(now, scheduling.interval_hours(db, offer, competitor, settings))
    db.commit()
    if offers:
        log.info("Scheduled %d check(s) in %d run(s)", len(offers), len(by_competitor))
    return TickResult("planned", jobs=len(offers), runs=len(by_competitor))


def main() -> None:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        result = tick(db)
    log.info("Scheduler tick: %s", result)


if __name__ == "__main__":
    main()
