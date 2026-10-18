"""Updates and logs: scheduler/worker state, runs, jobs, problems, and scheduling settings."""
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.config import get_settings
from app.models import Competitor, CompetitorProduct, CurrentPrice, ScrapeJob, ScrapeRun
from app.schemas.common import Page
from app.services import scheduling
from app.worker.scheduler import due_offers_query

router = APIRouter(tags=["monitoring"])
PROBLEM_THRESHOLD = 3  # failed checks / runs in a row


class RunItem(BaseModel):
    id: int
    competitor_id: int
    competitor_name: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    total_count: int
    success_count: int
    failure_count: int
    price_change_count: int
    error_summary: str | None


class JobItem(BaseModel):
    id: int
    kind: str
    status: str
    competitor_id: int
    competitor_name: str
    competitor_product_id: int | None
    url: str | None
    attempt_count: int
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    error_kind: str | None


class CompetitorProblem(BaseModel):
    id: int
    name: str
    failed_runs_in_row: int
    last_error: str | None


class OfferProblem(BaseModel):
    id: int
    competitor_id: int
    competitor_name: str
    product_url: str
    consecutive_failures: int
    last_error: str | None
    last_success_at: datetime | None


class MonitoringOut(BaseModel):
    now: datetime
    timezone: str
    scheduler_enabled: bool
    in_window: bool
    scheduler_last_tick_at: datetime | None
    worker_last_seen_at: datetime | None
    pending_jobs: int
    running_jobs: int
    due_offers: int
    stale_prices: int
    checks_24h: int
    failures_24h: int
    price_changes_24h: int
    problem_competitors: list[CompetitorProblem]
    problem_offers: list[OfferProblem]


def _competitor_problems(db) -> list[CompetitorProblem]:
    problems = []
    for competitor in db.scalars(select(Competitor).where(Competitor.status == "active")):
        runs = list(db.scalars(select(ScrapeRun).where(ScrapeRun.competitor_id == competitor.id,
                                                       ScrapeRun.status != "running")
                               .order_by(ScrapeRun.started_at.desc()).limit(10)))
        in_row = 0
        for run in runs:
            if run.status != "failed":
                break
            in_row += 1
        if in_row >= PROBLEM_THRESHOLD:
            problems.append(CompetitorProblem(id=competitor.id, name=competitor.name, failed_runs_in_row=in_row,
                                              last_error=runs[0].error_summary))
    return problems


@router.get("/monitoring", response_model=MonitoringOut)
def monitoring(db: DbSession, _user: CurrentUser) -> MonitoringOut:
    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    settings = scheduling.load_settings(db)
    jobs_by_status = dict(db.execute(select(ScrapeJob.status, func.count()).where(
        ScrapeJob.status.in_(("pending", "running"))).group_by(ScrapeJob.status)).all())
    finished = dict(db.execute(select(ScrapeJob.status, func.count()).where(
        ScrapeJob.kind != "test", ScrapeJob.finished_at >= day_ago).group_by(ScrapeJob.status)).all())
    changes = db.scalar(select(func.coalesce(func.sum(ScrapeRun.price_change_count), 0))
                        .where(ScrapeRun.started_at >= day_ago))
    offer_rows = db.execute(
        select(CompetitorProduct, Competitor.name, CurrentPrice)
        .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
        .join(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id)
        .where(CurrentPrice.consecutive_failures >= PROBLEM_THRESHOLD, CompetitorProduct.status == "active",
               Competitor.status == "active")
        .order_by(CurrentPrice.consecutive_failures.desc()).limit(50))
    return MonitoringOut(
        now=now, timezone=get_settings().timezone, scheduler_enabled=settings.scheduler_enabled,
        in_window=scheduling.in_window(settings, now),
        scheduler_last_tick_at=scheduling.get_heartbeat(db, "scheduler"),
        worker_last_seen_at=scheduling.get_heartbeat(db, "worker"),
        pending_jobs=jobs_by_status.get("pending", 0), running_jobs=jobs_by_status.get("running", 0),
        due_offers=db.scalar(select(func.count()).select_from(due_offers_query(now).subquery())),
        stale_prices=db.scalar(select(func.count(CurrentPrice.id)).join(
            CompetitorProduct, CompetitorProduct.id == CurrentPrice.competitor_product_id).where(
            CurrentPrice.is_stale.is_(True), CompetitorProduct.status == "active")),
        checks_24h=finished.get("succeeded", 0), failures_24h=finished.get("failed", 0),
        price_changes_24h=changes,
        problem_competitors=_competitor_problems(db),
        problem_offers=[OfferProblem(id=o.id, competitor_id=o.competitor_id, competitor_name=name,
                                     product_url=o.product_url, consecutive_failures=c.consecutive_failures,
                                     last_error=c.last_error, last_success_at=c.last_success_at)
                        for o, name, c in offer_rows],
    )


@router.get("/runs", response_model=Page[RunItem])
def list_runs(db: DbSession, _user: CurrentUser, competitor_id: int | None = None,
              status: Literal["running", "succeeded", "partial", "failed"] | None = None,
              trigger: Literal["scheduled", "manual"] | None = None,
              page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)) -> Page[RunItem]:
    query = select(ScrapeRun, Competitor.name).join(Competitor, Competitor.id == ScrapeRun.competitor_id)
    for column, value in ((ScrapeRun.competitor_id, competitor_id), (ScrapeRun.status, status),
                          (ScrapeRun.trigger, trigger)):
        if value is not None:
            query = query.where(column == value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(ScrapeRun.started_at.desc(), ScrapeRun.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    items = [RunItem(**{f: getattr(run, f) for f in RunItem.model_fields if f != "competitor_name"},
                     competitor_name=name) for run, name in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/jobs", response_model=Page[JobItem])
def list_jobs(db: DbSession, _user: CurrentUser, competitor_id: int | None = None, run_id: int | None = None,
              status: Literal["pending", "running", "succeeded", "failed", "cancelled"] | None = None,
              kind: Literal["scheduled", "manual", "test"] | None = None,
              page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)) -> Page[JobItem]:
    query = (select(ScrapeJob, Competitor.name, CompetitorProduct.product_url)
             .join(Competitor, Competitor.id == ScrapeJob.competitor_id)
             .outerjoin(CompetitorProduct, CompetitorProduct.id == ScrapeJob.competitor_product_id))
    for column, value in ((ScrapeJob.competitor_id, competitor_id), (ScrapeJob.run_id, run_id),
                          (ScrapeJob.status, status), (ScrapeJob.kind, kind)):
        if value is not None:
            query = query.where(column == value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(ScrapeJob.created_at.desc(), ScrapeJob.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    items = [JobItem(**{f: getattr(job, f) for f in JobItem.model_fields if f not in ("competitor_name", "url")},
                     competitor_name=name, url=job.url or offer_url) for job, name, offer_url in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/settings/scheduling", response_model=scheduling.SchedulingSettings)
def get_scheduling(db: DbSession, _user: CurrentUser) -> scheduling.SchedulingSettings:
    return scheduling.load_settings(db)


@router.put("/settings/scheduling", response_model=scheduling.SchedulingSettings)
def put_scheduling(data: scheduling.SchedulingSettings, db: DbSession,
                   _admin: AdminUser) -> scheduling.SchedulingSettings:
    previous = scheduling.load_settings(db)
    scheduling.save_settings(db, data)
    db.flush()
    if previous.priority_interval_hours != data.priority_interval_hours:
        scheduling.reschedule(db, db.scalars(select(CompetitorProduct).where(CompetitorProduct.status == "active")))
    db.commit()
    return data
