"""Automatic product discovery: settings per competitor, the competitor's catalogue and decisions on its items."""
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import CatalogItem, Competitor, CompetitorProduct, DiscoveryRun, DiscoveryUrl, Product
from app.schemas.common import Page
from app.scraping.parsers.base import Extracted
from app.services import discovery, pricing

router = APIRouter(tags=["discovery"])


class DiscoverySettingsIn(BaseModel):
    mode: Literal["off", "shadow", "auto"]
    terms_confirmed: bool = False
    interval_days: int = Field(default=7, ge=1, le=60)
    budget_per_night: int = Field(default=120, ge=10, le=1000)
    spacing_seconds: int = Field(default=10, ge=5, le=300)
    max_pages: int = Field(default=400, ge=10, le=20000)
    max_depth: int = Field(default=4, ge=1, le=10)
    max_run_days: int = Field(default=14, ge=1, le=60)
    source: discovery.DiscoverySource | None = None  # None = keep (or detect automatically)
    recipe: discovery.Recipe | None = None  # None = keep the recipe as it is
    redetect: bool = False  # forget the source and the diagnosis, find the methods again


class RunOut(BaseModel):
    id: int
    kind: str
    mode: str
    status: str
    phase: str
    source_type: str | None
    requests: int
    stats: dict[str, Any]
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class DiscoveryOut(BaseModel):
    settings: discovery.DiscoverySettings
    runs: list[RunOut]
    counts: dict[str, int]  # catalogue items by match status (active items), plus "gone"


def _competitor(db, competitor_id: int) -> Competitor:
    competitor = db.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konkurent neexistuje.")
    return competitor


def _out(db, competitor: Competitor) -> DiscoveryOut:
    runs = db.scalars(select(DiscoveryRun).where(DiscoveryRun.competitor_id == competitor.id)
                      .order_by(DiscoveryRun.id.desc()).limit(10))
    counts = dict(db.execute(select(CatalogItem.match_status, func.count()).where(
        CatalogItem.competitor_id == competitor.id, CatalogItem.status == "active")
        .group_by(CatalogItem.match_status)).all())
    counts["gone"] = db.scalar(select(func.count(CatalogItem.id)).where(
        CatalogItem.competitor_id == competitor.id, CatalogItem.status == "gone"))
    return DiscoveryOut(settings=discovery.load(competitor.scrape_config), runs=list(runs), counts=counts)


@router.get("/competitors/{competitor_id}/discovery", response_model=DiscoveryOut)
def get_discovery(competitor_id: int, db: DbSession, _user: CurrentUser) -> DiscoveryOut:
    return _out(db, _competitor(db, competitor_id))


@router.put("/competitors/{competitor_id}/discovery", response_model=DiscoveryOut)
def put_discovery(competitor_id: int, data: DiscoverySettingsIn, db: DbSession, admin: AdminUser) -> DiscoveryOut:
    competitor = _competitor(db, competitor_id)
    config = competitor.scrape_config
    settings = discovery.load(config)
    if data.terms_confirmed and settings.terms_confirmed_at is None:
        settings.terms_confirmed_at, settings.terms_confirmed_by = datetime.now(UTC), admin.email
    if not data.terms_confirmed:
        settings.terms_confirmed_at = settings.terms_confirmed_by = None
    if data.mode != "off" and settings.terms_confirmed_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Objavovanie možno zapnúť až po potvrdení, že podmienky webu konkurenta ho dovoľujú.")
    if data.mode != settings.mode:
        settings.shadow_ok_runs = 0
    settings.mode = data.mode
    settings.interval_days, settings.budget_per_night = data.interval_days, data.budget_per_night
    settings.spacing_seconds = data.spacing_seconds
    settings.max_pages, settings.max_depth, settings.max_run_days = data.max_pages, data.max_depth, data.max_run_days
    if data.recipe is not None:
        new = data.recipe.model_dump()
        redetect = any(new[k] != getattr(settings, k) for k in discovery.REDETECT_FIELDS)
        for key, value in new.items():
            setattr(settings, key, value)
        if settings.diagnosis.get("suggestions"):  # suggestions of a domain now ignored disappear
            settings.diagnosis = {**settings.diagnosis, "suggestions": [
                t for t in settings.diagnosis["suggestions"] if not discovery.is_ignored(t, settings.ignored_domains)]}
        if redetect:
            data.redetect = True  # a changed recipe: the methods are found and judged anew
    if data.source is not None:
        settings.source = data.source.model_copy(update={"detected": False})
    elif data.redetect:
        settings.source, settings.diagnosis = None, {}
    discovery.save(config, settings)
    if data.mode == "off":  # stop what is running
        for run in db.scalars(select(DiscoveryRun).where(DiscoveryRun.competitor_id == competitor.id,
                                                         DiscoveryRun.status == "running")):
            run.status, run.error, run.finished_at = "stopped", "Objavovanie bolo vypnuté.", datetime.now(UTC)
    db.commit()
    return _out(db, competitor)


