"""Automatic discovery of competitor products (docs/AUTO_DISCOVERY_PLAN.md).

A competitor's catalogue is everything found on its website, kept apart from tracked offers: an offer is
created only from an item that was matched to one of our products. A discovery run is a resumable state
machine that the worker advances one request at a time (see app.services.discovery).
"""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, one_of, utc_now_column

ITEM_STATUSES = ("active", "gone")
ITEM_MATCH = ("new", "matched", "review", "unrelated", "ignored")
ITEM_MATCH_METHODS = ("identifier", "attributes", "manual", "existing")
RUN_KINDS = ("discovery", "prices")
RUN_STATUSES = ("running", "succeeded", "failed", "stopped")
RUN_PHASES = ("detect", "list", "enrich", "done")


class CatalogItem(TimestampMixin, Base):
    """One product found on a competitor's website."""
    __tablename__ = "competitor_catalog_items"
    __table_args__ = (
        UniqueConstraint("competitor_id", "key", name="uq_catalog_items_key"),
        Index("ix_catalog_items_competitor_match", "competitor_id", "match_status"),
        one_of("status", ITEM_STATUSES, "ck_catalog_items_status"),
        one_of("match_status", ITEM_MATCH, "ck_catalog_items_match"),
        one_of("match_method", ITEM_MATCH_METHODS, "ck_catalog_items_method"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(2048))  # product URL, or e.g. "polozka:277" for a listing entry
    url: Mapped[str] = mapped_column(String(2048))  # where to see it (product page or listing page)
    title: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    price_from_listing: Mapped[bool] = mapped_column(default=False)  # the price is read from the listing itself
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    hints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")  # e.g. grade, category
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")  # recognized
    status: Mapped[str] = mapped_column(String(16), default="active")
    match_status: Mapped[str] = mapped_column(String(16), default="new")
    match_method: Mapped[str | None] = mapped_column(String(16))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("competitor_products.id", ondelete="SET NULL"))
    candidates: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")  # for review
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # product page read
    enrich_error: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = utc_now_column()
    last_seen_at: Mapped[datetime] = utc_now_column()
    missing_runs: Mapped[int] = mapped_column(Integer, default=0)


class DiscoveryRun(Base):
    """One pass over a competitor's product sources (weekly), or a daily read of listing prices."""
    __tablename__ = "discovery_runs"
    __table_args__ = (
        Index("ix_discovery_runs_open", "status", "competitor_id"),
        one_of("kind", RUN_KINDS, "ck_discovery_runs_kind"),
        one_of("status", RUN_STATUSES, "ck_discovery_runs_status"),
        one_of("phase", RUN_PHASES, "ck_discovery_runs_phase"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="discovery")
    mode: Mapped[str] = mapped_column(String(16), default="shadow")  # mode at the start: shadow / auto
    status: Mapped[str] = mapped_column(String(16), default="running")
    phase: Mapped[str] = mapped_column(String(16), default="list")
    source_type: Mapped[str | None] = mapped_column(String(16))
    queue: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")  # URLs still to read
    done_urls: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    seen_keys: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    requests: Mapped[int] = mapped_column(Integer, default=0)
    errors_in_row: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = utc_now_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
