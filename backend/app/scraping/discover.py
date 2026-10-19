"""Product links on one listing page (category, news, search results), for the user to pick from.

Only the single page the user entered is read; the site is never crawled. Links are limited to the
competitor's domain and ranked by how much they look like product pages.
"""
import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.services.urls import InvalidUrl, normalize_url

MAX_CANDIDATES = 500
PRODUCT_PATH = re.compile(r"/(produkt|product|produkty|products|p|item|detail|kniha|book|zbozi|tovar)/", re.I)
SKIP_PATH = re.compile(r"/(kosik|cart|basket|login|user|prihlas|registr|search|vyhledav|hladat|obchodni|kontakt|"
                       r"cdn-cgi|sites/default/files|themes|images|gallery|galerie|add_dialog)(/|$)", re.I)
TRAILING_ID = re.compile(r"[-/]\d{2,}(\.html?)?/?$")


GENERIC_TEXT = re.compile(r"^(více|viac|detail|detaily|zobrazit|zobraziť|more|read more|kúpiť|koupit|do košíka|"
                          r"do košíku|info|informace|informácie|více informací|viac informácií|>|»)\b", re.I)


def _card_title(anchor) -> str:
    """Text of the card around a generic "more info" link: the closest container with more text than
    the link itself (up to three levels up), without the link text, shortened."""
    own = anchor.get_text(" ", strip=True)
    node = anchor.parent
    for _ in range(3):
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        text = " ".join((text.replace(own, " ") if own else text).split())
        if len(text) > 3:
            return text[:120]
        node = node.parent
    return ""


def _score(path: str) -> int:
    score = 0
    if PRODUCT_PATH.search(path):
        score += 2
    if TRAILING_ID.search(path) or path.endswith((".html", ".htm")):
        score += 1
    return score


def _belongs(url: str, domain: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def discover_links(html: str, page_url: str, domain: str, tracked: set[str]) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}

    def add(raw: str | None, text: str) -> None:
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            return
        try:
            url = normalize_url(urljoin(page_url, raw))
        except InvalidUrl:
            return
        path = urlsplit(url).path
        if not _belongs(url, domain) or SKIP_PATH.search(path) or url.rstrip("/") == page_url.rstrip("/"):
            return
        score = _score(path)
        if score == 0:
            return
        text = " ".join(text.split())[:200]
        if url not in found:
            found[url] = {"url": url, "text": text, "score": score, "tracked": url in tracked}
        elif text and not found[url]["text"]:
            found[url]["text"] = text

    for a in soup.find_all("a", href=True):
        image = a.find("img")
        text = a.get_text(" ", strip=True) or (image.get("alt", "") if image else "")
        if not text or GENERIC_TEXT.match(text):
            text = _card_title(a) or text
        add(a["href"], text)
    for tag in soup.find_all("script", type="application/ld+json"):  # ItemList entries
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for item in (data.get("itemListElement", []) if isinstance(data, dict) else []):
            if isinstance(item, dict):
                add(item.get("url") or (item.get("item") or {}).get("url"), str(item.get("name", "")))

    ranked = sorted(found.values(), key=lambda c: (-c["score"], c["url"]))
    # When the page has links with a clear product path, weaker guesses (e.g. "/katalog-2026") are noise.
    if ranked and ranked[0]["score"] >= 2:
        ranked = [c for c in ranked if c["score"] >= 2]
    return ranked[:MAX_CANDIDATES]