@router.post("/competitors/{competitor_id}/discovery/run", response_model=DiscoveryOut,
             status_code=status.HTTP_202_ACCEPTED)
def run_discovery(competitor_id: int, db: DbSession, _admin: AdminUser) -> DiscoveryOut:
    """The worker starts a run within a few seconds, also outside the night window (budget and spacing apply)."""
    competitor = _competitor(db, competitor_id)
    settings = discovery.load(competitor.scrape_config)
    if settings.mode == "off" or settings.terms_confirmed_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Najprv zapnite objavovanie a potvrďte podmienky webu.")
    if competitor.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Konkurent nie je aktívny.")
    discovery.request_run(competitor.scrape_config)
    db.commit()
    return _out(db, competitor)


# ---------- the competitor's catalogue ----------

class CatalogItemOut(BaseModel):
    id: int
    key: str
    url: str
    title: str | None
    price: Decimal | None
    currency: str | None
    price_from_listing: bool
    hints: dict[str, Any]
    attributes: dict[str, Any]
    identifiers: dict[str, Any]
    status: str
    match_status: str
    match_method: str | None
    product_id: int | None
    product_name: str | None
    offer_id: int | None
    candidates: list[dict[str, Any]]
    enrich_error: str | None
    found_by: str | None
    parsed_by: str | None
    fetch_status: str | None
    fetched_at: datetime | None
    canonical_url: str | None
    category: str | None
    completeness: int | None
    first_seen_at: datetime
    last_seen_at: datetime


def _item_out(item: CatalogItem, product_name: str | None) -> CatalogItemOut:
    return CatalogItemOut(**{k: getattr(item, k) for k in CatalogItemOut.model_fields if k != "product_name"},
                          product_name=product_name)


@router.get("/competitors/{competitor_id}/catalog", response_model=Page[CatalogItemOut])
def list_catalog(competitor_id: int, db: DbSession, _user: CurrentUser,
                 match_status: Literal["new", "matched", "review", "unrelated", "ignored"] | None = None,
                 item_status: Literal["active", "gone"] = "active",
                 q: str | None = Query(default=None, max_length=200),
                 incomplete: bool = False,  # products without a price, title or identifier
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)
                 ) -> Page[CatalogItemOut]:
    _competitor(db, competitor_id)
    query = (select(CatalogItem, Product.name).outerjoin(Product, Product.id == CatalogItem.product_id)
             .where(CatalogItem.competitor_id == competitor_id, CatalogItem.status == item_status))
    if match_status:
        query = query.where(CatalogItem.match_status == match_status)
    if incomplete:
        query = query.where(CatalogItem.fetch_status.is_distinct_from("skipped"),
                            func.coalesce(CatalogItem.completeness, 0) < 70)
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(CatalogItem.title).like(pattern), func.lower(Product.name).like(pattern)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(CatalogItem.title, CatalogItem.id).offset((page - 1) * page_size)
                      .limit(page_size))
    return Page(items=[_item_out(i, n) for i, n in rows], total=total, page=page, page_size=page_size)


class AssignIn(BaseModel):
    product_id: int


