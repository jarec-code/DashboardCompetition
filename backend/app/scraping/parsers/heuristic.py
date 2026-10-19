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
# A price label: "Cena:", "Naše cena", "Cena s DPH:", "cena vč. DPH:", "cena bez DPH:", "Price" … (the label
# may have a few words between "cena" and the colon), then the amount with a currency.
_LABELLED = re.compile(r"((?:\w+\s+)?(?:cena|price)(?:[ \t]+[\w.%]+){0,4}?)\s*:?\s*" + _MONEY, re.I)
_WITHOUT_VAT = re.compile(r"bez\s+dph|without\s+vat|excl", re.I)
_WITH_VAT = re.compile(r"(vč\.?|včetně|vrátane|s|with)\s+(dph|vat)", re.I)
# Words before an amount that make it a condition, not a price ("vyšší než 500 Kč", "doprava zdarma od 700 Kč").
_THRESHOLD = re.compile(r"(vyšší|vyšš|nižší|než|nad|od|až|do|minimáln\w*|maximáln\w*|zdarma|poštovné|poštovného|"
                        r"doprav\w*|balné|sleva|slevu|zľava|ušetř\w*|ušetr\w*|celkem|spolu|košík\w*)\W*$", re.I)
UNLABELLED_NEAR_TITLE = 400   # an amount without a label counts only this close after the title
_MEMBER = re.compile(r"vaše|vaša|your|členská|clenska|pro\s+školy|škol|velkoobchod|veľkoobchod", re.I)
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
    vat_mode = "unknown"
    if labelled:
        # The price with VAT for everyone: a label "with VAT" first, then plain labels; prices without VAT or
        # for members/schools only when there is nothing else. The first of a kind belongs to the product.
        def rank(entry):
            label = entry[0]
            return (bool(_WITHOUT_VAT.search(label)), bool(_MEMBER.search(label)), not _WITH_VAT.search(label))
        label, amount, currency = min(labelled, key=rank)
        vat_mode = "without_vat" if _WITHOUT_VAT.search(label) else "with_vat" if _WITH_VAT.search(label) else "unknown"
    else:
        # One amount without a label is a price only right after the title and not inside a condition
        # ("Poštovné neúčtujeme, je-li částka vyšší než 500,- Kč" on a "how to order" page is not a price).
        found = list(_ANY_MONEY.finditer(text))
        if len({(m.group(1), m.group(2)) for m in found}) != 1:
            raise ParseError("Na stránke sa nenašla cena s návestím (napr. „Cena: 238 Kč“) ani jediná jednoznačná "
                             "cena.")
        match = found[0]
        start = text.rfind(title, 0, match.start()) if title else -1  # the title closest before the amount
        near = start >= 0 and 0 <= match.start() - start <= UNLABELLED_NEAR_TITLE
        if not near or _THRESHOLD.search(text[max(0, match.start() - 40):match.start()]):
            raise ParseError("Jediná suma na stránke nie je cena produktu (podmienka, poštovné alebo ďaleko od názvu).")
        amount, currency = match.group(1), match.group(2)
    price = parse_price(amount)
    if price is None or not title:
        raise ParseError("Na stránke sa nenašiel názov alebo cena produktu.")
    return Extracted(price=price, currency=detect_currency(currency) or currency.upper(), title=title[:500],
                     availability=normalize_availability(text[:20000]), identifiers=find_identifiers(text),
                     vat_mode=vat_mode, source="heuristic")
