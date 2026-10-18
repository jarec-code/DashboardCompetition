"""Scrape configuration and parser settings.

parser_config is plain data validated per parser type. Users can never submit code; parsers for
specific websites are Python modules written by developers and only selected by name (stage 4).
"""
from datetime import datetime
from typing import Any, Literal

import soupsieve
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Parser types available now. Site-specific parsers (e.g. alter.cz) are added with the scraping engine.
GENERIC_PARSERS = ("auto", "jsonld", "microdata", "opengraph")
ParserType = Literal["auto", "jsonld", "microdata", "opengraph", "css"]
VatMode = Literal["with_vat", "without_vat", "unknown"]  # same values as models.base.VAT_MODES

MAX_SELECTOR_LENGTH = 500


class CssSelectors(BaseModel):
    """CSS selectors for one product page. Only `price` is required."""
    model_config = ConfigDict(extra="forbid")

    price: str
    name: str | None = None
    original_price: str | None = None
    currency: str | None = None
    availability: str | None = None
    identifier: str | None = None  # element containing EAN/ISBN/SKU

    @model_validator(mode="after")
    def selectors_compile(self) -> "CssSelectors":
        for field, selector in self.model_dump(exclude_none=True).items():
            selector = selector.strip()
            if not selector:
                raise ValueError(f"Selektor '{field}' je prázdny.")
            if len(selector) > MAX_SELECTOR_LENGTH:
                raise ValueError(f"Selektor '{field}' je príliš dlhý.")
            try:
                soupsieve.compile(selector)
            except Exception:
                raise ValueError(f"Selektor '{field}' nie je platný CSS selektor: {selector}") from None
            setattr(self, field, selector)
        return self


def validate_parser_config(parser_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if parser_type in GENERIC_PARSERS:
        if config:
            raise ValueError(f"Parser '{parser_type}' nemá žiadne nastavenia.")
        return {}
    if parser_type == "css":
        return CssSelectors.model_validate(config).model_dump(exclude_none=True)
    raise ValueError(f"Neznámy typ parsera: {parser_type}")


class ScrapeConfigIn(BaseModel):
    source_type: Literal["api", "feed", "html"] = "html"
    parser_type: ParserType = "auto"
    parser_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    max_retries: int = Field(default=3, ge=0, le=5)
    min_interval_seconds: int = Field(default=5, ge=1, le=3600)
    prices_vat_mode: VatMode = "unknown"
    enabled: bool = True

    @model_validator(mode="after")
    def check_parser_config(self) -> "ScrapeConfigIn":
        self.parser_config = validate_parser_config(self.parser_type, self.parser_config)
        return self


class ScrapeConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: str
    parser_type: str
    parser_config: dict[str, Any]
    parser_version: int
    timeout_seconds: int
    max_concurrency: int
    max_retries: int
    min_interval_seconds: int
    prices_vat_mode: str
    enabled: bool
    last_test_status: str | None
    last_test_at: datetime | None
    updated_at: datetime
