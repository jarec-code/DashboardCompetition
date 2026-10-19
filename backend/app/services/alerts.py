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
from sqlalchemy import or_, select
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


def _rules_for(db: Session, offer: CompetitorProduct) -> list[AlertRule]:
    return list(db.scalars(select(AlertRule).where(
        AlertRule.enabled.is_(True),
        or_(AlertRule.competitor_id.is_(None), AlertRule.competitor_id == offer.competitor_id),
        or_(AlertRule.product_id.is_(None), AlertRule.product_id == offer.product_id))))


def _resolve(db: Session, rule_id: int, offer_id: int, now: datetime) -> None:
    for alert in db.scalars(select(Alert).where(Alert.rule_id == rule_id, Alert.competitor_product_id == offer_id,
                                                Alert.status.in_(("open", "acknowledged")))):
        alert.status, alert.resolved_at = "resolved", now


def evaluate_offer(db: Session, offer: CompetitorProduct, event: Event | None = None,
                   now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    competitor = db.get(Competitor, offer.competitor_id)
    inactive = offer.status == "archived" or competitor is None or competitor.status == "archived"
    product = db.get(Product, offer.product_id) if offer.product_id else None
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    for rule in _rules_for(db, offer):
        verdict = Verdict(False) if inactive else _check(rule, offer, competitor, product, current, event)
        if verdict.active is True:
            stmt = insert(Alert).values(rule_id=rule.id, competitor_product_id=offer.id, alert_type=rule.rule_type,
                                        status="open", message=verdict.message[:500], details=verdict.details or {},
                                        last_seen_at=now, created_at=now, updated_at=now)
            db.execute(stmt.on_conflict_do_update(
                index_elements=["rule_id", "competitor_product_id"],
                index_where=Alert.status.in_(("open", "acknowledged")),
                set_={"message": stmt.excluded.message, "details": stmt.excluded.details, "last_seen_at": now,
                      "updated_at": now}))
        elif verdict.active is False and rule.rule_type not in EVENT_RULES:
            _resolve(db, rule.id, offer.id, now)
        elif inactive:
            _resolve(db, rule.id, offer.id, now)


def evaluate_all(db: Session) -> int:
    """Re-check condition rules for every offer (e.g. after our price changed, or periodically)."""
    offers = list(db.scalars(select(CompetitorProduct)))
    for offer in offers:
        evaluate_offer(db, offer)
    return len(offers)
