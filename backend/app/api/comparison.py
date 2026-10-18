"""Price comparison, its CSV export, and the dashboard. All numbers come from the database."""
import csv
import io
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession
from app.models import Competitor, CompetitorProduct, CurrentPrice, PriceHistory, Product, ScrapeJob, ScrapeRun
from app.services import comparison

router = APIRouter(tags=["comparison"])


class OfferCellOut(BaseModel):
    offer_id: int
    competitor_id: int
    competitor_name: str
    product_url: str
    price: Decimal | None
    currency: str | None
    availability: str
    vat_mode: str
    last_success_at: datetime | None
    is_stale: bool
    last_error: str | None
    comparable: bool
    reason: str | None
    vat_unverified: bool
    diff_abs: Decimal | None
    diff_pct: Decimal | None


class ComparisonRowOut(BaseModel):
    product_id: int
    product_name: str
    priority: str
    market: str
    own_price: Decimal | None
    own_currency: str | None
    own_vat_mode: str | None
    offers: list[OfferCellOut]
    min_price: Decimal | None
    max_price: Decimal | None
    cheaper_count: int
    position: str
    max_undercut_pct: Decimal | None


class ComparisonOut(BaseModel):
    items: list[ComparisonRowOut]
    total: int
    page: int
    page_size: int
    markets: list[str]
    competitors: list[dict]
    summary: dict[str, int]


def _filtered(db, market, competitor_id, q, position):
    rows = comparison.build(db, market=market, competitor_id=competitor_id, q=q)
    if position:
        rows = [r for r in rows if r.position == position]
    return rows


@router.get("/comparison", response_model=ComparisonOut)
def get_comparison(
    db: DbSession, _user: CurrentUser,
    market: str | None = Query(default=None, max_length=16), competitor_id: int | None = None,
    q: str | None = Query(default=None, max_length=200),
    position: Literal["cheapest", "not_cheapest", "unknown"] | None = None,
    sort: Literal["name", "-name", "undercut"] = "name",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
) -> ComparisonOut:
    everything = comparison.build(db)
    rows = comparison.sort_rows(_filtered(db, market.upper() if market else None, competitor_id, q, position), sort)
    page_rows = rows[(page - 1) * page_size: page * page_size]
    competitors = sorted({(c.competitor_id, c.competitor_name) for r in everything for c in r.offers},
                         key=lambda x: x[1].lower())
    return ComparisonOut(
        items=[ComparisonRowOut.model_validate(asdict(r)) for r in page_rows], total=len(rows), page=page,
        page_size=page_size, markets=sorted({r.market for r in everything}),
        competitors=[{"id": i, "name": n} for i, n in competitors],
        summary={"cheapest": sum(r.position == "cheapest" for r in everything),
                 "not_cheapest": sum(r.position == "not_cheapest" for r in everything),
                 "unknown": sum(r.position == "unknown" for r in everything)},
    )


REASONS = {"no_price": "cena neznáma", "no_own_price": "chýba vlastná cena pre trh",
           "currency_mismatch": "iná mena", "vat_mismatch": "iný režim DPH"}


@router.get("/comparison/export.csv")
def export_comparison(db: DbSession, _user: CurrentUser, market: str | None = None,
                      competitor_id: int | None = None, q: str | None = None,
                      position: Literal["cheapest", "not_cheapest", "unknown"] | None = None) -> Response:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Produkt", "Trh", "Vlastná cena", "Mena", "Konkurent", "Cena konkurenta", "Mena konkurenta",
                     "Rozdiel", "Rozdiel %", "Dostupnosť", "Posledné overenie (UTC)", "Neaktuálne", "Poznámka", "URL"])
    for row in _filtered(db, market.upper() if market else None, competitor_id, q, position):
        for c in row.offers:
            writer.writerow([row.product_name, row.market, row.own_price or "", row.own_currency or "",
                             c.competitor_name, c.price or "", c.currency or "",
                             c.diff_abs if c.diff_abs is not None else "", c.diff_pct if c.diff_pct is not None else "",
                             c.availability,
                             c.last_success_at.isoformat(timespec="minutes") if c.last_success_at else "",
                             "áno" if c.is_stale else "nie",
                             REASONS.get(c.reason or "", "DPH neoverené" if c.vat_unverified else ""), c.product_url])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",  # BOM for Excel
                    headers={"Content-Disposition": 'attachment; filename="cenove-porovnanie.csv"'})


# ---------- dashboard ----------

class ChangeItem(BaseModel):
    observed_at: datetime
    offer_id: int
    competitor_id: int
    competitor_name: str
    product_id: int | None
    product_name: str | None
    title: str | None
    price: Decimal | None
    previous_price: Decimal | None
    currency: str | None
    change_pct: Decimal | None
    change_types: list[str]


