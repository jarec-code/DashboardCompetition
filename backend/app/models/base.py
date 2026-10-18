from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def utc_now_column(**kwargs) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, **kwargs)


class TimestampMixin:
    created_at: Mapped[datetime] = utc_now_column()
    updated_at: Mapped[datetime] = utc_now_column(onupdate=func.now())


def one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    """CHECK constraint for enum-like text columns (simpler to migrate than PostgreSQL enums)."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


# Shared value sets
VAT_MODES = ("with_vat", "without_vat", "unknown")
AVAILABILITY = ("in_stock", "out_of_stock", "preorder", "unknown")
