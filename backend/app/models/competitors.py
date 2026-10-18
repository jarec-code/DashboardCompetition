from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import VAT_MODES, TimestampMixin, one_of

COMPETITOR_STATUSES = ("active", "paused", "archived")
COMPETITOR_TYPES = ("direct", "distributor", "marketplace", "other")
SOURCE_TYPES = ("api", "feed", "html")


class Competitor(TimestampMixin, Base):
    __tablename__ = "competitors"
    __table_args__ = (
        one_of("status", COMPETITOR_STATUSES, "ck_competitors_status"),
        one_of("competitor_type", COMPETITOR_TYPES, "ck_competitors_type"),
        one_of("preferred_source", SOURCE_TYPES, "ck_competitors_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(2048))
    domain: Mapped[str] = mapped_column(String(253), index=True)  # lowercase, without "www."
    # Filled in when a second competitor on the same domain is saved on purpose.
    duplicate_domain_reason: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    market: Mapped[str | None] = mapped_column(String(16))  # matches product_prices.market
    competitor_type: Mapped[str] = mapped_column(String(16), default="direct")
    preferred_source: Mapped[str] = mapped_column(String(8), default="html")
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    default_frequency_hours: Mapped[int | None] = mapped_column(Integer)

    scrape_config: Mapped["ScrapeConfig"] = relationship(back_populates="competitor", uselist=False,
                                                          cascade="all, delete-orphan")


class ScrapeConfig(TimestampMixin, Base):
    """Per-competitor scraping settings. parser_config is validated data only, never code."""
    __tablename__ = "scrape_configs"
    __table_args__ = (
        one_of("source_type", SOURCE_TYPES, "ck_scrape_configs_source"),
        one_of("prices_vat_mode", VAT_MODES, "ck_scrape_configs_vat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), unique=True)
    source_type: Mapped[str] = mapped_column(String(8), default="html")
    parser_type: Mapped[str] = mapped_column(String(64), default="auto")
    parser_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    parser_version: Mapped[int] = mapped_column(Integer, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=5)
    prices_vat_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_test_status: Mapped[str | None] = mapped_column(String(16))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    competitor: Mapped[Competitor] = relationship(back_populates="scrape_config")
