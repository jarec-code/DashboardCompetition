"""Two-step imports: upload -> preview (validated rows) -> commit or discard."""
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import AdminUser, DbSession
from app.models import ImportBatch
from app.services import imports

router = APIRouter(prefix="/imports", tags=["imports"])


class BatchOut(BaseModel):
    id: int
    kind: str
    status: str
    file_name: str
    file_format: str
    competitor_id: int | None
    rows: list[dict[str, Any]]
    stats: dict[str, Any]
    created_at: datetime
    committed_at: datetime | None

    model_config = {"from_attributes": True}


class CommitOut(BaseModel):
    processed: int
    imported: int
    updated: int
    unchanged: int
    review: int
    duplicates: int
    errors: int
    skipped_at_commit: list[dict[str, Any]]


def _get(db, batch_id: int) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import neexistuje.")
    return batch


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, db: DbSession, admin: AdminUser,
                 kind: Literal["products", "offers"] = Form(...), competitor_id: int | None = Form(default=None),
                 market: str | None = Form(default=None, max_length=16)):
    data = await file.read(imports.MAX_BYTES + 1)
    try:
        batch = imports.preview(db, kind, data, file.filename or "import", admin.id, competitor_id,
                                default_market=market)
    except imports.ImportFileError as exc:
        raise HTTPException(422, str(exc)) from None
    db.commit()
    return batch


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, db: DbSession, _admin: AdminUser) -> ImportBatch:
    return _get(db, batch_id)


@router.post("/{batch_id}/commit", response_model=CommitOut)
def commit_batch(batch_id: int, db: DbSession, _admin: AdminUser) -> CommitOut:
    batch = _get(db, batch_id)
    try:
        result = imports.commit(db, batch)
    except imports.ImportFileError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return CommitOut(**result)


@router.post("/{batch_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
def discard_batch(batch_id: int, db: DbSession, _admin: AdminUser) -> None:
    batch = _get(db, batch_id)
    if batch.status == "preview":
        batch.status = "discarded"
        db.commit()
