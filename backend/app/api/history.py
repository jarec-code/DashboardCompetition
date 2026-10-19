"""Price history: chart data for one product on one market, and a searchable list of changes."""
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import Competitor, CompetitorProduct, CurrentPrice, PriceHistory, Product
from app.schemas.common import Page

router = APIRouter(prefix="/history", tags=["history"])


class Point(BaseModel):
    t: datetime
    price: Decimal | None
    availability: str
    change_types: list[str]
    synthetic: bool = False  # start of the period (value carried over) or "now" (line extended)


class Series(BaseModel):
    offer_id: int
    competitor_id: int
    competitor_name: str
    slot: int  # stable color slot: follows the competitor, never its rank in a filtered view
    currency: str | None
    plottable: bool  # False when its currency differs from the chart axis (never mixed on one axis)
    points: list[Point]


class ChartOut(BaseModel):
    product_id: int
    product_name: str
    market: str | None
    markets: list[str]
    axis_currency: str | None
    own_price: Decimal | None
    own_currency: str | None
    period_from: datetime
    period_to: datetime
    series: list[Series]


@router.get("/chart", response_model=ChartOut)
def chart(db: DbSession, _user: CurrentUser, product_id: int, market: str | None = Query(default=None, max_length=16),
          period_from: datetime | None = Query(default=None, alias="from"),
          period_to: datetime | None = Query(default=None, alias="to"),
          competitor_ids: str | None = Query(default=None, max_length=500)) -> ChartOut:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produkt neexistuje.")
    now = datetime.now(UTC)
    end = min(period_to or now, now)
    start = period_from or end - timedelta(days=90)
    if start >= end:
        raise HTTPException(422, "Začiatok obdobia musí byť pred koncom.")

    offers = db.execute(select(CompetitorProduct, Competitor)
                        .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
                        .where(CompetitorProduct.product_id == product_id, Competitor.status != "archived")
                        .order_by(Competitor.id, CompetitorProduct.id)).all()
    markets = sorted({c.market or c.country or "?" for _, c in offers})
    market = (market or "").upper() or (markets[0] if markets else None)
    slots = {cid: i for i, cid in enumerate(sorted({c.id for _, c in offers}))}
    selected = {int(x) for x in competitor_ids.split(",") if x.strip().isdigit()} if competitor_ids else None
    own = next((p for p in product.prices if p.market == market), None)

    series: list[Series] = []
    for offer, competitor in offers:
        if (competitor.market or competitor.country or "?") != market:
            continue
        if selected is not None and competitor.id not in selected:
            continue
        before = db.scalars(select(PriceHistory).where(PriceHistory.competitor_product_id == offer.id,
                                                       PriceHistory.observed_at < start)
                            .order_by(PriceHistory.observed_at.desc()).limit(1)).first()
        rows = list(db.scalars(select(PriceHistory).where(PriceHistory.competitor_product_id == offer.id,
                                                          PriceHistory.observed_at >= start,
                                                          PriceHistory.observed_at <= end)
                               .order_by(PriceHistory.observed_at)))
        points = []
        if before is not None:
            points.append(Point(t=start, price=before.price, availability=before.availability, change_types=[],
                                synthetic=True))
        points += [Point(t=r.observed_at, price=r.price, availability=r.availability, change_types=r.change_types)
                   for r in rows]
        if points:  # the price holds until the next change: extend the step line to the end of the period
            last = points[-1]
            points.append(Point(t=end, price=last.price, availability=last.availability, change_types=[],
                                synthetic=True))
        currency = (rows[-1].currency if rows else before.currency if before else None)
        series.append(Series(offer_id=offer.id, competitor_id=competitor.id, competitor_name=competitor.name,
                             slot=slots[competitor.id], currency=currency, plottable=True, points=points))

    currencies = Counter(s.currency for s in series if s.currency)
    axis = own.currency if own else (currencies.most_common(1)[0][0] if currencies else None)
    for s in series:
        s.plottable = s.currency is None or axis is None or s.currency == axis
    return ChartOut(product_id=product.id, product_name=product.name, market=market, markets=markets,
                    axis_currency=axis, own_price=own.price if own and own.currency == axis else None,
                    own_currency=own.currency if own else None, period_from=start, period_to=end, series=series)


class ChangeOut(BaseModel):
    id: int
    observed_at: datetime
    offer_id: int
    product_url: str
    offer_title: str | None
    competitor_id: int
    competitor_name: str
    product_id: int | None
    product_name: str | None
    price: Decimal | None
    previous_price: Decimal | None
    currency: str | None
    availability: str
    change_types: list[str]


@router.get("/changes", response_model=Page[ChangeOut])
def changes(db: DbSession, _user: CurrentUser, product_id: int | None = None, competitor_id: int | None = None,
            period_from: datetime | None = Query(default=None, alias="from"),
            period_to: datetime | None = Query(default=None, alias="to"),
            change_type: Literal["initial", "price_up", "price_down", "currency", "availability",
                                 "original_price"] | None = None,
            page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)) -> Page[ChangeOut]:
    previous = func.lag(PriceHistory.price).over(partition_by=PriceHistory.competitor_product_id,
                                                 order_by=PriceHistory.observed_at).label("previous_price")
    h = select(PriceHistory, previous).subquery()
    query = (select(h, CompetitorProduct.product_url, CompetitorProduct.competitor_id, Competitor.name,
                    Product.id.label("pid"), Product.name.label("pname"), CurrentPrice.title)
             .join(CompetitorProduct, CompetitorProduct.id == h.c.competitor_product_id)
             .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
             .outerjoin(Product, Product.id == CompetitorProduct.product_id)
             .outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id))
    if product_id is not None:
        query = query.where(CompetitorProduct.product_id == product_id)
    if competitor_id is not None:
        query = query.where(CompetitorProduct.competitor_id == competitor_id)
    if period_from is not None:
        query = query.where(h.c.observed_at >= period_from)
    if period_to is not None:
        query = query.where(h.c.observed_at <= period_to)
    if change_type is not None:
        query = query.where(h.c.change_types.any(change_type))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(h.c.observed_at.desc(), h.c.id.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).mappings()
    items = [ChangeOut(id=r["id"], observed_at=r["observed_at"], offer_id=r["competitor_product_id"],
                       product_url=r["product_url"], offer_title=r["title"], competitor_id=r["competitor_id"],
                       competitor_name=r["name"], product_id=r["pid"], product_name=r["pname"], price=r["price"],
                       previous_price=r["previous_price"], currency=r["currency"], availability=r["availability"],
                       change_types=r["change_types"]) for r in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)
