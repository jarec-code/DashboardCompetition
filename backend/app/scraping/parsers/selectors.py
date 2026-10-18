"""Parsers driven by user-configured CSS selectors or XPath expressions (data only, never code)."""
from typing import ClassVar

import soupsieve
from bs4 import BeautifulSoup
from lxml import etree
from lxml import html as lxml_html
from pydantic import BaseModel, ConfigDict, model_validator

from app.scraping.normalize import detect_currency, find_identifiers, normalize_availability, parse_price
from app.scraping.parsers.base import Extracted, PageContext, ParseError

VERSION = 1
MAX_EXPRESSION_LENGTH = 500
FIELDS = ("price", "name", "original_price", "currency", "availability", "identifier")


class _Selectors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: str
    name: str | None = None
    original_price: str | None = None
    currency: str | None = None
    availability: str | None = None
    identifier: str | None = None  # element containing EAN/ISBN/SKU
    kind: ClassVar[str] = "výraz"

    def _compile(self, expression: str) -> None:
        raise NotImplementedError

    @model_validator(mode="after")
    def check_expressions(self):
        for field, expression in self.model_dump(exclude_none=True).items():
            expression = expression.strip()
            if not expression:
                raise ValueError(f"Výraz '{field}' je prázdny.")
            if len(expression) > MAX_EXPRESSION_LENGTH:
                raise ValueError(f"Výraz '{field}' je príliš dlhý.")
            try:
                self._compile(expression)
            except Exception:
                raise ValueError(f"'{field}' nie je platný {self.kind}: {expression}") from None
            setattr(self, field, expression)
        return self


class CssSelectors(_Selectors):
    kind: ClassVar[str] = "CSS selektor"

    def _compile(self, expression: str) -> None:
        soupsieve.compile(expression)


class XPathExpressions(_Selectors):
    kind: ClassVar[str] = "XPath výraz"

    def _compile(self, expression: str) -> None:
        etree.XPath(expression)


def _build(ctx: PageContext, values: dict[str, str | None], source: str) -> Extracted:
    price = parse_price(values.get("price"))
    if price is None:
        raise ParseError(f"Cena sa nenašla ({source} pre cenu nenašiel platnú hodnotu; web mohol zmeniť štruktúru).")
    return Extracted(
        price=price,
        currency=detect_currency(values.get("currency")) or detect_currency(values.get("price")),
        original_price=parse_price(values.get("original_price")),
        availability=normalize_availability(values.get("availability")),
        title=(values.get("name") or "").strip() or None,
        identifiers=find_identifiers(values.get("identifier")) if values.get("identifier") else {},
        source=source,
    )


def parse_css(ctx: PageContext) -> Extracted:
    config = CssSelectors.model_validate(ctx.config)
    soup = BeautifulSoup(ctx.html, "html.parser")
    values = {}
    for field in FIELDS:
        selector = getattr(config, field)
        el = soup.select_one(selector) if selector else None
        values[field] = (el.get("content") or el.get_text(" ", strip=True)) if el is not None else None
    if values["identifier"]:
        # A bare code in its own element is fine here: the user pointed at it explicitly.
        values["identifier"] = f"EAN {values['identifier']}" if values["identifier"].replace(" ", "").isdigit() \
            else values["identifier"]
    return _build(ctx, values, "CSS selektor")


def _xpath_value(tree, expression: str) -> str | None:
    result = tree.xpath(expression)
    if isinstance(result, list):
        result = result[0] if result else None
    if result is None:
        return None
    if hasattr(result, "text_content"):
        return " ".join(result.text_content().split())
    return " ".join(str(result).split()) or None


def parse_xpath(ctx: PageContext) -> Extracted:
    config = XPathExpressions.model_validate(ctx.config)
    try:
        tree = lxml_html.fromstring(ctx.html)
    except (etree.ParserError, ValueError) as exc:
        raise ParseError(f"Stránku nebolo možné spracovať: {exc}") from None
    values = {f: _xpath_value(tree, getattr(config, f)) if getattr(config, f) else None for f in FIELDS}
    if values["identifier"] and values["identifier"].replace(" ", "").isdigit():
        values["identifier"] = f"EAN {values['identifier']}"
    return _build(ctx, values, "XPath")
