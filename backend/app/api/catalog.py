"""Settings and state of the regular update of our own catalogue from the e-shop feed."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.services import catalog_sync

router = APIRouter(prefix="/settings/catalog-sync", tags=["catalog"])
MAX_LISTED_ROWS = 200


class LastRunOut(BaseModel):
    batch_id: int
    file_name: str
    committed_at: datetime | None
    stats: dict[str, Any]
    problems: list[dict[str, Any]]  # rows for manual review and rejected rows


class CatalogSyncOut(BaseModel):
    settings: catalog_sync.CatalogSyncSettings
    status: catalog_sync.CatalogSyncStatus
    last_run: LastRunOut | None


def _out(db) -> CatalogSyncOut:
    state = catalog_sync.load_status(db)
    batch = catalog_sync.last_batch(db, state)
    last_run = None
    if batch is not None:
        problems = [{"row": r["row"], "status": r["status"], "reason": r["reason"],
                     "name": (r.get("data") or {}).get("name"), "internal_id": (r.get("data") or {}).get("internal_id")}
                    for r in batch.rows if r["status"] in ("review", "error")][:MAX_LISTED_ROWS]
        last_run = LastRunOut(batch_id=batch.id, file_name=batch.file_name, committed_at=batch.committed_at,
                              stats=batch.stats, problems=problems)
    return CatalogSyncOut(settings=catalog_sync.load_settings(db), status=state, last_run=last_run)


@router.get("", response_model=CatalogSyncOut)
def get_catalog_sync(db: DbSession, _user: CurrentUser) -> CatalogSyncOut:
    return _out(db)


@router.put("", response_model=CatalogSyncOut)
def put_catalog_sync(data: catalog_sync.CatalogSyncSettings, db: DbSession, _admin: AdminUser) -> CatalogSyncOut:
    if data.enabled and not data.feed_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Zadajte adresu feedu.")
    catalog_sync.save_settings(db, data)
    db.commit()
    return _out(db)


@router.post("/run", response_model=CatalogSyncOut, status_code=status.HTTP_202_ACCEPTED)
def run_catalog_sync(db: DbSession, _admin: AdminUser) -> CatalogSyncOut:
    """Ask the worker to synchronize now (within half a minute); the download never runs in this request."""
    if not catalog_sync.load_settings(db).feed_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "Najprv uložte adresu feedu.")
    catalog_sync.request_run(db)
    db.commit()
    return _out(db)
