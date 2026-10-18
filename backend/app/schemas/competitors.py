import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.scrape_config import ScrapeConfigIn, ScrapeConfigOut
from app.services.urls import InvalidUrl, normalize_url

CompetitorStatus = Literal["active", "paused", "archived"]
CompetitorType = Literal["direct", "distributor", "marketplace", "other"]
SourceType = Literal["api", "feed", "html"]

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_MARKET_RE = re.compile(r"^[A-Z0-9_-]{1,16}$")


def _country(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().upper()
    if not _COUNTRY_RE.match(value):
        raise ValueError("Krajina musí byť dvojpísmenový kód (napr. SK, CZ).")
    return value


def _market(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().upper()
    if not _MARKET_RE.match(value):
        raise ValueError("Trh môže obsahovať iba písmená, číslice, '-' a '_' (napr. CZ).")
    return value


def _base_url(value: str) -> str:
    try:
        return normalize_url(value)
    except InvalidUrl as exc:
        raise ValueError(str(exc)) from None


def _text(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


class CompetitorBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(max_length=2048)
    country: str | None = None
    market: str | None = None
    competitor_type: CompetitorType = "direct"
    preferred_source: SourceType = "html"
    notes: str | None = Field(default=None, max_length=5000)
    status: CompetitorStatus = "active"
    default_frequency_hours: int | None = Field(default=None, ge=1, le=720)

    _name = field_validator("name")(lambda v: v.strip())
    _url = field_validator("base_url")(_base_url)
    _c = field_validator("country")(_country)
    _m = field_validator("market")(_market)
    _n = field_validator("notes")(_text)

    @model_validator(mode="after")
    def market_defaults_to_country(self):
        if self.market is None:
            self.market = self.country
        if not self.name:
            raise ValueError("Zadajte názov konkurenta.")
        return self


class CompetitorCreate(CompetitorBase):
    scrape_config: ScrapeConfigIn = Field(default_factory=ScrapeConfigIn)
    # A second competitor on an already registered domain needs an explicit reason.
    duplicate_domain_reason: str | None = Field(default=None, max_length=500)

    _r = field_validator("duplicate_domain_reason")(_text)


class CompetitorUpdate(BaseModel):
    """Partial update; only the fields sent are changed."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    country: str | None = None
    market: str | None = None
    competitor_type: CompetitorType | None = None
    preferred_source: SourceType | None = None
    notes: str | None = Field(default=None, max_length=5000)
    status: CompetitorStatus | None = None
    default_frequency_hours: int | None = Field(default=None, ge=1, le=720)
    duplicate_domain_reason: str | None = Field(default=None, max_length=500)

    _url = field_validator("base_url")(lambda v: _base_url(v) if v is not None else None)
    _c = field_validator("country")(_country)
    _m = field_validator("market")(_market)
    _n = field_validator("notes")(_text)
    _r = field_validator("duplicate_domain_reason")(_text)


class CompetitorListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_url: str
    domain: str
    country: str | None
    market: str | None
    competitor_type: str
    status: str
    default_frequency_hours: int | None
    offer_count: int
    last_success_at: datetime | None
    last_run_error_count: int
    updated_at: datetime


class CompetitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_url: str
    domain: str
    duplicate_domain_reason: str | None
    country: str | None
    market: str | None
    competitor_type: str
    preferred_source: str
    notes: str | None
    status: str
    default_frequency_hours: int | None
    created_at: datetime
    updated_at: datetime
    scrape_config: ScrapeConfigOut


class ScrapeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    total_count: int
    success_count: int
    failure_count: int
    price_change_count: int
    error_summary: str | None


class CompetitorFilters(BaseModel):
    countries: list[str]
