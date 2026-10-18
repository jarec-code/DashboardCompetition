"""Update intervals, scheduling settings and rescheduling after frequency changes.

All stored times are UTC. The optional scheduling window (e.g. only at night) is given in local
hours of the configured time zone (TIMEZONE, default Europe/Bratislava).
"""
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting, Competitor, CompetitorProduct, CurrentPrice, Product

DEFAULT_HOURS = 24
SETTINGS_KEY = "scheduling"
JITTER = 0.05  # +-5 % so that offers checked together drift apart over time


class PriorityHours(BaseModel):
    strategic: int = Field(default=8, ge=1, le=720)
    regular: int = Field(default=24, ge=1, le=720)
    stable: int = Field(default=120, ge=1, le=720)


class SchedulingSettings(BaseModel):
    scheduler_enabled: bool = True
    priority_interval_hours: PriorityHours = Field(default_factory=PriorityHours)
    # Local hours [start, end) in which automatic checks may start; None = any time. May wrap midnight.
    window_start_hour: int | None = Field(default=None, ge=0, le=23)
    window_end_hour: int | None = Field(default=None, ge=0, le=23)
    batch_size: int = Field(default=100, ge=1, le=1000)  # offers scheduled per scheduler tick

    @model_validator(mode="after")
    def window_complete(self) -> "SchedulingSettings":
        if (self.window_start_hour is None) != (self.window_end_hour is None):
            raise ValueError("Zadajte začiatok aj koniec časového okna, alebo ani jedno.")
        if self.window_start_hour is not None and self.window_start_hour == self.window_end_hour:
            raise ValueError("Začiatok a koniec časového okna sa nesmú rovnať.")
        return self


def load_settings(db: Session) -> SchedulingSettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    return SchedulingSettings.model_validate(row.value) if row and isinstance(row.value, dict) else SchedulingSettings()


def save_settings(db: Session, settings: SchedulingSettings) -> None:
    value = settings.model_dump()
    db.execute(insert(AppSetting).values(key=SETTINGS_KEY, value=value).on_conflict_do_update(
        index_elements=["key"], set_={"value": value, "updated_at": datetime.now(UTC)}))


def set_heartbeat(db: Session, name: str, at: datetime) -> None:
    db.execute(insert(AppSetting).values(key=f"{name}_heartbeat", value=at.isoformat()).on_conflict_do_update(
        index_elements=["key"], set_={"value": at.isoformat(), "updated_at": at}))


def get_heartbeat(db: Session, name: str) -> datetime | None:
    row = db.get(AppSetting, f"{name}_heartbeat")
    return datetime.fromisoformat(row.value) if row and isinstance(row.value, str) else None


def in_window(settings: SchedulingSettings, now: datetime) -> bool:
    if settings.window_start_hour is None:
        return True
    hour = now.astimezone(ZoneInfo(get_settings().timezone)).hour
    start, end = settings.window_start_hour, settings.window_end_hour
    return start <= hour < end if start < end else hour >= start or hour < end


def interval_hours(db: Session, offer: CompetitorProduct, competitor: Competitor,
                   settings: SchedulingSettings | None = None) -> int:
    """Offer's own frequency, else its product's priority, else the competitor default, else 24 h."""
    if offer.custom_frequency_hours:
        return offer.custom_frequency_hours
    if competitor.default_frequency_hours:
        return competitor.default_frequency_hours
    if offer.product_id:
        product = db.get(Product, offer.product_id)
        if product is not None:
            hours = (settings or load_settings(db)).priority_interval_hours
            return getattr(hours, product.priority, DEFAULT_HOURS)
    return DEFAULT_HOURS


def next_check(now: datetime, hours: int) -> datetime:
    return now + timedelta(hours=hours * (1 + random.uniform(-JITTER, JITTER)))


def is_stale(last_success_at: datetime | None, interval: int, now: datetime) -> bool:
    """A price is stale when the last successful check is older than twice the update interval."""
    return last_success_at is None or now - last_success_at > timedelta(hours=2 * interval)


def reschedule(db: Session, offers: Iterable[CompetitorProduct]) -> int:
    """Recompute next_check_at after a frequency change: last check + new interval (or now if overdue).
    Offers never checked stay due immediately."""
    now = datetime.now(UTC)
    settings = load_settings(db)
    count = 0
    for offer in offers:
        last = db.scalar(select(CurrentPrice.last_checked_at).where(CurrentPrice.competitor_product_id == offer.id))
        if last is None:
            continue
        competitor = db.get(Competitor, offer.competitor_id)
        offer.next_check_at = max(now, last + timedelta(hours=interval_hours(db, offer, competitor, settings)))
        count += 1
    return count
