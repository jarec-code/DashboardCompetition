from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, one_of, utc_now_column

RULE_TYPES = ("price_drop", "cheaper_than_us", "unavailable", "stale", "scraper_failing")
ALERT_STATUSES = ("open", "acknowledged", "resolved")


class AlertRule(TimestampMixin, Base):
    """A configurable condition, evaluated by plain code (never by AI)."""
    __tablename__ = "alert_rules"
    __table_args__ = (one_of("rule_type", RULE_TYPES, "ck_alert_rules_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Optional scope; both empty = all offers.
    competitor_id: Mapped[int | None] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Alert(TimestampMixin, Base):
    """One occurrence of a rule for one offer. At most one active (open/acknowledged) per rule and offer."""
    __tablename__ = "alerts"
    __table_args__ = (
        Index("uq_alerts_active", "rule_id", "competitor_product_id", unique=True,
              postgresql_where=text("status IN ('open', 'acknowledged')")),
        Index("ix_alerts_status_created", "status", "created_at"),
        one_of("status", ALERT_STATUSES, "ck_alerts_status"),
        one_of("alert_type", RULE_TYPES, "ck_alerts_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"))
    competitor_product_id: Mapped[int] = mapped_column(ForeignKey("competitor_products.id", ondelete="CASCADE"),
                                                       index=True)
    alert_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="open")
    message: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_seen_at: Mapped[datetime] = utc_now_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
