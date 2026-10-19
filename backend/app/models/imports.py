from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import one_of, utc_now_column

IMPORT_KINDS = ("products", "offers")
IMPORT_STATUSES = ("preview", "committed", "discarded")


class ImportBatch(Base):
    """An uploaded file, validated row by row; nothing is saved until the user confirms the preview."""
    __tablename__ = "import_batches"
    __table_args__ = (
        one_of("kind", IMPORT_KINDS, "ck_import_batches_kind"),
        one_of("status", IMPORT_STATUSES, "ck_import_batches_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="preview")
    file_name: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(32))
    competitor_id: Mapped[int | None] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # [{row, status: ok|warning|duplicate|error, reason, data}] - validated and normalized
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = utc_now_column()
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
