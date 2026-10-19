"""Recording scrape results: the current state of an offer and the history of real changes.

Rules (docs/IMPLEMENTATION_PLAN.md, section 3.1):
  - the first successful observation and every real change (price, original price, currency,
    availability) create one price_history row; an unchanged check does not,
  - last_checked_at is updated on every check, last_success_at only on success,
  - a failed check never overwrites the last valid price; it records the error and staleness.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Competitor, CompetitorProduct, CurrentPrice, PriceHistory
from app.scraping.parsers.base import Extracted
from app.services import scheduling


@dataclass
class Outcome:
    changed: bool
    change_types: list[str]
    previous_price: Decimal | None = None


def _locked_current(db: Session, offer_id: int) -> CurrentPrice:
    # Create the row if missing (race-safe), then lock it for this transaction.
    db.execute(insert(CurrentPrice).values(competitor_product_id=offer_id).on_conflict_do_nothing(
        index_elements=["competitor_product_id"]))
    return db.scalars(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer_id)
                      .with_for_update()).one()


def _changes(current: CurrentPrice, data: Extracted) -> list[str]:
    if current.last_success_at is None:
        return ["initial"]
    changes = []
    if data.price != current.price:
        changes.append("price_up" if current.price is not None and data.price > current.price else "price_down")
    if data.currency != current.currency:
        changes.append("currency")
    if data.availability != current.availability:
        changes.append("availability")
    if data.original_price != current.original_price:
        changes.append("original_price")
    return changes


def record_success(db: Session, offer: CompetitorProduct, data: Extracted, observed_at: datetime,
                   default_vat_mode: str = "unknown", etag: str | None = None,
                   last_modified: str | None = None) -> Outcome:
    if data.price is None:
        raise ValueError("record_success needs a price; a missing price is a parse error")
    current = _locked_current(db, offer.id)
    vat_mode = data.vat_mode if data.vat_mode != "unknown" else default_vat_mode
    previous_price = current.price
    change_types = _changes(current, data)
    if change_types:
        db.add(PriceHistory(competitor_product_id=offer.id, price=data.price, original_price=data.original_price,
                            currency=data.currency, availability=data.availability, vat_mode=vat_mode,
                            observed_at=observed_at, change_types=change_types))
    current.price, current.original_price, current.currency = data.price, data.original_price, data.currency
    current.availability, current.vat_mode = data.availability, vat_mode
    current.title = data.title or current.title
    current.found_identifiers = data.identifiers or current.found_identifiers or {}
    current.last_checked_at = current.last_success_at = observed_at
    current.is_stale, current.last_error, current.consecutive_failures = False, None, 0
    current.etag, current.last_modified = etag, last_modified
    current.updated_at = observed_at
    return Outcome(changed=bool(change_types), change_types=change_types, previous_price=previous_price)


def record_not_modified(db: Session, offer: CompetitorProduct, observed_at: datetime) -> Outcome:
    """HTTP 304: the page did not change since the last successful check."""
    current = _locked_current(db, offer.id)
    current.last_checked_at = current.last_success_at = observed_at
    current.is_stale, current.last_error, current.consecutive_failures = False, None, 0
    current.updated_at = observed_at
    return Outcome(changed=False, change_types=[])


def record_failure(db: Session, offer: CompetitorProduct, competitor: Competitor, error: str,
                   observed_at: datetime, final: bool = True) -> None:
    """`final=False` for an attempt that will be retried: it does not count as a failed check yet."""
    current = _locked_current(db, offer.id)
    current.last_checked_at = observed_at
    current.last_error = error[:2000]
    if final:
        current.consecutive_failures += 1
    current.is_stale = scheduling.is_stale(current.last_success_at,
                                           scheduling.interval_hours(db, offer, competitor), observed_at)
    current.updated_at = observed_at
