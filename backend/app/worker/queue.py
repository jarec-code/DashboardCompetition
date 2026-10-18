"""Durable job queue in PostgreSQL (no Redis): enqueue, claim, finish and recover jobs.

- At most one open (pending/running) job per offer, enforced by a partial unique index.
- Claiming uses FOR UPDATE SKIP LOCKED, so several workers never take the same job.
- A running job whose lease expired (worker crashed or restarted) is returned to the queue.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import ScrapeJob

PRIORITY = {"test": 10, "manual": 20, "scheduled": 100}  # lower runs first
MAX_ATTEMPTS_AFTER_CRASH = 3


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(db: Session, competitor_id: int, kind: str, offer_id: int | None = None, url: str | None = None,
            scheduled_at: datetime | None = None, run_id: int | None = None) -> ScrapeJob:
    """Create a job, or return the already open job of the same offer."""
    values = dict(competitor_id=competitor_id, competitor_product_id=offer_id, url=url, kind=kind,
                  priority=PRIORITY[kind], scheduled_at=scheduled_at or now(), status="pending", attempt_count=0,
                  run_id=run_id)
    if offer_id is None:
        job = ScrapeJob(**values)
        db.add(job)
        db.flush()
        return job
    job_id = db.scalar(insert(ScrapeJob).values(**values).on_conflict_do_nothing(
        index_elements=["competitor_product_id"], index_where=ScrapeJob.status.in_(("pending", "running")))
        .returning(ScrapeJob.id))
    if job_id is None:  # an open job exists: a manual request moves it to the front
        job = db.scalars(select(ScrapeJob).where(ScrapeJob.competitor_product_id == offer_id,
                                                 ScrapeJob.status.in_(("pending", "running")))).one()
        if job.status == "pending" and PRIORITY[kind] < job.priority:
            job.priority, job.kind, job.scheduled_at = PRIORITY[kind], kind, min(job.scheduled_at, now())
        return job
    return db.get(ScrapeJob, job_id)


def claim(db: Session, limit: int = 1) -> list[ScrapeJob]:
    """Take up to `limit` due jobs for this worker (commits)."""
    due = (select(ScrapeJob.id).where(ScrapeJob.status == "pending", ScrapeJob.scheduled_at <= now())
           .order_by(ScrapeJob.priority, ScrapeJob.scheduled_at, ScrapeJob.id)
           .limit(limit).with_for_update(skip_locked=True))
    ids = list(db.scalars(update(ScrapeJob).where(ScrapeJob.id.in_(due.scalar_subquery()))
                          .values(status="running", locked_at=now(), started_at=now(),
                                  attempt_count=ScrapeJob.attempt_count + 1)
                          .returning(ScrapeJob.id)))
    db.commit()
    return list(db.scalars(select(ScrapeJob).where(ScrapeJob.id.in_(ids)).order_by(ScrapeJob.priority))) if ids else []


def finish(db: Session, job: ScrapeJob, status: str, result: dict[str, Any] | None = None,
           error: str | None = None, error_kind: str | None = None) -> None:
    job.status, job.finished_at, job.locked_at = status, now(), None
    job.result = result
    job.last_error = error[:2000] if error else None
    job.error_kind = error_kind


def retry_later(db: Session, job: ScrapeJob, at: datetime, error: str) -> None:
    job.status, job.scheduled_at, job.locked_at = "pending", at, None
    job.last_error, job.error_kind = error[:2000], "temporary"


def recover_abandoned(db: Session, lease_seconds: int) -> int:
    """Return jobs of crashed workers to the queue (or fail them after repeated crashes). Commits."""
    expired = now() - timedelta(seconds=lease_seconds)
    stuck = list(db.scalars(select(ScrapeJob).where(ScrapeJob.status == "running", or_(
        ScrapeJob.locked_at < expired, ScrapeJob.locked_at.is_(None))).with_for_update(skip_locked=True)))
    for job in stuck:
        if job.attempt_count >= MAX_ATTEMPTS_AFTER_CRASH:
            finish(db, job, "failed", error="Úloha opakovane nedobehla (worker bol prerušený).",
                   error_kind="permanent")
        else:
            job.status, job.locked_at = "pending", None
            job.last_error = "Worker bol prerušený, úloha sa zopakuje."
    db.commit()
    return len(stuck)
