from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Competitor, CompetitorProduct, CurrentPrice, ScrapeConfig, ScrapeRun
from app.schemas.common import Page
from app.schemas.competitors import (
    CompetitorCreate,
    CompetitorFilters,
    CompetitorListItem,
    CompetitorOut,
    CompetitorUpdate,
    ScrapeRunOut,
)
from app.schemas.scrape_config import ScrapeConfigIn, ScrapeConfigOut
from app.services import scheduling
from app.services.urls import domain_of

router = APIRouter(prefix="/competitors", tags=["competitors"])

NOT_NULLABLE = {"name", "base_url", "competitor_type", "preferred_source", "status"}

offer_count = (
    select(func.count(CompetitorProduct.id))
    .where(CompetitorProduct.competitor_id == Competitor.id, CompetitorProduct.status != "archived")
    .correlate(Competitor).scalar_subquery()
)
last_success_at = (
    select(func.max(CurrentPrice.last_success_at))
    .join(CompetitorProduct, CompetitorProduct.id == CurrentPrice.competitor_product_id)
    .where(CompetitorProduct.competitor_id == Competitor.id)
    .correlate(Competitor).scalar_subquery()
)
last_run_error_count = func.coalesce(
    select(ScrapeRun.failure_count).where(ScrapeRun.competitor_id == Competitor.id)
    .order_by(ScrapeRun.started_at.desc()).limit(1).correlate(Competitor).scalar_subquery(),
    0,
)

SORT_COLUMNS = {
    "name": func.lower(Competitor.name),
    "domain": Competitor.domain,
    "country": Competitor.country,
    "status": Competitor.status,
    "type": Competitor.competitor_type,
    "frequency": Competitor.default_frequency_hours,
    "offers": offer_count,
    "last_success": last_success_at,
    "errors": last_run_error_count,
    "updated": Competitor.updated_at,
}
SortKey = Literal[tuple(SORT_COLUMNS) + tuple(f"-{k}" for k in SORT_COLUMNS)]  # type: ignore[valid-type]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _get(db: Session, competitor_id: int) -> Competitor:
    competitor = db.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konkurent neexistuje.")
    return competitor


def _check_duplicate_domain(db: Session, domain: str, reason: str | None, exclude_id: int | None = None) -> None:
    query = select(Competitor).where(Competitor.domain == domain)
    if exclude_id is not None:
        query = query.where(Competitor.id != exclude_id)
    existing = db.scalars(query.limit(1)).first()
    if existing and not reason:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "duplicate_domain",
            "message": f"Doména {domain} je už evidovaná u konkurenta „{existing.name}“. "
                       "Ak ide o samostatného konkurenta, uveďte dôvod.",
            "existing": {"id": existing.id, "name": existing.name},
        })


@router.get("", response_model=Page[CompetitorListItem])
def list_competitors(
    db: DbSession, _user: CurrentUser,
    q: str | None = Query(default=None, max_length=200),
    status_filter: Literal["active", "paused", "archived", "all"] | None = Query(default=None, alias="status"),
    country: str | None = Query(default=None, max_length=2),
    sort: SortKey = "name",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[CompetitorListItem]:
    query: Select = select(Competitor, offer_count.label("offer_count"), last_success_at.label("last_success_at"),
                           last_run_error_count.label("last_run_error_count"))
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip().lower())}%"
        query = query.where(or_(func.lower(Competitor.name).like(pattern, escape="\\"),
                                Competitor.domain.like(pattern, escape="\\")))
    if status_filter is None:
        query = query.where(Competitor.status != "archived")  # archived ones are listed only on request
    elif status_filter != "all":
        query = query.where(Competitor.status == status_filter)
    if country:
        query = query.where(Competitor.country == country.upper())

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    column = SORT_COLUMNS[sort.lstrip("-")]
    order = column.desc().nulls_last() if sort.startswith("-") else column.asc().nulls_last()
    rows = db.execute(query.order_by(order, Competitor.id).offset((page - 1) * page_size).limit(page_size))
    items = [
        CompetitorListItem.model_validate({**{c: getattr(comp, c) for c in CompetitorListItem.model_fields
                                               if hasattr(comp, c)},
                                           "offer_count": oc, "last_success_at": ls, "last_run_error_count": ec})
        for comp, oc, ls, ec in rows
    ]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/filters", response_model=CompetitorFilters)
