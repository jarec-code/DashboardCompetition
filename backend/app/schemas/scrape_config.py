"""Scrape configuration and parser settings.

parser_config is plain data validated by the chosen parser's own schema (see
app/scraping/parsers/registry.py). Users can never submit code; parsers for specific websites are
Python modules written by developers and only selected by name.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.scraping.parsers.registry import PARSERS, validate_parser_config

VatMode = Literal["with_vat", "without_vat", "unknown"]  # same values as models.base.VAT_MODES


class ScrapeConfigIn(BaseModel):
    source_type: Literal["api", "feed", "html"] = "html"
    parser_type: str = Field(default="auto", max_length=64)
    parser_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    max_retries: int = Field(default=3, ge=0, le=5)
    min_interval_seconds: int = Field(default=5, ge=1, le=3600)
    prices_vat_mode: VatMode = "unknown"
    enabled: bool = True

    @field_validator("parser_type")
    @classmethod
    def known_parser(cls, value: str) -> str:
        if value not in PARSERS:
            raise ValueError(f"Neznámy typ parsera: {value}")
        return value

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


class ParserOut(BaseModel):
    name: str
    label: str
    description: str
    version: int
    config_kind: str
