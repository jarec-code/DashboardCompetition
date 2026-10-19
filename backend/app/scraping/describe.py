"""The description of a product page, for telling the grade when the title does not ("Grafomotorika",
"Učíme se číst"): structured data (JSON-LD, Open Graph, meta description) or a site rule. Pure functions."""
import json
from collections.abc import Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

MAX_LENGTH = 1500


def _site_rules() -> dict[str, Callable[[str], str | None]]:
    from app.scraping.sites import naseucebnice_cz

    return {"naseucebnice.cz": naseucebnice_cz.description}


def _jsonld(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data, *(data.get("@graph") or [])]:
            if isinstance(node, dict) and "Product" in str(node.get("@type")) and node.get("description"):
                return str(node["description"])
    return None


def description_of(html: str, url: str, title: str | None = None) -> str | None:
    """The product's description (at most MAX_LENGTH characters); None when the page has none or it only
    repeats the title."""
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    rule = _site_rules().get(host)
    text = rule(html) if rule else None
    if not text:
        soup = BeautifulSoup(html, "html.parser")
        text = _jsonld(soup)
        for attrs in ({"property": "og:description"}, {"name": "description"}):
            if text:
                break
            meta = soup.find("meta", attrs=attrs)
            text = meta.get("content") if meta else None
    text = " ".join(BeautifulSoup(text, "html.parser").get_text(" ").split()) if text else ""
    if not text or (title and text.strip().lower() == title.strip().lower()):
        return None
    return text[:MAX_LENGTH]
