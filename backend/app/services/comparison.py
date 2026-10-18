"""Price comparison: our price on a market against each competitor's current price on that market.

Rules (assignment, section 9.2):
  - a competitor is compared with our price for the competitor's market (e.g. CZ in CZK),
  - prices in different currencies, or with a known different VAT mode, are not compared
    (no conversion without explicit normalization) and do not count into min/max,
  - unknown and stale prices are flagged; an offer without any successful check has no price.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Competitor, CompetitorProduct, CurrentPrice, Product, ProductPrice


@dataclass
class OfferCell:
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
    reason: str | None  # no_price, no_own_price, currency_mismatch, vat_mismatch
    vat_unverified: bool  # one side's VAT mode is unknown: compared, but flagged
    diff_abs: Decimal | None = None
    diff_pct: Decimal | None = None


@dataclass
class ComparisonRow:
    product_id: int
    product_name: str
    priority: str
    market: str
    own_price: Decimal | None
    own_currency: str | None
    own_vat_mode: str | None
    offers: list[OfferCell] = field(default_factory=list)
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    cheaper_count: int = 0       # comparable competitors cheaper than us
    position: str = "unknown"    # cheapest, not_cheapest, unknown (nothing comparable)
    max_undercut_pct: Decimal | None = None  # most negative diff (competitor cheaper), for sorting


def _pct(diff: Decimal, base: Decimal) -> Decimal:
    return (diff / base * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _cell(offer: CompetitorProduct, competitor: Competitor, current: CurrentPrice | None,
          own: ProductPrice | None) -> OfferCell:
    price = current.price if current else None
    cell = OfferCell(
        offer_id=offer.id, competitor_id=competitor.id, competitor_name=competitor.name,
        product_url=offer.product_url, price=price, currency=current.currency if current else None,
        availability=current.availability if current else "unknown",
        vat_mode=current.vat_mode if current else "unknown",
        last_success_at=current.last_success_at if current else None,
        is_stale=bool(current and current.is_stale) or price is None,
        last_error=current.last_error if current else None, comparable=False, reason=None, vat_unverified=False)
    if price is None:
        cell.reason = "no_price"
    elif own is None:
        cell.reason = "no_own_price"
    elif cell.currency != own.currency:
        cell.reason = "currency_mismatch"
    elif "unknown" not in (cell.vat_mode, own.vat_mode) and cell.vat_mode != own.vat_mode:
        cell.reason = "vat_mismatch"
    else:
        cell.comparable = True
        cell.vat_unverified = "unknown" in (cell.vat_mode, own.vat_mode)
        cell.diff_abs = (price - own.price).quantize(Decimal("0.01"))
        cell.diff_pct = _pct(cell.diff_abs, own.price)
    return cell


def build(db: Session, market: str | None = None, competitor_id: int | None = None,
          q: str | None = None) -> list[ComparisonRow]:
    """One row per (our product, market) that has at least one assigned, non-archived offer."""
    query = (select(CompetitorProduct, Competitor, CurrentPrice)
             .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
             .outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id)
             .where(CompetitorProduct.product_id.is_not(None), CompetitorProduct.status != "archived",
                    Competitor.status != "archived"))
    if competitor_id is not None:
        query = query.where(Competitor.id == competitor_id)
    products = {p.id: p for p in db.scalars(select(Product))}
    rows: dict[tuple[int, str], ComparisonRow] = {}
    for offer, competitor, current in db.execute(query.order_by(Competitor.name, CompetitorProduct.id)):
        product = products.get(offer.product_id)
        offer_market = competitor.market or competitor.country or "?"
        if product is None or (market and offer_market != market):
            continue
        if q and q.lower() not in product.name.lower():
            continue
        own = next((p for p in product.prices if p.market == offer_market), None)
        key = (product.id, offer_market)
        if key not in rows:
            rows[key] = ComparisonRow(product_id=product.id, product_name=product.name, priority=product.priority,
                                      market=offer_market, own_price=own.price if own else None,
                                      own_currency=own.currency if own else None,
                                      own_vat_mode=own.vat_mode if own else None)
        rows[key].offers.append(_cell(offer, competitor, current, own))

    for row in rows.values():
        comparable = [c for c in row.offers if c.comparable]
        prices = [c.price for c in comparable]
        row.min_price, row.max_price = (min(prices), max(prices)) if prices else (None, None)
        row.cheaper_count = sum(1 for c in comparable if c.diff_abs < 0)
        if comparable:
            row.position = "not_cheapest" if row.cheaper_count else "cheapest"
            row.max_undercut_pct = min(c.diff_pct for c in comparable)
    return sorted(rows.values(), key=lambda r: (r.product_name.lower(), r.market))


def sort_rows(rows: list[ComparisonRow], sort: str) -> list[ComparisonRow]:
    if sort == "undercut":  # competitor cheapest by the largest margin first
        return sorted(rows, key=lambda r: (r.max_undercut_pct is None, r.max_undercut_pct or 0))
    if sort == "-name":
        return list(reversed(rows))
    return rows
