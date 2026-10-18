from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ParseError(Exception):
    """The page was fetched but the expected data was not found (e.g. the site changed its layout)."""


@dataclass
class Extracted:
    price: Decimal | None = None
    currency: str | None = None
    original_price: Decimal | None = None
    availability: str = "unknown"
    vat_mode: str = "unknown"
    title: str | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    variant: str | None = None
    available_variants: list[str] = field(default_factory=list)
    source: str = ""  # which strategy produced the data, e.g. "jsonld"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("price", "original_price"):
            data[key] = str(data[key]) if data[key] is not None else None
        return data


@dataclass
class PageContext:
    html: str
    url: str
    config: dict[str, Any]
    variant: str = ""  # requested variant of the offer ("" = default)


ParseFn = Callable[[PageContext], Extracted]


@dataclass
class ParserSpec:
    """One registered parser. `config_model` validates its settings (None = no settings)."""
    name: str
    label: str
    description: str
    version: int
    parse: ParseFn
    config_model: type[BaseModel] | None = None
    config_kind: str = "none"  # tells the UI which form to show: none, css, xpath

    @property
    def code_version(self) -> str:
        return f"{self.name}@{self.version}"
