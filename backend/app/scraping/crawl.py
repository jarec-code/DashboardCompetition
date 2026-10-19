"""Helpers of the guided crawler (docs/SCRAPING_ROBUSTNESS_PLAN.md, method B/C): links of a page and
how promising they are. Pure functions; the crawl itself is driven by app.services.discovery.Runner.

Template statistics ("yield") are kept per URL template: how many pages of the template were read, how
many were products and how many were product listings. Templates that gave nothing after several pages
are not followed any more ("dead"), so the crawler spends its budget where products are.
"""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.scraping.discover import SKIP_PATH, _score, skipped_query
from app.scraping.urlnorm import safe_canonical, template

FILE_EXTENSION = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|rar|7z|docx?|xlsx?|pptx?|mp3|mp4|avi|mov|css|js|"
                            r"xml|ico|txt|epub|mobi)$", re.I)
DEAD_AFTER_PAGES = 5          # a template that gave no product or listing after this many pages is not followed
LISTING_MIN_PRODUCT_LINKS = 3

PRIORITY_ENTRY, PRIORITY_PRODUCT, PRIORITY_LISTING, PRIORITY_UNKNOWN, PRIORITY_INFO = 0, 10, 20, 50, 80


def belongs(url: str, domains: set[str]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in domains)


def page_links(html: str, page_url: str, domains: set[str], drop_params: set[str],
               include: list[str] | None = None, exclude: list[str] | None = None) -> list[str]:
    """Canonical internal links of a page worth reading: allowed domains, no files, cart, login or search;
    `include`/`exclude` are regular expressions of a website's recipe on the path with query."""
    keep = [re.compile(p) for p in include or []]
    drop = [re.compile(p) for p in exclude or []]
    found: list[str] = []
    seen: set[str] = set()
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        raw = a["href"].strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = safe_canonical(urljoin(page_url, raw), drop_params)
        if url is None or url in seen or not belongs(url, domains):
            continue
        parts = urlsplit(url)
        if SKIP_PATH.search(parts.path) or FILE_EXTENSION.search(parts.path) or skipped_query(parts.query):
            continue
        target = parts.path + ("?" + parts.query if parts.query else "")
        if (keep and not any(p.search(target) for p in keep)) or any(p.search(target) for p in drop):
            continue
        seen.add(url)
        found.append(url)
    return found


def merged(*stats: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    total: dict[str, dict[str, int]] = {}
    for part in stats:
        for tpl, values in (part or {}).items():
            row = total.setdefault(tpl, {"pages": 0, "products": 0, "listings": 0})
            for key in row:
                row[key] += values.get(key, 0)
    return total


def product_like(url: str, stats: dict[str, dict[str, int]], pattern: str | None = None) -> bool:
    """The recipe's product pattern; else a known product template, or a URL that looks like a product page."""
    if pattern:
        parts = urlsplit(url)
        return bool(re.search(pattern, parts.path + ("?" + parts.query if parts.query else "")))
    known = stats.get(template(url))
    if known and known["pages"] >= 2:
        return known["products"] * 2 >= known["pages"]
    parts = urlsplit(url)
    return _score(parts.path, parts.query) >= 2


def dead(tpl: str, stats: dict[str, dict[str, int]]) -> bool:
    known = stats.get(tpl)
    return bool(known and known["pages"] >= DEAD_AFTER_PAGES and not known["products"] and not known["listings"])


def priority(url: str, stats: dict[str, dict[str, int]], pattern: str | None = None) -> int:
    if product_like(url, stats, pattern):
        return PRIORITY_PRODUCT
    known = stats.get(template(url))
    if not known or not known["pages"]:
        return PRIORITY_UNKNOWN
    return PRIORITY_LISTING if known["listings"] else PRIORITY_INFO


def looks_like_js_app(html: str) -> bool:
    """A page whose content is drawn by JavaScript: almost no visible text and no links, but scripts
    (e.g. "<div id=app>Loading…</div>" of a single-page application). HTTP methods cannot read such a site."""
    soup = BeautifulSoup(html, "html.parser")
    scripts = len(soup.find_all("script"))
    for tag in soup(["script", "style", "noscript", "title"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return scripts >= 1 and len(soup.find_all("a", href=True)) < 3 and len(text) < 200