def competitor_filters(db: DbSession, _user: CurrentUser) -> CompetitorFilters:
    countries = db.scalars(select(Competitor.country).where(Competitor.country.is_not(None))
                           .distinct().order_by(Competitor.country))
    return CompetitorFilters(countries=list(countries))


@router.post("", response_model=CompetitorOut, status_code=status.HTTP_201_CREATED)
def create_competitor(data: CompetitorCreate, db: DbSession, _admin: AdminUser) -> Competitor:
    domain = domain_of(data.base_url)
    _check_duplicate_domain(db, domain, data.duplicate_domain_reason)
    competitor = Competitor(**data.model_dump(exclude={"scrape_config"}), domain=domain)
    competitor.scrape_config = ScrapeConfig(**data.scrape_config.model_dump())
    db.add(competitor)
    db.commit()
    return competitor


@router.get("/{competitor_id}", response_model=CompetitorOut)
def get_competitor(competitor_id: int, db: DbSession, _user: CurrentUser) -> Competitor:
    return _get(db, competitor_id)


@router.patch("/{competitor_id}", response_model=CompetitorOut)
def update_competitor(competitor_id: int, data: CompetitorUpdate, db: DbSession, _admin: AdminUser) -> Competitor:
    competitor = _get(db, competitor_id)
    changes = data.model_dump(exclude_unset=True)
    for field in NOT_NULLABLE & changes.keys():
        if changes[field] is None:
            raise HTTPException(422, f"Pole {field} nesmie byť prázdne.")
    if "base_url" in changes:
        domain = domain_of(changes["base_url"])
        if domain != competitor.domain:
            _check_duplicate_domain(db, domain, changes.get("duplicate_domain_reason")
                                    or competitor.duplicate_domain_reason, exclude_id=competitor.id)
            competitor.domain = domain
    if "country" in changes and "market" not in changes and competitor.market in (None, competitor.country):
        changes["market"] = changes["country"]
    for field, value in changes.items():
        setattr(competitor, field, value)
    if "default_frequency_hours" in changes:
        scheduling.reschedule(db, db.scalars(select(CompetitorProduct).where(
            CompetitorProduct.competitor_id == competitor.id)))
    db.commit()
    return competitor


@router.put("/{competitor_id}/scrape-config", response_model=ScrapeConfigOut)
def update_scrape_config(competitor_id: int, data: ScrapeConfigIn, db: DbSession, _admin: AdminUser) -> ScrapeConfig:
    config = _get(db, competitor_id).scrape_config
    if (config.parser_type, config.parser_config) != (data.parser_type, data.parser_config):
        config.parser_version += 1  # results can be traced back to the parser setup that produced them
        config.last_test_status = None
        config.last_test_at = None
    for field, value in data.model_dump().items():
        setattr(config, field, value)
    db.commit()
    return config


@router.get("/{competitor_id}/runs", response_model=list[ScrapeRunOut])
def list_runs(competitor_id: int, db: DbSession, _user: CurrentUser,
              limit: int = Query(default=20, ge=1, le=100)) -> list[ScrapeRun]:
    _get(db, competitor_id)
    return list(db.scalars(select(ScrapeRun).where(ScrapeRun.competitor_id == competitor_id)
                           .order_by(ScrapeRun.started_at.desc()).limit(limit)))


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_competitor(competitor_id: int, db: DbSession, _admin: AdminUser) -> None:
    competitor = _get(db, competitor_id)
    has_data = db.scalar(select(func.count(CompetitorProduct.id))
                         .where(CompetitorProduct.competitor_id == competitor_id)) or db.scalar(
        select(func.count(ScrapeRun.id)).where(ScrapeRun.competitor_id == competitor_id))
    if has_data:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Konkurent má sledované ponuky alebo históriu kontrol. Namiesto zmazania ho archivujte.")
    db.delete(competitor)
    db.commit()
