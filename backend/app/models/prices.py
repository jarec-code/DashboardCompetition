from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import AVAILABILITY, VAT_MODES, one_of, utc_now_column


class CurrentPrice(Base):
    """Latest known state of one offer. A failed check never overwrites the last valid price."""
    __tablename__ = "current_prices"
    __table_args__ = (
        one_of("availability", AVAILABILITY, "ck_current_prices_availability"),
        one_of("vat_mode", VAT_MODES, "ck_current_prices_vat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_product_id: Mapped[int] = mapped_column(
        ForeignKey("competitor_products.id", ondelete="CASCADE"), unique=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    vat_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    title: Mapped[str | None] = mapped_column(String(500))
    found_identifiers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_error: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = utc_now_column()


class PriceHistory(Base):
    """One row per real change of a tracked value (never for an unchanged check)."""
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("competitor_product_id", "observed_at", name="uq_price_history_offer_time"),
        Index("ix_price_history_observed_at", "observed_at"),
        one_of("availability", AVAILABILITY, "ck_price_history_availability"),
        one_of("vat_mode", VAT_MODES, "ck_price_history_vat"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    competitor_product_id: Mapped[int] = mapped_column(ForeignKey("competitor_products.id", ondelete="CASCADE"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    vat_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # initial, price_up, price_down, currency, availability, original_price
    change_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