def _item(db, item_id: int) -> CatalogItem:
    item = db.get(CatalogItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Položka katalógu neexistuje.")
    return item


def _result(db, item: CatalogItem) -> CatalogItemOut:
    db.commit()
    db.refresh(item)
    return _item_out(item, db.scalar(select(Product.name).where(Product.id == item.product_id)))


@router.post("/catalog-items/{item_id}/assign", response_model=CatalogItemOut)
def assign_item(item_id: int, data: AssignIn, db: DbSession, _admin: AdminUser) -> CatalogItemOut:
    """A person's decision: track this item as an offer of the product (at once, whatever the mode)."""
    item = _item(db, item_id)
    if db.get(Product, data.product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produkt neexistuje.")
    item.product_id, item.match_status, item.match_method = data.product_id, "matched", "manual"
    offer = db.get(CompetitorProduct, item.offer_id) if item.offer_id else None
    if offer is None:
        offer = db.scalar(select(CompetitorProduct).where(CompetitorProduct.competitor_id == item.competitor_id,
                                                          CompetitorProduct.product_url == item.url))
    if offer is None:
        offer = CompetitorProduct(competitor_id=item.competitor_id, product_url=item.url, variant="",
                                  price_source="listing" if item.price_from_listing else "page",
                                  external_product_id=item.key.split(":", 1)[1][:128]
                                  if item.key.startswith("polozka:") else None)
        db.add(offer)
    offer.product_id, offer.matching_method, offer.match_status = data.product_id, "manual", "matched"
    offer.match_score, offer.status = Decimal("1"), "active"
    db.flush()
    item.offer_id = offer.id
    if offer.price_source == "listing" and item.price is not None:
        competitor = db.get(Competitor, item.competitor_id)
        pricing.record_success(db, offer, Extracted(price=item.price, currency=item.currency, title=item.title,
                                                    source="listing"), datetime.now(UTC),
                               default_vat_mode=competitor.scrape_config.prices_vat_mode)
    return _result(db, item)


@router.post("/catalog-items/{item_id}/ignore", response_model=CatalogItemOut)
def ignore_item(item_id: int, db: DbSession, _admin: AdminUser) -> CatalogItemOut:
    """Not comparable: never tracked. An offer created automatically from it is archived (history is kept)."""
    item = _item(db, item_id)
    offer = db.get(CompetitorProduct, item.offer_id) if item.offer_id else None
    if offer is not None and offer.matching_method in ("attributes", "identifier") and offer.status == "active":
        offer.status = "archived"
    item.match_status, item.match_method, item.product_id = "ignored", "manual", None
    return _result(db, item)


@router.post("/catalog-items/{item_id}/reset", response_model=CatalogItemOut)
def reset_item(item_id: int, db: DbSession, _admin: AdminUser) -> CatalogItemOut:
    """Back to the automatic decision at the next discovery run."""
    item = _item(db, item_id)
    if item.offer_id is None:
        item.product_id = None
    item.match_status, item.match_method, item.candidates = "new", None, []
    return _result(db, item)


class UrlGroup(BaseModel):
    template: str
    status: str
    reason: str | None
    count: int
    examples: list[str]


class UrlsOut(BaseModel):
    by_status: dict[str, int]  # queued / done / failed / skipped
    by_kind: dict[str, int]    # product / listing / category / info / unknown
    problems: list[UrlGroup]   # failed and skipped pages grouped by URL template and reason


@router.get("/competitors/{competitor_id}/discovery/urls", response_model=UrlsOut)
def discovery_urls(competitor_id: int, db: DbSession, _user: CurrentUser) -> UrlsOut:
    """What the crawler knows about the website, and which parts could not be processed."""
    _competitor(db, competitor_id)
    base = select(DiscoveryUrl).where(DiscoveryUrl.competitor_id == competitor_id)
    by_status = dict(db.execute(select(DiscoveryUrl.status, func.count()).where(
        DiscoveryUrl.competitor_id == competitor_id).group_by(DiscoveryUrl.status)).all())
    by_kind = dict(db.execute(select(DiscoveryUrl.kind, func.count()).where(
        DiscoveryUrl.competitor_id == competitor_id, DiscoveryUrl.status == "done").group_by(DiscoveryUrl.kind)).all())
    groups: dict[tuple[str, str, str | None], list[str]] = {}
    for row in db.scalars(base.where(DiscoveryUrl.status.in_(("failed", "skipped"))).order_by(DiscoveryUrl.id)
                          .limit(5000)):
        reason = (row.error or "").split(" (")[0][:200] or None
        groups.setdefault((row.template, row.status, reason), []).append(row.url)
    problems = sorted((UrlGroup(template=t, status=st, reason=r, count=len(urls), examples=urls[:3])
                       for (t, st, r), urls in groups.items()), key=lambda g: -g.count)[:50]
    return UrlsOut(by_status=by_status, by_kind=by_kind, problems=problems)
