"""Product details from ordinary HTML without structured data (docs/SCRAPING_ROBUSTNESS_PLAN.md, 5.2).

Used as the last step of the "auto" parser and by product discovery. Rules, in order of trust:
  title    <h1>; else the first element whose class or id says "name/title" (nazev, název, title,
           product-name, …) before the price; else og:title; else <title> without the site name
  price    a number with a currency right after a price label ("Cena:", "Naše cena", "Cena s DPH",
           "Price"); without a label only when the page shows exactly one price
  other    ISBN/EAN/SKU by label (find_identifiers), availability by known phrases

Pages that list several products (a price label repeated with different prices before any title) are
not treated as a product. The result says source="heuristic", so it can be told apart from structured data.
"""
import re

from bs4 import BeautifulSoup

from app.scraping.normalize import detect_currency, find_identifiers, normalize_availability, parse_price
from app.scraping.parsers.base import Extracted, PageContext, ParseError

VERSION = 1
_MONEY = r"(\d{1,3}(?:[  .]\d{3})*(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?)\s*(?:,-|,—)?\s*(Kč|CZK|€|EUR|Eur|eur|Sk)"
_LABELLED = re.compile(r"(?:naše\s+cena|naša\s+cena|cena\s+s\s+dph|prodejní\s+cena|predajná\s+cena|cena\s+za\s+kus|"
                       r"\bcena|\bprice)\s*(?:s\s*dph)?\s*:?\s*" + _MONEY, re.I)
_ANY_MONEY = re.compile(_MONEY)
_TITLE_CLASS = re.compile(r"(nazev|název|nazov|title|product[-_]?name|productname|heading|jmeno|titul)", re.I)
_SKIP_TEXT = re.compile(r"(košík|kosik|cookies|přihlášení|prihlásenie|menu)", re.I)


def _text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1 and 3 <= len(h1.get_text(" ", strip=True)) <= 300:
        return " ".join(h1.get_text(" ", strip=True).split())
    for tag in soup.find_all(["div", "span", "h2", "h3", "p", "td", "strong"]):
        names = " ".join(tag.get("class", []) + [tag.get("id") or "", tag.get("itemprop") or ""])
        if names.strip() and _TITLE_CLASS.search(names):
            text = " ".join(tag.get_text(" ", strip=True).split())
            if 3 <= len(text) <= 300 and not _SKIP_TEXT.search(text):
                return text
    meta = soup.find("meta", property="og:title")
    if meta and meta.get("content"):
        return meta["content"].strip()
    if soup.title and soup.title.string:
        return re.split(r"\s+[|–—-]\s+", soup.title.string.strip())[0] or None
    return None


def parse_heuristic(ctx: PageContext) -> Extracted:
    soup = BeautifulSoup(ctx.html, "html.parser")
    title = _title(soup)
    text = _text(soup)
    labelled = _LABELLED.findall(text)
    if labelled:
        amount, currency = labelled[0]  # the first price label belongs to the product the page is about
    else:
        prices = {(a, c) for a, c in _ANY_MONEY.findall(text)}
        if len(prices) != 1:
            raise ParseError("Na stránke sa nenašla cena s návestím (napr. „Cena: 238 Kč“) ani jediná jednoznačná "
                             "cena.")
        amount, currency = next(iter(prices))
    price = parse_price(amount)
    if price is None or not title:
        raise ParseError("Na stránke sa nenašiel názov alebo cena produktu.")
    return Extracted(price=price, currency=detect_currency(currency) or currency.upper(), title=title[:500],
                     availability=normalize_availability(text[:20000]), identifiers=find_identifiers(text),
                     source="heuristic")
