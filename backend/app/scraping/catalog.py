"""Reading a competitor's product catalogue from one fetched page of a discovery source.

Source types (docs/AUTO_DISCOVERY_PLAN.md, part B):
  sitemap   XML sitemap or sitemap index; items are product URLs (title and price come later from the page)
  feed      Heureka / Google Merchant feed; items carry title, price and identifiers
  listing   product listing pages followed through rel="next"; items are product links with their card text
  adapter   a site-specific listing reader (app.scraping.sites.*), e.g. alter.cz, whose listing has prices

Every function is pure (HTML/XML in, items out), so it can be tested on saved pages.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from app.scraping.discover import SKIP_PATH, discover_links
from app.scraping.normalize import detect_currency, parse_price
from app.services.urls import InvalidUrl, normalize_url

SOURCE_TYPES = ("sitemap", "feed", "listing", "adapter")
MAX_SITEMAP_CHILDREN = 50
TAB_MIN_PARENTS = 10  # a last path segment under this many pages that are themselves listed = a tab of a page
_SITEMAP_NS = re.compile(r"^\{[^}]+\}")


@dataclass
class FoundItem:
    key: str        # stable identity within the competitor (normally the product URL)
    url: str        # where a person can see it
    title: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    price_from_listing: bool = False  # the source itself carries the price (no product page needed)
    identifiers: dict[str, str] = field(default_factory=dict)
    hints: dict[str, Any] = field(default_factory=dict)  # e.g. {"grade": "1. ročník", "category": "…"}


@dataclass
class PageResult:
    items: list[FoundItem] = field(default_factory=list)
    next_urls: list[str] = field(default_factory=list)  # further pages / sitemaps of the same source
    tabs: list[str] = field(default_factory=list)  # sitemap: last path segments of tab pages left out


def _belongs(url: str, domain: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def _local(tag: str) -> str:
    return _SITEMAP_NS.sub("", tag)


# ---------- sitemap ----------

def read_sitemap(text: str, page_url: str, domain: str, pattern: str | None = None) -> PageResult:
    """URLs of a sitemap (or the child sitemaps of a sitemap index), limited to the competitor's domain.

    `pattern` (a regular expression on the URL path) keeps only product pages, e.g. ``^/produkt/\\d+$``.
    Without it, pages that are clearly not products (cart, login, …) are left out and the rest is
    checked later by reading the page: a page without a price is then not treated as a product.
    """
    try:
        root = ElementTree.fromstring(text.encode("utf-8"), forbid_dtd=True)
    except Exception:
        raise ValueError("Sitemap nie je platné XML.") from None
    locs = [(el.text or "").strip() for el in root.iter() if _local(el.tag) == "loc" and (el.text or "").strip()]
    if _local(root.tag) == "sitemapindex":
        children = [u for u in locs if _belongs(u, domain)]
        return PageResult(next_urls=children[:MAX_SITEMAP_CHILDREN])
    if _local(root.tag) != "urlset":
        raise ValueError("Neznámy formát sitemap (očakáva sa urlset alebo sitemapindex).")
    regex = re.compile(pattern) if pattern else None
    tabs = tab_segments([urlsplit(u).path for u in locs])
    items = []
    for raw in locs:
        try:
            url = normalize_url(raw)
        except InvalidUrl:
            continue
        path = urlsplit(url).path
        if not _belongs(url, domain) or path in ("", "/") or SKIP_PATH.search(path):
            continue
        if regex and not regex.search(path):
            continue
        if is_tab(path, tabs):
            continue
        items.append(FoundItem(key=url, url=url))
    return PageResult(items=items, tabs=sorted(tabs))


def tab_segments(paths: list[str]) -> set[str]:
    """Tabs of product pages in a sitemap: a page /ucebnice/prirodopis-6 listed together with
    /ucebnice/prirodopis-6/obsah, …/ukazky, …/ke_stazeni, the same last segments under many listed pages.
    Such tabs repeat the product (title, price, ISBN) and would cost a request each (ucebnice.org: 8 tabs
    for each of 332 products, 87 % of its sitemap)."""
    listed = {p.rstrip("/") for p in paths}
    parents: dict[str, set[str]] = {}
    for path in listed:
        parent, _, last = path.rpartition("/")
        if parent and last and parent in listed:
            parents.setdefault(last, set()).add(parent)
    return {last for last, found in parents.items() if len(found) >= TAB_MIN_PARENTS}


def is_tab(path: str, tabs: set[str] | list[str]) -> bool:
    parent, _, last = path.rstrip("/").rpartition("/")
    return bool(parent) and last in tabs


# ---------- feed ----------

def read_feed(data: bytes, page_url: str, domain: str, default_currency: str | None = None) -> PageResult:
    """Products of a Heureka or Google Merchant feed (title, price, identifiers, URL)."""
    from app.services import imports  # the same readers as the product import

    try:
        _, rows = imports.read_file(data, page_url)
    except imports.ImportFileError as exc:
        raise ValueError(str(exc)) from None
    items = []
    for row in rows:
        try:
            url = normalize_url(row.get("url") or "")
        except InvalidUrl:
            continue
        if not _belongs(url, domain):
            continue
        price = parse_price(row.get("price"))
        identifiers = {k: row[k] for k in ("isbn", "ean") if row.get(k)}
        items.append(FoundItem(key=url, url=url, title=row.get("name"), price=price if price else None,
                               currency=(row.get("currency") or detect_currency(row.get("price")) or default_currency),
                               price_from_listing=bool(price), identifiers=identifiers,
                               hints={"category": row["category"]} if row.get("category") else {}))
    return PageResult(items=items)


# ---------- listing pages ----------

def next_page_links(soup: BeautifulSoup, page_url: str, domain: str) -> list[str]:
    """The "next page" of a paginated listing (rel="next"), on the same path as the current page."""
    path = urlsplit(page_url).path
    found = []
    for tag in soup.select('a[rel~="next"], link[rel~="next"]'):
        try:
            url = normalize_url(urljoin(page_url, tag.get("href") or ""))
        except InvalidUrl:
            continue
        if _belongs(url, domain) and urlsplit(url).path == path and url != page_url:
            found.append(url)
    return found[:1]


def read_listing(html: str, page_url: str, domain: str, pattern: str | None = None) -> PageResult:
    """Product links of a listing page (the same rules as the "from a category page" helper)."""
    soup = BeautifulSoup(html, "html.parser")
    regex = re.compile(pattern) if pattern else None
    items = [FoundItem(key=c["url"], url=c["url"], title=c["text"] or None)
             for c in discover_links(html, page_url, domain, set())
             if c["score"] >= 2 and (regex is None or regex.search(urlsplit(c["url"]).path))]
    return PageResult(items=items, next_urls=next_page_links(soup, page_url, domain))


# ---------- adapters for particular websites ----------

@dataclass
class Adapter:
    """A site-specific catalogue reader. `start_urls` are relative to the competitor's base URL.

    `request`, when set, turns a queued address into the real request: (URL, JSON body or None). A JSON
    body makes it a read-only POST to the shop's own listing interface (the only POST discovery ever sends,
    allowed per adapter, approved for nns.cz on 19 Oct 2026)."""
    name: str
    domains: tuple[str, ...]
    start_urls: tuple[str, ...]
    read: Callable[[str, str, str], PageResult]
    request: Callable[[str], tuple[str, Any]] | None = None


def _alter_listing(html: str, page_url: str, domain: str) -> PageResult:
    from app.scraping.sites import alter_cz

    soup = BeautifulSoup(html, "html.parser")
    return PageResult(items=alter_cz.listing_items(soup, page_url), next_urls=next_page_links(soup, page_url, domain))


def _nns_request(url: str) -> tuple[str, Any]:
    from app.scraping.sites import nns_cz

    return nns_cz.request(url)


def _nns_listing(text: str, page_url: str, domain: str) -> PageResult:
    from app.scraping.sites import nns_cz

    return nns_cz.read(text, page_url)


def _nns_start() -> tuple[str, ...]:
    from app.scraping.sites import nns_cz

    return nns_cz.start_urls()


def _naseucebnice_listing(text: str, page_url: str, domain: str) -> PageResult:
    from app.scraping.sites import naseucebnice_cz

    return naseucebnice_cz.read(text, page_url)


def _naseucebnice_start() -> tuple[str, ...]:
    from app.scraping.sites import naseucebnice_cz

    return naseucebnice_cz.start_urls()


ADAPTERS = [
    Adapter("alter_cz", ("alter.cz",), ("/shop",), _alter_listing),
    # SPN sells in the shared shop www.naseucebnice.cz (absolute start URLs: another domain than spn.cz)
    Adapter("naseucebnice_cz", ("spn.cz",), _naseucebnice_start(), _naseucebnice_listing),
    Adapter("nns_cz", ("nns.cz",), _nns_start(), _nns_listing, _nns_request),
]


def adapter_for(domain: str) -> Adapter | None:
    return next((a for a in ADAPTERS if domain in a.domains), None)


def adapter_by_name(name: str | None) -> Adapter | None:
    return next((a for a in ADAPTERS if a.name == name), None)
