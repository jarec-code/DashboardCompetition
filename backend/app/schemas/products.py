import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.identifiers import normalize_ean, normalize_isbn, normalize_sku

Priority = Literal["strategic", "regular", "stable"]
TrackingStatus = Literal["active", "paused"]
VatMode = Literal["with_vat", "without_vat", "unknown"]

_MARKET_RE = re.compile(r"^[A-Z0-9_-]{1,16}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
# Default currency per market and market per currency (EUR is Slovakia for Taktik).
MARKET_CURRENCY = {"SK": "EUR", "CZ": "CZK", "PL": "PLN", "HU": "HUF", "AT": "EUR", "DE": "EUR"}
CURRENCY_MARKET = {"EUR": "SK", "CZK": "CZ", "PLN": "PL", "HUF": "HU"}


def _text(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


def _isbn(value: str | None) -> str | None:
    if not _text(value):
        return None
    isbn = normalize_isbn(value)
    if isbn is None:
        raise ValueError("Neplatné ISBN (skontrolujte číslice a kontrolnú číslicu).")
    return isbn


def _ean(value: str | None) -> str | None:
    if not _text(value):
        return None
    ean = normalize_ean(value)
    if ean is None:
        raise ValueError("Neplatný EAN (skontrolujte číslice a kontrolnú číslicu).")
    return ean


class ProductPriceIn(BaseModel):
    market: str
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str | None = None  # defaults by market
    vat_mode: VatMode = "with_vat"

    @field_validator("market")
    @classmethod
    def market_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not _MARKET_RE.match(value):
            raise ValueError("Neplatný kód trhu (napr. SK, CZ).")
        return value

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str | None) -> str | None:
        if not _text(value):
            return None
        value = value.strip().upper()
        if not _CURRENCY_RE.match(value):
            raise ValueError("Mena musí byť trojpísmenový kód (EUR, CZK).")
        return value

    @field_validator("price")
    @classmethod
    def cents(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    def resolved_currency(self) -> str:
        currency = self.currency or MARKET_CURRENCY.get(self.market)
        if not currency:
            raise ValueError(f"Zadajte menu pre trh {self.market}.")
        return currency


class ProductAttributes(BaseModel):
    """Parameters that help matching comparable titles across publishers."""
    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, max_length=100)  # predmet
    grade: str | None = Field(default=None, max_length=20)     # ročník
    type: str | None = Field(default=None, max_length=100)     # učebnica, pracovný zošit…

    _t = field_validator("subject", "grade", "type")(_text)


class ProductFields(BaseModel):
    internal_id: str | None = Field(default=None, max_length=64)
    isbn: str | None = Field(default=None, max_length=32)
    ean: str | None = Field(default=None, max_length=32)
    sku: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    attributes: ProductAttributes | None = None
    priority: Priority | None = None
    tracking_status: TrackingStatus | None = None
    prices: list[ProductPriceIn] | None = Field(default=None, max_length=20)

    _t = field_validator("internal_id", "brand", "category")(_text)
    _i = field_validator("isbn")(_isbn)
    _e = field_validator("ean")(_ean)
    _s = field_validator("sku")(lambda v: normalize_sku(v))

    @field_validator("prices")
    @classmethod
    def one_price_per_market(cls, prices: list[ProductPriceIn] | None) -> list[ProductPriceIn] | None:
        if prices:
            markets = [p.market for p in prices]
            if len(markets) != len(set(markets)):
                raise ValueError("Každý trh môže mať iba jednu cenu.")
            for p in prices:
                p.currency = p.resolved_currency()
        return prices


class ProductCreate(ProductFields):
    name: str = Field(min_length=1, max_length=500)
    priority: Priority = "regular"
    tracking_status: TrackingStatus = "active"
    prices: list[ProductPriceIn] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Zadajte názov produktu.")
        return value.strip()


class ProductUpdate(ProductFields):
    """Partial update; `prices`, when sent, replaces all prices of the product."""
    name: str | None = Field(default=None, min_length=1, max_length=500)


class ProductPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    market: str
    price: Decimal
    currency: str
    vat_mode: str


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    internal_id: str | None
    name: str
    isbn: str | None
    ean: str | None
    sku: str | None
    brand: str | None
    category: str | None
    attributes: dict
    priority: str
    tracking_status: str
    prices: list[ProductPriceOut]
    offer_count: int = 0
    created_at: datetime
    updated_at: datetime


class ImportRowIssue(BaseModel):
    row: int
    reason: str


class ImportResult(BaseModel):
    imported: int
    duplicates: list[ImportRowIssue]
    errors: list[ImportRowIssue]
