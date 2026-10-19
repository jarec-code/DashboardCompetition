"""Products of SPN (spn.cz) in its e-shop www.naseucebnice.cz.

spn.cz only presents the publisher; it sells through www.naseucebnice.cz, a shop shared with other publishers
of the group (Fortuna, ČGS, Kartografie Praha). The shop's publisher page lists the products of one publisher,
rendered on the server (Next.js) with their data in the page:
    /nakladatelstvi?manufacturer__id__in=<publisher id>&limit=100&offset=0
    … {"dependency": {"meta": {"total_count": 355, "limit": 100, "offset": 0}, "objects": [
        {"code": "6207", "name": "Slabikář …", "page_path": "slabikar--…", "manufacturer": {"name": "SPN …"},
         "variants": [{"type": "physical", "ean": "9788072356935", "is_in_stock": true,
                       "price_with_vat": 7900, "original_price_with_vat": 7900, …}, {"type": "demo", …}]}, …]}}
Prices are in hellers; the printed ("physical") variant is the comparable one (others are licences of the
interactive textbook). The whole catalogue of SPN is 4 requests; prices come from this listing.
"""
import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

PUBLISHERS = {"spn.cz": "f83f3eed-294f-4572-a0ec-ed88d91c7ab1"}  # competitor domain -> publisher id in the shop
SHOP = "https://www.naseucebnice.cz"
LIMIT = 100
_PUSH = re.compile(r"self\.__next_f\.push\(\[1,")
_LISTING = re.compile(r'\{"dependency":\s*\{"meta"')


def start_urls() -> tuple[str, ...]:
    return tuple(f"{SHOP}/nakladatelstvi?manufacturer__id__in={pid}&limit={LIMIT}&offset=0"
                 for pid in PUBLISHERS.values())


def _payload(text: str) -> str:
    """The Next.js payload: the JavaScript strings pushed by the page, decoded (they are JSON strings)."""
    decoder, parts = json.JSONDecoder(), []
    for match in _PUSH.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.end())
        except ValueError:
            continue
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)


def _listing(text: str) -> dict[str, Any]:
    payload = _payload(text)
    match = _LISTING.search(payload)
    if match is None:
        raise ValueError("Stránka nakladateľstva neobsahuje zoznam produktov (zmenila sa štruktúra obchodu?).")
    try:
        data, _ = json.JSONDecoder().raw_decode(payload, match.start())
    except ValueError:
        raise ValueError("Zoznam produktov obchodu sa nedá prečítať.") from None
    return data["dependency"]


def _price(raw: Any) -> Decimal | None:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return None
    return (Decimal(str(raw)) / 100).quantize(Decimal("0.01"))


def read(text: str, page_url: str):
    from app.scraping.catalog import FoundItem, PageResult

    listing = _listing(text)
    meta, rows = listing.get("meta") or {}, listing.get("objects") or []
    items = []
    for row in rows:
        printed = next((v for v in row.get("variants") or [] if v.get("type") == "physical"), None)
        value = _price(printed.get("price_with_vat")) if printed else None
        if not row.get("page_path") or value is None:
            continue  # only licences of an interactive textbook, or free: not comparable
        hints: dict[str, Any] = {"availability": "in_stock" if printed.get("is_in_stock") else "out_of_stock",
                                 "format": "printed"}  # the price of the printed variant
        if row.get("code"):
            hints["code"] = str(row["code"])
        regular = _price(printed.get("original_price_with_vat"))
        if regular and regular != value:
            hints["regular_price"] = str(regular)
        items.append(FoundItem(key=f"naseucebnice:{row['page_path']}", url=f"{SHOP}/{row['page_path']}",
                               title=str(row.get("name") or "").strip() or None, price=value, currency="CZK",
                               price_from_listing=True,
                               identifiers={"ean": printed["ean"]} if printed.get("ean") else {}, hints=hints))
    next_urls = []
    offset, total = int(meta.get("offset") or 0), int(meta.get("total_count") or 0)
    if rows and offset + len(rows) < total:
        parts = urlsplit(page_url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        query.update(limit=str(LIMIT), offset=str(offset + len(rows)))
        next_urls.append(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), "")))
    return PageResult(items=items, next_urls=next_urls)


def description(html: str) -> str | None:
    """The text under "Popis produktu" of a product page (its meta description only repeats the title)."""
    from bs4 import BeautifulSoup

    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())
    start = text.rfind("Popis produktu")
    if start < 0:
        return None
    body = text[start:].split("Všechny varianty produktu", 1)[-1]
    for end in ("Parametry Počet stran", "Parametry ISBN", "Související produkty"):
        body = body.split(end, 1)[0]
    return body.strip() or None
