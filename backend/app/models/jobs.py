from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import one_of, utc_now_column

RUN_STATUSES = ("running", "succeeded", "partial", "failed")
RUN_TRIGGERS = ("scheduled", "manual")
JOB_KINDS = ("scheduled", "manual", "test")
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
ERROR_KINDS = ("temporary", "permanent")


class ScrapeRun(Base):
    """Aggregated result of one scraping run for a competitor."""
    __tablename__ = "scrape_runs"
    __table_args__ = (
        one_of("status", RUN_STATUSES, "ck_scrape_runs_status"),
        one_of("trigger", RUN_TRIGGERS, "ck_scrape_runs_trigger"),
        Index("ix_scrape_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    started_at: Mapped[datetime] = utc_now_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    price_change_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class ScrapeJob(Base):
    """Durable job queue row. A test job may target a URL that is not saved as an offer yet."""
    __tablename__ = "scrape_jobs"
    __table_args__ = (
        # At most one open job per offer: protects against duplicate scheduling.
        Index("uq_scrape_jobs_open_offer", "competitor_product_id", unique=True,
              postgresql_where=text("status IN ('pending', 'running')")),
        Index("ix_scrape_jobs_claim", "status", "priority", "scheduled_at"),
        Index("ix_scrape_jobs_created_at", "created_at"),
        Index("ix_scrape_jobs_run_id", "run_id"),
        one_of("kind", JOB_KINDS, "ck_scrape_jobs_kind"),
        one_of("status", JOB_STATUSES, "ck_scrape_jobs_status"),
        one_of("error_kind", ERROR_KINDS, "ck_scrape_jobs_error_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    competitor_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("competitor_products.id", ondelete="CASCADE"))
    url: Mapped[str | None] = mapped_column(String(2048))  # only for test jobs without an offer
    run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), default="scheduled")
    priority: Mapped[int] = mapped_column(SmallInteger, default=100)  # lower runs first
    scheduled_at: Mapped[datetime] = utc_now_column()
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(16))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # extracted values, shown for test jobs
    created_at: Mapped[datetime] = utc_now_column()
