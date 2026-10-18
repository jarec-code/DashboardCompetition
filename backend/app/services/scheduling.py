"""Update intervals. The scheduler itself comes in stage 5; this module already decides staleness."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AppSetting, Competitor, CompetitorProduct, Product

DEFAULT_PRIORITY_HOURS = {"strategic": 8, "regular": 24, "stable": 120}
DEFAULT_HOURS = 24
PRIORITY_SETTING_KEY = "priority_interval_hours"


def priority_hours(db: Session) -> dict[str, int]:
    setting = db.get(AppSetting, PRIORITY_SETTING_KEY)
    return {**DEFAULT_PRIORITY_HOURS, **(setting.value if setting and isinstance(setting.value, dict) else {})}


def interval_hours(db: Session, offer: CompetitorProduct, competitor: Competitor) -> int:
    """Offer's own frequency, else its product's priority, else the competitor default, else 24 h."""
    if offer.custom_frequency_hours:
        return offer.custom_frequency_hours
    if offer.product_id:
        product = db.get(Product, offer.product_id)
        if product is not None:
            return priority_hours(db).get(product.priority, DEFAULT_HOURS)
    return competitor.default_frequency_hours or DEFAULT_HOURS


def is_stale(last_success_at: datetime | None, interval: int, now: datetime) -> bool:
    """A price is stale when the last successful check is older than twice the update interval."""
    return last_success_at is None or now - last_success_at > timedelta(hours=2 * interval)
