from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

OfferStatus = Literal["active", "paused", "archived"]
MAX_URLS_PER_REQUEST = 500


class OffersCreate(BaseModel):
    """One or more product page URLs of one competitor (e.g. pasted, one per line)."""
    urls: list[str] = Field(min_length=1, max_length=MAX_URLS_PER_REQUEST)
    variant: str = Field(default="", max_length=100)

    _v = field_validator("variant")(lambda v: v.strip())


class OfferUpdate(BaseModel):
    status: OfferStatus | None = None
    variant: str | None = Field(default=None, max_length=100)
    external_product_id: str | None = Field(default=None, max_length=128)
    custom_frequency_hours: int | None = Field(default=None, ge=1, le=720)


class AssignmentIn(BaseModel):
    action: Literal["assign", "ignore", "auto"]
    product_id: int | None = None


class ProductRef(BaseModel):
    id: int
    name: str


class CurrentPriceOut(BaseModel):
    price: Decimal | None
    original_price: Decimal | None
    currency: str | None
    availability: str
    vat_mode: str
    title: str | None
    last_checked_at: datetime | None
    last_success_at: datetime | None
    is_stale: bool
    last_error: str | None


class OfferOut(BaseModel):
    id: int
    competitor_id: int
    competitor_name: str
    product_url: str
    variant: str
    external_product_id: str | None
    product: ProductRef | None
    matching_method: str | None
    match_status: str
    match_score: Decimal | None
    price_source: str = "page"
    status: str
    custom_frequency_hours: int | None
    next_check_at: datetime | None
    current: CurrentPriceOut | None
    created_at: datetime
    updated_at: datetime


class InvalidUrl(BaseModel):
    url: str
    reason: str


class OffersCreateResult(BaseModel):
    created: list[OfferOut]
    duplicates: list[str]
    invalid: list[InvalidUrl]


class CandidateOut(BaseModel):
    product: ProductRef
    isbn: str | None
    ean: str | None
    score: float
    method: str


class OfferRemoveResult(BaseModel):
    result: Literal["deleted", "archived"]
