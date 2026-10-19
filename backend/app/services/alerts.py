"""Alert rules evaluated by plain code (assignment section 11; no AI decides anything here).

Two kinds of rules:
  event rules      price_drop: raised when a check records a drop larger than the threshold;
                   stays until a user resolves it (a later drop updates the same alert).
  condition rules  cheaper_than_us, unavailable, stale, scraper_failing: active while the condition
                   holds, resolved automatically when it stops holding.
At most one active alert exists per rule and offer (partial unique index).
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Alert, AlertRule, Competitor, CompetitorProduct, CurrentPrice, Product
from app.services import comparison

EVENT_RULES = {"price_drop"}


class PriceDropParams(BaseModel):
    percent: float = Field(default=10, gt=0, le=100)


class CheaperParams(BaseModel):
    percent: float = Field(default=0, ge=0, le=100)  # competitor cheaper by more than this


class FailingParams(BaseModel):
    count: int = Field(default=3, ge=1, le=50)


class NoParams(BaseModel):
    pass


PARAMS: dict[str, type[BaseModel]] = {"price_drop": PriceDropParams, "cheaper_than_us": CheaperParams,
                                      "unavailable": NoParams, "stale": NoParams, "scraper_failing": FailingParams}


def validate_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    return PARAMS[rule_type].model_validate(params).model_dump()


@dataclass
class Verdict:
    active: bool | None  # True raise/update, False resolve, None leave as is
    message: str = ""
    details: dict[str, Any] | None = None


@dataclass
class Event:
    change_types: list[str]
    previous_price: Decimal | None


def _money(value: Decimal | None, currency: str | None) -> str:
    return f"{value:.2f} {currency or ''}".strip() if value is not None else "?"


def _check(rule: AlertRule, offer: CompetitorProduct, competitor: Competitor, product: Product | None,
           current: CurrentPrice | None, event: Event | None) -> Verdict:
    params = rule.params or {}
    kind = rule.rule_type
    if kind == "price_drop":
        if not event or "price_down" not in event.change_types or not event.previous_price or not current:
            return Verdict(None)
        drop = (event.previous_price - current.price) / event.previous_price * 100
        if drop < Decimal(str(params.get("percent", 10))):
            return Verdict(None)
        return Verdict(True, f"Cena klesla o {drop:.1f} % ({_money(event.previous_price, current.currency)} → "
                             f"{_money(current.price, current.currency)}).",
                       {"previous_price": str(event.previous_price), "price": str(current.price),
                        "drop_percent": f"{drop:.1f}"})
    if kind == "cheaper_than_us":
        if product is None or current is None or current.price is None:
            return Verdict(False)
        market = competitor.market or competitor.country
        own = next((p for p in product.prices if p.market == market), None)
        cell = comparison._cell(offer, competitor, current, own)
        if not cell.comparable or cell.diff_pct is None:
            return Verdict(False)
        if -cell.diff_pct <= Decimal(str(params.get("percent", 0))) or cell.diff_abs >= 0:
            return Verdict(False)
        return Verdict(True, f"Konkurent je lacnejší o {-cell.diff_pct} % ({_money(current.price, current.currency)} "
                             f"oproti vašim {_money(own.price, own.currency)}).",
                       {"price": str(current.price), "own_price": str(own.price), "diff_percent": str(cell.diff_pct)})
    if kind == "unavailable":
        if current is None or current.availability == "unknown":
            return Verdict(None)
        if current.availability == "out_of_stock":
            return Verdict(True, "Produkt je u konkurenta nedostupný.", {})
        return Verdict(False)
    if kind == "stale":
        if current is not None and current.is_stale:
            when = current.last_success_at.isoformat(timespec="minutes") if current.last_success_at else None
            return Verdict(True, "Cenu sa nepodarilo aktualizovať v požadovanom intervale.",
                           {"last_success_at": when})
        return Verdict(False)
    if kind == "scraper_failing":
        failures = current.consecutive_failures if current else 0
        if failures >= int(params.get("count", 3)):
            return Verdict(True, f"Kontrola zlyhala {failures}× za sebou: {current.last_error or 'neznáma chyba'}",
                           {"consecutive_failures": failures, "last_error": current.last_error})
        return Verdict(False)
    return Verdict(None)


def _applies(rule: AlertRule, offer: CompetitorProduct) -> bool:
    return (rule.competitor_id in (None, offer.competitor_id)) and (rule.product_id in (None, offer.product_id))


@dataclass
class _Context:
    """Everything evaluation needs, loaded once (a single offer, or all offers in bulk)."""
    rules: list[AlertRule]
    competitors: dict[int, Competitor]
    products: dict[int, Product]
    currents: dict[int, CurrentPrice]
    active: dict[tuple[int, int], Alert]  # (rule_id, offer_id) -> open or acknowledged alert


def _load(db: Session, offer_ids: list[int] | None) -> _Context:
    def scoped(query, column):
        return query if offer_ids is None else query.where(column.in_(offer_ids))
    rules = list(db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))))
    # also alerts of disabled rules are irrelevant here: disabling resolves them (see the API)
    active = scoped(select(Alert).where(Alert.status.in_(("open", "acknowledged"))), Alert.competitor_product_id)
    currents = scoped(select(CurrentPrice), CurrentPrice.competitor_product_id)
    competitors, products = select(Competitor), select(Product)
    if offer_ids is not None:  # one check: only the rows this offer needs, not the whole catalogue
        offers = select(CompetitorProduct.competitor_id, CompetitorProduct.product_id).where(
            CompetitorProduct.id.in_(offer_ids)).subquery()
        competitors = competitors.where(Competitor.id.in_(select(offers.c.competitor_id)))
        products = products.where(Product.id.in_(select(offers.c.product_id)))
    return _Context(
        rules=rules,
        competitors={c.id: c for c in db.scalars(competitors)},
        products={p.id: p for p in db.scalars(products)},
        currents={c.competitor_product_id: c for c in db.scalars(currents)},
        active={(a.rule_id, a.competitor_product_id): a for a in db.scalars(active)},
    )


def _evaluate(db: Session, ctx: _Context, offer: CompetitorProduct, event: Event | None, now: datetime) -> None:
    competitor = ctx.competitors.get(offer.competitor_id)
    inactive = offer.status == "archived" or competitor is None or competitor.status == "archived"
    product = ctx.products.get(offer.product_id) if offer.product_id else None
    current = ctx.currents.get(offer.id)
    for rule in ctx.rules:
        if not _applies(rule, offer):
            continue
        existing = ctx.active.get((rule.id, offer.id))
        verdict = Verdict(False) if inactive else _check(rule, offer, competitor, product, current, event)
        if verdict.active is True:
            if existing is not None:  # update in place (no duplicate)
                existing.message, existing.details = verdict.message[:500], verdict.details or {}
                existing.last_seen_at = existing.updated_at = now
                continue
            stmt = insert(Alert).values(rule_id=rule.id, competitor_product_id=offer.id, alert_type=rule.rule_type,
                                        status="open", message=verdict.message[:500], details=verdict.details or {},
                                        last_seen_at=now, created_at=now, updated_at=now)
            db.execute(stmt.on_conflict_do_update(  # race-safe if another process raised it meanwhile
                index_elements=["rule_id", "competitor_product_id"],
                # Literal predicate: with a bound parameter PostgreSQL cannot match the partial index once
                # psycopg switches to a prepared statement (from the 5th execution on one connection).
                index_where=text("status IN ('open', 'acknowledged')"),
                set_={"message": stmt.excluded.message, "details": stmt.excluded.details, "last_seen_at": now,
                      "updated_at": now}))
        elif existing is not None and (inactive or (verdict.active is False and rule.rule_type not in EVENT_RULES)):
            existing.status, existing.resolved_at = "resolved", now


def evaluate_offer(db: Session, offer: CompetitorProduct, event: Event | None = None,
                   now: datetime | None = None) -> None:
    db.flush()
    _evaluate(db, _load(db, [offer.id]), offer, event, now or datetime.now(UTC))


def evaluate_all(db: Session) -> int:
    """Re-check condition rules for every offer (periodically, or after our price changed). Loads in bulk."""
    ctx = _load(db, None)
    now = datetime.now(UTC)
    offers = list(db.scalars(select(CompetitorProduct)))
    for offer in offers:
        _evaluate(db, ctx, offer, None, now)
    return len(offers)