class DashboardOut(BaseModel):
    competitors_total: int
    competitors_active: int
    offers_tracked: int
    checks_24h: int
    failed_checks_24h: int
    missing_or_stale: int
    price_changes_24h: int
    price_changes_7d: int
    cheaper_competitor_rows: int
    comparable_rows: int
    biggest_drops: list[ChangeItem]
    recent_changes: list[ChangeItem]
    recent_runs: list[dict]


def _changes(db, since: datetime | None, only_drops: bool, limit: int) -> list[ChangeItem]:
    previous = func.lag(PriceHistory.price).over(partition_by=PriceHistory.competitor_product_id,
                                                 order_by=PriceHistory.observed_at).label("previous_price")
    history = select(PriceHistory, previous).subquery()
    h = history.c
    query = (select(h.observed_at, h.competitor_product_id, h.price, h.previous_price, h.currency, h.change_types,
                    Competitor.id, Competitor.name, Product.id, Product.name, CurrentPrice.title)
             .join(CompetitorProduct, CompetitorProduct.id == h.competitor_product_id)
             .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
             .outerjoin(Product, Product.id == CompetitorProduct.product_id)
             .outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id)
             .where(~h.change_types.any("initial")))
    if since is not None:
        query = query.where(h.observed_at >= since)
    if only_drops:
        query = query.where(h.change_types.any("price_down"), h.previous_price > 0)
        query = query.order_by(((h.price - h.previous_price) / h.previous_price).asc())
    else:
        query = query.order_by(h.observed_at.desc())
    items = []
    for row in db.execute(query.limit(limit)):
        observed, offer_id, price, prev, currency, types, cid, cname, pid, pname, title = row
        pct = ((price - prev) / prev * 100).quantize(Decimal("0.1")) if price is not None and prev else None
        items.append(ChangeItem(observed_at=observed, offer_id=offer_id, competitor_id=cid, competitor_name=cname,
                                product_id=pid, product_name=pname, title=title, price=price, previous_price=prev,
                                currency=currency, change_pct=pct, change_types=types))
    return items


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: DbSession, _user: CurrentUser) -> DashboardOut:
    now = datetime.now(UTC)
    day, week, month = now - timedelta(hours=24), now - timedelta(days=7), now - timedelta(days=30)
    tracked = (select(CompetitorProduct.id).join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
               .where(CompetitorProduct.status == "active", Competitor.status == "active"))
    finished = dict(db.execute(select(ScrapeJob.status, func.count()).where(
        ScrapeJob.kind != "test", ScrapeJob.finished_at >= day).group_by(ScrapeJob.status)).all())
    real_change = ~PriceHistory.change_types.any("initial")  # the first observation is not a change
    rows = comparison.build(db)
    runs = db.execute(select(ScrapeRun, Competitor.name).join(Competitor, Competitor.id == ScrapeRun.competitor_id)
                      .order_by(ScrapeRun.started_at.desc()).limit(5))
    return DashboardOut(
        competitors_total=db.scalar(select(func.count(Competitor.id)).where(Competitor.status != "archived")),
        competitors_active=db.scalar(select(func.count(Competitor.id)).where(Competitor.status == "active")),
        offers_tracked=db.scalar(select(func.count()).select_from(tracked.subquery())),
        checks_24h=finished.get("succeeded", 0), failed_checks_24h=finished.get("failed", 0),
        missing_or_stale=db.scalar(select(func.count()).select_from(
            tracked.outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id)
            .where(or_(CurrentPrice.id.is_(None), CurrentPrice.price.is_(None), CurrentPrice.is_stale.is_(True)))
            .subquery())),
        price_changes_24h=db.scalar(select(func.count(PriceHistory.id))
                                    .where(real_change, PriceHistory.observed_at >= day)),
        price_changes_7d=db.scalar(select(func.count(PriceHistory.id))
                                   .where(real_change, PriceHistory.observed_at >= week)),
        cheaper_competitor_rows=sum(r.position == "not_cheapest" for r in rows),
        comparable_rows=sum(r.position != "unknown" for r in rows),
        biggest_drops=_changes(db, month, only_drops=True, limit=5),
        recent_changes=_changes(db, None, only_drops=False, limit=8),
        recent_runs=[{"id": r.id, "competitor_id": r.competitor_id, "competitor_name": name, "trigger": r.trigger,
                      "status": r.status, "started_at": r.started_at, "success_count": r.success_count,
                      "total_count": r.total_count, "failure_count": r.failure_count} for r, name in runs],
    )
