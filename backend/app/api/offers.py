from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Competitor, CompetitorProduct, CurrentPrice, PriceHistory, Product
from app.schemas.common import Page
from app.schemas.offers import (
    AssignmentIn,
    CandidateOut,
    CurrentPriceOut,
    InvalidUrl,
    OfferOut,
    OfferRemoveResult,
    OffersCreate,
    OffersCreateResult,
    OfferUpdate,
    ProductRef,
)
from app.services import matching, scheduling
from app.services.urls import InvalidUrl as InvalidUrlError
from app.services.urls import normalize_url

router = APIRouter(tags=["offers"])

MatchStatus = Literal["matched", "pending_review", "unmatched", "ignored"]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _base_query() -> Select:
    return (select(CompetitorProduct, Competitor.name, Product.id, Product.name, CurrentPrice)
            .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
            .outerjoin(Product, Product.id == CompetitorProduct.product_id)
            .outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id))


def _to_out(row) -> OfferOut:
    offer, competitor_name, product_id, product_name, current = row
    return OfferOut(
        id=offer.id, competitor_id=offer.competitor_id, competitor_name=competitor_name,
        product_url=offer.product_url, variant=offer.variant, external_product_id=offer.external_product_id,
        product=ProductRef(id=product_id, name=product_name) if product_id else None,
        matching_method=offer.matching_method, match_status=offer.match_status, match_score=offer.match_score,
        status=offer.status, custom_frequency_hours=offer.custom_frequency_hours, next_check_at=offer.next_check_at,
        current=CurrentPriceOut.model_validate(current, from_attributes=True) if current else None,
        created_at=offer.created_at, updated_at=offer.updated_at,
    )


def _offer_out(db: Session, offer_id: int) -> OfferOut:
    return _to_out(db.execute(_base_query().where(CompetitorProduct.id == offer_id)).one())


def _get_offer(db: Session, offer_id: int) -> CompetitorProduct:
    offer = db.get(CompetitorProduct, offer_id)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ponuka neexistuje.")
    return offer


