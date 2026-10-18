"""Manual checks, test checks and job status. Scraping never runs inside the HTTP request: the request
only queues a job; the UI polls GET /jobs/{id} until it is finished."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Competitor, CompetitorProduct, PriceHistory, ScrapeJob
from app.schemas.scrape_config import ParserOut
from app.scraping.parsers.registry import PARSERS
from app.services.urls import InvalidUrl, domain_of, normalize_url
from app.worker import queue

router = APIRouter(tags=["jobs"])


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    competitor_id: int
    competitor_product_id: int | None
    url: str | None
    attempt_count: int
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    error_kind: str | None
    result: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TestCheckIn(BaseModel):
    url: str = Field(max_length=2048)
    variant: str = Field(default="", max_length=100)

    _v = field_validator("variant")(lambda v: v.strip())


class HistoryOut(BaseModel):
    observed_at: datetime
    price: Decimal | None
    original_price: Decimal | None
    currency: str | None
    availability: str
    vat_mode: str
    change_types: list[str]

    model_config = {"from_attributes": True}


@router.get("/parsers", response_model=list[ParserOut])
def list_parsers(_user: CurrentUser) -> list[ParserOut]:
    return [ParserOut(name=p.name, label=p.label, description=p.description, version=p.version,
                      config_kind=p.config_kind) for p in PARSERS.values()]


@router.post("/offers/{offer_id}/check", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def check_offer(offer_id: int, db: DbSession, _admin: AdminUser) -> ScrapeJob:
    offer = db.get(CompetitorProduct, offer_id)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ponuka neexistuje.")
    competitor = db.get(Competitor, offer.competitor_id)
    if competitor.status == "archived" or offer.status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, "Archivovanú ponuku ani konkurenta nemožno kontrolovať.")
    job = queue.enqueue(db, competitor.id, "manual", offer_id=offer.id)
    db.commit()
    return job


@router.post("/competitors/{competitor_id}/test", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def test_competitor(competitor_id: int, data: TestCheckIn, db: DbSession, _admin: AdminUser) -> ScrapeJob:
    """Try fetching and parsing one page with the current settings, without saving any price."""
    competitor = db.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konkurent neexistuje.")
    try:
        url = normalize_url(data.url)
    except InvalidUrl as exc:
        raise HTTPException(422, str(exc)) from None
    domain = domain_of(url)
    if domain != competitor.domain and not domain.endswith("." + competitor.domain):
        raise HTTPException(422, f"URL nepatrí doméne konkurenta ({competitor.domain}).")
    job = queue.enqueue(db, competitor.id, "test", url=url)
    job.result = {"variant": data.variant}  # input for the worker; replaced by the outcome
    db.commit()
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: DbSession, _user: CurrentUser) -> ScrapeJob:
    job = db.get(ScrapeJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Úloha neexistuje.")
    return job


@router.get("/offers/{offer_id}/history", response_model=list[HistoryOut])
def offer_history(offer_id: int, db: DbSession, _user: CurrentUser,
                  limit: int = Query(default=50, ge=1, le=500)) -> list[PriceHistory]:
    if db.get(CompetitorProduct, offer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ponuka neexistuje.")
    return list(db.scalars(select(PriceHistory).where(PriceHistory.competitor_product_id == offer_id)
                           .order_by(PriceHistory.observed_at.desc()).limit(limit)))
