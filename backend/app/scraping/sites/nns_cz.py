"""Product listing of www.nns.cz (Nová škola), read through the shop's own JSON interface.

The shop page /obchod/?item=nabidka draws its products in the browser from
    POST /obchod/api/inventory/{limit}/{offset}   body {"tags": [...], "query": "", "packages": false, "ids": []}
which answers a list of items: {"type": "item", "id": 1649, "name": "Zápis je tu", "code": "P-50", "price": 99,
"status": "skladem", "tag_matches": 1, …}. Tags come from GET /obchod/api/inventory/tags/filters:
category 5 = "Učebnice a sešity", grades 17 (preschool) and 7–15 (1st–9th grade). With two tags the interface
also returns items matching one of them; tag_matches == 2 are the items of the grade.

Discovery queues one address per grade, e.g. https://www.nns.cz/obchod/api/inventory/60/0?tags=5,7, and pages
through it by the offset. The tags are in the query of the queued address only (stored addresses keep their
query, not a fragment); the real request is a POST to the address without it.
The price is read from this listing (no product page needed); a person can open
the product at /obchod/index.php?item=nab_det&sort_id={id}. No cart, no login, no session.
"""
import json
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

LIMIT = 60
CATEGORY_TEXTBOOKS = 5
GRADES = {17: "předškolní vzdělávání", 7: "1. ročník", 8: "2. ročník", 9: "3. ročník", 10: "4. ročník",
          11: "5. ročník", 12: "6. ročník", 13: "7. ročník", 14: "8. ročník", 15: "9. ročník"}
AVAILABILITY = {"skladem": "in_stock", "doprodej": "in_stock", "novinka": "in_stock", "bonus": "in_stock",
                "vyprodano": "out_of_stock", "pripravujeme": "preorder"}


def start_urls() -> tuple[str, ...]:
    return tuple(f"/obchod/api/inventory/{LIMIT}/0?tags={CATEGORY_TEXTBOOKS},{grade}" for grade in GRADES)


def _parts(url: str) -> tuple[str, int, list[int]]:
    parts = urlsplit(url)
    segments = parts.path.rstrip("/").split("/")
    tags = [int(t) for t in ",".join(parse_qs(parts.query).get("tags", [])).split(",") if t.strip().isdigit()]
    return urlunsplit((parts.scheme, parts.netloc, "/".join(segments[:-2]), "", "")), int(segments[-1]), tags


def request(url: str) -> tuple[str, Any]:
    """The real request of a queued address: the interface URL and the JSON query."""
    base, offset, tags = _parts(url)
    return f"{base}/{LIMIT}/{offset}", {"tags": tags, "query": "", "packages": False, "ids": []}


def read(text: str, page_url: str):
    from app.scraping.catalog import FoundItem, PageResult

    try:
        rows = json.loads(text)
    except ValueError:
        raise ValueError("Rozhranie obchodu nevrátilo JSON.") from None
    if not isinstance(rows, list):
        raise ValueError("Rozhranie obchodu vrátilo neočakávaný formát.")
    base, offset, tags = _parts(page_url)
    grade = GRADES.get(tags[-1]) if len(tags) > 1 else None
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(page_url))
    items = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "item" or not row.get("id"):
            continue
        if len(tags) > 1 and row.get("tag_matches", 0) < len(tags):
            continue  # matches the category or the grade only
        price = row.get("price")
        value = Decimal(str(price)).quantize(Decimal("0.01")) if isinstance(price, (int, float, str)) and str(price) \
            else None
        hints = {"grade": grade} if grade else {}
        if row.get("status"):
            hints["availability"] = AVAILABILITY.get(str(row["status"]).lower(), "unknown")
        if row.get("code"):
            hints["code"] = str(row["code"])
        url = f"{origin}/obchod/index.php?item=nab_det&sort_id={row['id']}"
        items.append(FoundItem(key=f"nns:{row['id']}", url=url,
                               title=str(row.get("name") or "").strip() or None, price=value,
                               currency="CZK" if value is not None else None, price_from_listing=value is not None,
                               hints=hints))
    next_urls = []
    if len(rows) >= LIMIT:
        next_urls.append(f"{base}/{LIMIT}/{offset + LIMIT}?tags={','.join(str(t) for t in tags)}")
    return PageResult(items=items, next_urls=next_urls)
