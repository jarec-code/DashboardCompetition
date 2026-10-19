"""Removal of old diagnostic data so the database stays small (assignment: store only needed diagnostics).

Never deleted: price history, current prices, scrape runs (their summaries are the long-term log),
open or acknowledged alerts.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.models import Alert, ImportBatch, ScrapeJob

JOB_DAYS = 30            # finished scrape jobs (per-page details; runs keep the summary)
IMPORT_DAYS = 30         # committed or discarded import batches (they hold the uploaded rows)
STALE_PREVIEW_DAYS = 1   # previews nobody confirmed
RESOLVED_ALERT_DAYS = 180


def cleanup(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    jobs = db.execute(delete(ScrapeJob).where(
        ScrapeJob.status.in_(("succeeded", "failed", "cancelled")),
        ScrapeJob.finished_at < now - timedelta(days=JOB_DAYS))).rowcount
    imports = db.execute(delete(ImportBatch).where(or_(
        (ImportBatch.status != "preview") & (ImportBatch.created_at < now - timedelta(days=IMPORT_DAYS)),
        (ImportBatch.status == "preview") & (ImportBatch.created_at < now - timedelta(days=STALE_PREVIEW_DAYS)),
    ))).rowcount
    alerts = db.execute(delete(Alert).where(
        Alert.status == "resolved", Alert.resolved_at < now - timedelta(days=RESOLVED_ALERT_DAYS))).rowcount
    return {"jobs": jobs, "imports": imports, "alerts": alerts}
