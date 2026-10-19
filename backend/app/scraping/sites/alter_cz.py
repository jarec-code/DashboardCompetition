"""Parser for www.alter.cz (Nakladatelství ALTER, Drupal 10). See docs/IMPLEMENTATION_PLAN.md, section 6.1.

Product page layout (checked on saved samples in tests/fixtures/html/alter_cz):
  - title in <h1>
  - one price block per variant: <h2>variant name</h2> ... <div class="vatPricePD">44,00 <span>Kč</span></div>
    variants are e.g. "tištěná" (printed) and "elektronická — pro školy"
  - details: <span class="detail_key">Dostupnost:</span> <span class="detail_value">Skladem</span>
  - EAN only in the description text: "EAN: 8594165541796"

Listing /shop (?page=0…, 24 cards per page, rel="next" pagination), used by automatic discovery:
  - one card per product group: <div class="… views-row"> with <h3 class="mainHeigh">title</h3>,
    <h3 class="categoryH">… 1. ročník</h3> and <span class="priceNum">65</span> (printed title, CZK)
  - the card links only to the group (/cart/add_dialog/277), not to the product page /produkt/716,
    so a group is identified as "polozka:277" and its price is read from the listing itself.
"""
import re

from bs4 import BeautifulSoup

from app.scraping.normalize import detect_currency, find_identifiers, normalize_availability, parse_price
from app.scraping.parsers.base import Extracted, PageContext, ParseError

_GROUP = re.compile(r"/cart/add_dialog/(\d+)")

VERSION = 1
DEFAULT_VARIANT = "tištěná"


def _variants(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """(variant name, price text) for each price block on the page."""
    found = []
    for price in soup.select(".vatPricePD"):
        heading = price.find_previous("h2")
        found.append((heading.get_text(" ", strip=True) if heading else "", price.get_text(" ", strip=True)))
    return found


def _pick(variants: list[tuple[str, str]], requested: str) -> tuple[str, str]:
    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    if requested:
        for name, price in variants:
            if norm(name).startswith(norm(requested)):
                return name, price
        names = ", ".join(n for n, _ in variants) or "žiadne"
        raise ParseError(f"Variant „{requested}“ sa na stránke nenašiel. Dostupné varianty: {names}.")
    for name, price in variants:
        if norm(name).startswith(DEFAULT_VARIANT):
            return name, price
    return variants[0]


def parse(ctx: PageContext) -> Extracted:
    soup = BeautifulSoup(ctx.html, "html.parser")
    title = soup.select_one("h1")
    variants = _variants(soup)
    if not variants or title is None:
        raise ParseError("Na stránke alter.cz sa nenašiel názov alebo cena (blok .vatPricePD). "
                         "Web mohol zmeniť štruktúru, je potrebné upraviť parser alter_cz.")
    variant, price_text = _pick(variants, ctx.variant)
    price = parse_price(price_text)
    if price is None:
        raise ParseError(f"Cenu variantu „{variant}“ sa nepodarilo prečítať: {price_text!r}.")

    availability = "unknown"
    for key in soup.select(".detail_key"):
        if key.get_text(strip=True).lower().startswith("dostupnost"):
            value = key.find_next_sibling(class_="detail_value")
            availability = normalize_availability(value.get_text(" ", strip=True) if value else None)
            break
    if availability == "unknown" and variant.lower().startswith("elektronick"):
        availability = "in_stock"  # licences are always available; the page has no stock field for them

    return Extracted(
        price=price,
        currency=detect_currency(price_text) or "CZK",
        availability=availability,
        title=title.get_text(" ", strip=True),
        identifiers=find_identifiers(soup.get_text(" ")),
        variant=variant,
        available_variants=[name for name, _ in variants],
        source="alter_cz",
    )


def listing_items(soup: BeautifulSoup, page_url: str) -> list:
    """Product groups on one page of the /shop listing, with title, grade and price."""
    from app.scraping.catalog import FoundItem

    base = page_url.split("/shop")[0]
    items = []
    for card in soup.select("div.views-row"):
        link = card.find("a", href=_GROUP)
        title = card.select_one("h3.mainHeigh")
        if link is None or title is None:
            continue
        group = _GROUP.search(link["href"]).group(1)
        grade = card.select_one("h3.categoryH")
        price = card.select_one(".priceNum")
        hints = {"grade": " ".join(grade.get_text(" ", strip=True).split())} if grade else {}
        value = parse_price(price.get_text(strip=True)) if price else None
        items.append(FoundItem(key=f"polozka:{group}", url=f"{base}/shop#polozka-{group}",
                               title=" ".join(title.get_text(" ", strip=True).split()),
                               price=value, currency="CZK" if value else None, price_from_listing=value is not None,
                               hints=hints))
    return items