def _belongs_to(url: str, domain: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def _list(db: Session, query: Select, q: str | None, match_status: str | None, offer_status: str | None,
          page: int, page_size: int) -> Page[OfferOut]:
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip().lower())}%"
        query = query.where(or_(func.lower(CompetitorProduct.product_url).like(pattern, escape="\\"),
                                func.lower(CurrentPrice.title).like(pattern, escape="\\"),
                                func.lower(Product.name).like(pattern, escape="\\")))
    if match_status:
        query = query.where(CompetitorProduct.match_status == match_status)
    if offer_status:
        query = query.where(CompetitorProduct.status == offer_status)
    else:
        query = query.where(CompetitorProduct.status != "archived")
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(CompetitorProduct.created_at.desc(), CompetitorProduct.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    return Page(items=[_to_out(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/competitors/{competitor_id}/offers", response_model=Page[OfferOut])
def list_competitor_offers(
    competitor_id: int, db: DbSession, _user: CurrentUser,
    q: str | None = Query(default=None, max_length=200), match_status: MatchStatus | None = None,
    status_filter: Literal["active", "paused", "archived"] | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
) -> Page[OfferOut]:
    if db.get(Competitor, competitor_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konkurent neexistuje.")
    query = _base_query().where(CompetitorProduct.competitor_id == competitor_id)
    return _list(db, query, q, match_status, status_filter, page, page_size)


@router.get("/offers", response_model=Page[OfferOut])
def list_offers(
    db: DbSession, _user: CurrentUser,
    q: str | None = Query(default=None, max_length=200), match_status: MatchStatus | None = None,
    product_id: int | None = None, competitor_id: int | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
) -> Page[OfferOut]:
    query = _base_query()
    if product_id is not None:
        query = query.where(CompetitorProduct.product_id == product_id)
    if competitor_id is not None:
        query = query.where(CompetitorProduct.competitor_id == competitor_id)
    return _list(db, query, q, match_status, None, page, page_size)


@router.post("/competitors/{competitor_id}/offers", response_model=OffersCreateResult,
             status_code=status.HTTP_201_CREATED)
def create_offers(competitor_id: int, data: OffersCreate, db: DbSession, _admin: AdminUser) -> OffersCreateResult:
    competitor = db.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konkurent neexistuje.")
    if competitor.status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, "Archivovanému konkurentovi nemožno pridávať ponuky.")

    existing = set(db.scalars(select(CompetitorProduct.product_url).where(
        CompetitorProduct.competitor_id == competitor_id, CompetitorProduct.variant == data.variant)))
    created, duplicates, invalid, seen = [], [], [], set()
    for raw in data.urls:
        raw = raw.strip()
        if not raw:
            continue
        try:
            url = normalize_url(raw)
        except InvalidUrlError as exc:
            invalid.append(InvalidUrl(url=raw, reason=str(exc)))
            continue
        if not _belongs_to(url, competitor.domain):
            invalid.append(InvalidUrl(url=raw, reason=f"URL nepatrí doméne konkurenta ({competitor.domain})."))
            continue
        if url in existing or url in seen:
            duplicates.append(url)
            continue
        seen.add(url)
        offer = CompetitorProduct(competitor_id=competitor_id, product_url=url, variant=data.variant)
        db.add(offer)
        created.append(offer)
    db.flush()
    for offer in created:
        matching.auto_match(db, offer)
    db.commit()
    return OffersCreateResult(created=[_offer_out(db, o.id) for o in created], duplicates=duplicates,
                              invalid=invalid)


@router.patch("/offers/{offer_id}", response_model=OfferOut)
def update_offer(offer_id: int, data: OfferUpdate, db: DbSession, _admin: AdminUser) -> OfferOut:
    offer = _get_offer(db, offer_id)
    changes = data.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        raise HTTPException(422, "Pole status nesmie byť prázdne.")
    if "variant" in changes:
        changes["variant"] = (changes["variant"] or "").strip()
        clash = db.scalar(select(CompetitorProduct.id).where(
            CompetitorProduct.competitor_id == offer.competitor_id, CompetitorProduct.product_url == offer.product_url,
            CompetitorProduct.variant == changes["variant"], CompetitorProduct.id != offer.id))
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, "Táto URL s rovnakým variantom je už sledovaná.")
    for field, value in changes.items():
        setattr(offer, field, value)
    if "custom_frequency_hours" in changes:
        scheduling.reschedule(db, [offer])
    db.commit()
    return _offer_out(db, offer_id)


@router.post("/offers/{offer_id}/assignment", response_model=OfferOut)
def set_assignment(offer_id: int, data: AssignmentIn, db: DbSession, _admin: AdminUser) -> OfferOut:
    offer = _get_offer(db, offer_id)
    if data.action == "assign":
        if data.product_id is None or db.get(Product, data.product_id) is None:
            raise HTTPException(422, "Vyberte existujúci produkt.")
        matching.assign_manually(offer, data.product_id)
        scheduling.reschedule(db, [offer])
    elif data.action == "ignore":
        matching.ignore(offer)
    else:
        matching.reset_to_auto(db, offer)
    db.commit()
    return _offer_out(db, offer_id)


@router.get("/offers/{offer_id}/candidates", response_model=list[CandidateOut])
def offer_candidates(offer_id: int, db: DbSession, _user: CurrentUser,
                     q: str | None = Query(default=None, max_length=200)) -> list[CandidateOut]:
    """Suggested products for an offer: by its scraped title and identifiers, or by `q`."""
    offer = _get_offer(db, offer_id)
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    title = q or (current.title if current else None)
    candidates = matching.candidates_for(db, title, current.found_identifiers if current and not q else None,
                                         search=bool(q))
    return [CandidateOut(product=ProductRef(id=c.product.id, name=c.product.name), isbn=c.product.isbn,
                         ean=c.product.ean, score=c.score, method=c.method) for c in candidates]


@router.delete("/offers/{offer_id}", response_model=OfferRemoveResult)
def remove_offer(offer_id: int, db: DbSession, _admin: AdminUser) -> OfferRemoveResult:
    """An offer with price history is archived (history stays in reports); otherwise it is deleted."""
    offer = _get_offer(db, offer_id)
    has_history = db.scalar(select(func.count(PriceHistory.id)).where(PriceHistory.competitor_product_id == offer_id))
    if has_history:
        offer.status = "archived"
        db.commit()
        return OfferRemoveResult(result="archived")
    db.delete(offer)
    db.commit()
    return OfferRemoveResult(result="deleted")
