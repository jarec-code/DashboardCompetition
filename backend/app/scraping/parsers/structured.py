"""Parsers for structured product data that most shops publish for search engines (no configuration)."""
import json

from bs4 import BeautifulSoup

from app.scraping.normalize import detect_currency, find_identifiers, normalize_availability, parse_price
from app.scraping.parsers.base import Extracted, PageContext, ParseError
from app.services.identifiers import normalize_ean, normalize_isbn, normalize_sku

VERSION = 1


def _soup(ctx: PageContext) -> BeautifulSoup:
    return BeautifulSoup(ctx.html, "html.parser")


def _identifiers(data: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in ("gtin13", "gtin", "gtin14", "gtin12", "gtin8", "ean"):
        if (ean := normalize_ean(str(data.get(key) or ""))) and "ean" not in found:
            found["ean"] = ean
    if isbn := normalize_isbn(str(data.get("isbn") or "")) or (
            normalize_isbn(found["ean"]) if found.get("ean", "")[:3] in ("978", "979") else None):
        found["isbn"] = isbn
    if sku := normalize_sku(str(data.get("sku") or "")):
        found["sku"] = sku
    return found


# ---------- JSON-LD ----------

def _is_type(obj: dict, name: str) -> bool:
    t = obj.get("@type")
    return t == name or (isinstance(t, list) and name in t)


def _products(obj, found: list[dict]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _products(item, found)
    elif isinstance(obj, dict):
        if _is_type(obj, "Product") or _is_type(obj, "Book"):
            found.append(obj)
        for key in ("@graph", "mainEntity", "itemListElement", "item", "hasVariant", "workExample"):
            if key in obj:
                _products(obj[key], found)


def _offer_values(offers) -> Extracted:
    if isinstance(offers, list):
        offers = next((o for o in offers if isinstance(o, dict) and (o.get("price") or o.get("lowPrice")
                                                                     or o.get("priceSpecification"))), None)
    if isinstance(offers, dict) and _is_type(offers, "AggregateOffer") and isinstance(offers.get("offers"), list):
        offers = offers["offers"][0] if offers["offers"] else offers
    result = Extracted()
    if not isinstance(offers, dict):
        return result
    result.price = parse_price(offers.get("price") or offers.get("lowPrice"))
    result.currency = offers.get("priceCurrency")
    result.availability = normalize_availability(offers.get("availability"))
    specs = offers.get("priceSpecification")
    for spec in specs if isinstance(specs, list) else [specs] if isinstance(specs, dict) else []:
        kind = str(spec.get("priceType", "")).rsplit("/", 1)[-1]
        if kind in ("StrikethroughPrice", "ListPrice", "SRP"):
            result.original_price = parse_price(spec.get("price"))
        else:
            result.price = result.price or parse_price(spec.get("price"))
            result.currency = result.currency or spec.get("priceCurrency")
            if isinstance(spec.get("valueAddedTaxIncluded"), bool):
                result.vat_mode = "with_vat" if spec["valueAddedTaxIncluded"] else "without_vat"
    return result


def parse_jsonld(ctx: PageContext) -> Extracted:
    products: list[dict] = []
    for tag in _soup(ctx).find_all("script", type="application/ld+json"):
        try:
            _products(json.loads(tag.string or tag.get_text() or ""), products)
        except (ValueError, TypeError):
            continue
    for product in products:
        result = _offer_values(product.get("offers"))
        if result.price is None:
            continue
        result.title = str(product.get("name") or "").strip() or None
        result.identifiers = _identifiers(product)
        result.source = "jsonld"
        return result
    raise ParseError("Na stránke nie sú štruktúrované dáta JSON-LD s cenou produktu.")


# ---------- microdata ----------

def _prop(scope, name: str):
    el = scope.select_one(f'[itemprop="{name}"]')
    if el is None:
        return None
    return el.get("content") or el.get("href") or el.get_text(" ", strip=True)


def parse_microdata(ctx: PageContext) -> Extracted:
    for item in _soup(ctx).select('[itemtype*="schema.org/Product"], [itemtype*="schema.org/Book"]'):
        price_raw = _prop(item, "price") or _prop(item, "lowPrice")
        price = parse_price(price_raw)
        if price is None:
            continue
        ids = {k: _prop(item, k) for k in ("gtin13", "gtin", "isbn", "sku") if _prop(item, k)}
        return Extracted(price=price, currency=_prop(item, "priceCurrency") or detect_currency(price_raw),
                         availability=normalize_availability(_prop(item, "availability")),
                         title=_prop(item, "name"), identifiers=_identifiers(ids), source="microdata")
    raise ParseError("Na stránke nie sú microdata schema.org s cenou produktu.")


# ---------- Open Graph ----------

def parse_opengraph(ctx: PageContext) -> Extracted:
    soup = _soup(ctx)

    def meta(*names: str):
        for name in names:
            el = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if el and el.get("content"):
                return el["content"]
        return None

    price = parse_price(meta("product:price:amount", "og:price:amount"))
    if price is None:
        raise ParseError("Na stránke nie sú meta značky Open Graph s cenou produktu.")
    return Extracted(price=price, currency=meta("product:price:currency", "og:price:currency"),
                     availability=normalize_availability(meta("product:availability", "og:availability")),
                     title=meta("og:title"), identifiers=find_identifiers(meta("product:retailer_item_id") or ""),
                     source="opengraph")


def parse_auto(ctx: PageContext) -> Extracted:
    for parser in (parse_jsonld, parse_microdata, parse_opengraph):
        try:
            return parser(ctx)
        except ParseError:
            continue
    raise ParseError("Stránka neobsahuje štruktúrované dáta s cenou (JSON-LD, microdata ani Open Graph). "
                     "Nastavte CSS selektory alebo parser pre tento web.")
