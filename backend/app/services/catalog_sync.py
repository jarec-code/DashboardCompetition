"""Regular update of our own catalogue from the product feed of our e-shop (e.g. the Heureka feed of etaktik.cz).

The worker downloads the feed when it is due (every `interval_hours`, or right away after "Synchronize now"),
then runs the ordinary product import on it and commits it: new products are added, existing ones updated,
ambiguous rows are left for manual review. The result is an import batch like any uploaded file.
One request per run; robots.txt and the SSRF checks of the fetcher apply as for any other page.
"""
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AppSetting, ImportBatch
from app.schemas.products import MARKET_CURRENCY
from app.scraping.fetcher import Fetcher, FetchError
from app.services import imports
from app.services.urls import InvalidUrl, normalize_url

log = logging.getLogger("catalog_sync")
SETTINGS_KEY = "catalog_sync"
STATUS_KEY = "catalog_sync_status"
LOCK_ID = 727_003
TIMEOUT_SECONDS = 60
_XML_ENCODING = re.compile(rb"^(\s*<\?xml[^>]*?encoding=)([\"'])[^\"']+\2", re.IGNORECASE)


class CatalogSyncSettings(BaseModel):
    enabled: bool = False
    feed_url: str | None = Field(default=None, max_length=2048)
    market: str = "CZ"
    interval_hours: int = Field(default=24, ge=1, le=720)

    @field_validator("feed_url")
    @classmethod
    def url(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        try:
            return normalize_url(value)
        except InvalidUrl as exc:
            raise ValueError(str(exc)) from None

    @field_validator("market")
    @classmethod
    def known_market(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in MARKET_CURRENCY:
            raise ValueError(f"Neznámy trh {value}. Povolené: {', '.join(MARKET_CURRENCY)}.")
        return value


class CatalogSyncStatus(BaseModel):
    requested_at: datetime | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_batch_id: int | None = None


def _load(db: Session, key: str, model):
    row = db.get(AppSetting, key)
    return model.model_validate(row.value) if row and isinstance(row.value, dict) else model()


def _save(db: Session, key: str, value: BaseModel) -> None:
    data = value.model_dump(mode="json")
    db.execute(insert(AppSetting).values(key=key, value=data).on_conflict_do_update(
        index_elements=["key"], set_={"value": data, "updated_at": datetime.now(UTC)}))


def load_settings(db: Session) -> CatalogSyncSettings:
    return _load(db, SETTINGS_KEY, CatalogSyncSettings)


def save_settings(db: Session, settings: CatalogSyncSettings) -> None:
    _save(db, SETTINGS_KEY, settings)


def load_status(db: Session) -> CatalogSyncStatus:
    return _load(db, STATUS_KEY, CatalogSyncStatus)


def request_run(db: Session, now: datetime | None = None) -> None:
    status = load_status(db)
    status.requested_at = now or datetime.now(UTC)
    _save(db, STATUS_KEY, status)


def is_due(settings: CatalogSyncSettings, status: CatalogSyncStatus, now: datetime) -> bool:
    if not settings.feed_url:
        return False
    if status.requested_at is not None:  # "Synchronize now" works also while automatic updates are off
        return True
    if not settings.enabled:
        return False
    return status.last_run_at is None or status.last_run_at + timedelta(hours=settings.interval_hours) <= now


def _as_utf8(text: str) -> bytes:
    """The fetcher returns decoded text; re-encoded as UTF-8, the XML declaration must say so too."""
    return _XML_ENCODING.sub(rb"\1\2UTF-8\2", text.encode("utf-8"), count=1)


def run(db: Session, fetcher: Fetcher, settings: CatalogSyncSettings, now: datetime | None = None
        ) -> dict[str, Any] | None:
    """Download and import the feed. Records the outcome in the status. Does not commit."""
    now = now or datetime.now(UTC)
    status = load_status(db)
    status.requested_at, status.last_run_at = None, now
    url = settings.feed_url
    try:
        if not fetcher.robots_allowed(url):
            raise FetchError("Súbor robots.txt webu zakazuje načítanie feedu.", "permanent")
        response = fetcher.fetch(url, timeout=TIMEOUT_SECONDS, max_bytes=imports.MAX_BYTES)
        batch = imports.preview(db, "products", _as_utf8(response.text or ""), url, None,
                                default_market=settings.market)
        if batch.file_format not in imports.FEED_FORMATS:
            raise imports.ImportFileError("Na adrese nie je produktový XML feed (Heureka alebo Google Merchant).")
        outcome = imports.commit(db, batch)
        batch.rows = [r for r in batch.rows if r["status"] != "unchanged"]  # a daily run: keep only what matters
    except (FetchError, imports.ImportFileError) as exc:
        db.rollback()
        status.last_error = str(exc)[:500]
        _save(db, STATUS_KEY, status)
        log.warning("Catalogue sync from %s failed: %s", url, exc)
        return None
    status.last_success_at, status.last_error, status.last_batch_id = now, None, batch.id
    _save(db, STATUS_KEY, status)
    log.info("Catalogue sync from %s: %s", url, {k: v for k, v in outcome.items() if k != "skipped_at_commit"})
    return outcome


def run_if_due(db: Session, fetcher: Fetcher, now: datetime | None = None) -> dict[str, Any] | None:
    """Called by the worker loop. Commits. Only one process syncs at a time (advisory lock)."""
    now = now or datetime.now(UTC)
    settings, status = load_settings(db), load_status(db)
    if not is_due(settings, status, now):
        return None
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK_ID))):
        db.rollback()
        return None
    outcome = run(db, fetcher, settings, now)
    db.commit()
    return outcome


def last_batch(db: Session, status: CatalogSyncStatus) -> ImportBatch | None:
    return db.get(ImportBatch, status.last_batch_id) if status.last_batch_id else None
