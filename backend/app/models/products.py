from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import VAT_MODES, TimestampMixin, one_of

PRIORITIES = ("strategic", "regular", "stable")
TRACKING_STATUSES = ("active", "paused")
OFFER_STATUSES = ("active", "paused", "archived")
MATCH_STATUSES = ("matched", "pending_review", "unmatched", "ignored")
MATCHING_METHODS = ("identifier", "name", "manual", "ai")


class Product(TimestampMixin, Base):
    """One of our own products."""
    __tablename__ = "products"
    __table_args__ = (
        one_of("priority", PRIORITIES, "ck_products_priority"),
        one_of("tracking_status", TRACKING_STATUSES, "ck_products_tracking"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    internal_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(500))
    isbn: Mapped[str | None] = mapped_column(String(13), index=True, unique=True)  # normalized ISBN-13
    ean: Mapped[str | None] = mapped_column(String(14), index=True, unique=True)
    sku: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    brand: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    # e.g. {"subject": "matematika", "grade": 2, "type": "pracovní sešit"}
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    priority: Mapped[str] = mapped_column(String(16), default="regular")
    tracking_status: Mapped[str] = mapped_column(String(16), default="active")

    prices: Mapped[list["ProductPrice"]] = relationship(cascade="all, delete-orphan", order_by="ProductPrice.market",
                                                        lazy="selectin")


class ProductPrice(TimestampMixin, Base):
    """Our own price for a product on one market (e.g. SK in EUR, CZ in CZK)."""
    __tablename__ = "product_prices"
    __table_args__ = (
        UniqueConstraint("product_id", "market", name="uq_product_prices_product_market"),
        one_of("vat_mode", VAT_MODES, "ck_product_prices_vat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    market: Mapped[str] = mapped_column(String(16))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    vat_mode: Mapped[str] = mapped_column(String(16), default="with_vat")


class CompetitorProduct(TimestampMixin, Base):
    """A competitor's offer: one product page (and variant) that we track."""
    __tablename__ = "competitor_products"
    __table_args__ = (
        UniqueConstraint("competitor_id", "product_url", "variant", name="uq_competitor_products_url"),
        Index("ix_competitor_products_due", "status", "next_check_at"),
        one_of("status", OFFER_STATUSES, "ck_competitor_products_status"),
        one_of("match_status", MATCH_STATUSES, "ck_competitor_products_match_status"),
        one_of("matching_method", MATCHING_METHODS, "ck_competitor_products_matching_method"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    product_url: Mapped[str] = mapped_column(String(2048))
    variant: Mapped[str] = mapped_column(String(100), default="", server_default="")  # "" = default variant
    external_product_id: Mapped[str | None] = mapped_column(String(128))
    matching_method: Mapped[str | None] = mapped_column(String(16))
    match_status: Mapped[str] = mapped_column(String(16), default="unmatched")
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(16), default="active")
    custom_frequency_hours: Mapped[int | None] = mapped_column(Integer)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
