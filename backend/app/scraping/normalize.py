"""Normalization of scraped values. Ported from the original scraper.py, using Decimal for prices."""
import re
from decimal import Decimal, InvalidOperation

from app.services.identifiers import normalize_ean, normalize_isbn, normalize_sku

_CURRENCY_SYMBOLS = (("€", "EUR"), ("Kč", "CZK"), ("zł", "PLN"), ("Ft", "HUF"), ("£", "GBP"), ("$", "USD"))
_CURRENCY_CODE_RE = re.compile(r"\b(EUR|USD|GBP|CZK|PLN|HUF|CHF)\b", re.I)


def parse_price(value: object) -> Decimal | None:
    """Turn '1 299,90 €', '$1,299.99', '1.299,- Kč' or 12.5 into a Decimal rounded to cents (or None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return _to_decimal(str(value))
    text = str(value).replace("\xa0", " ").replace(" ", " ").strip()
    match = re.search(r"\d[\d\s.,']*", text)
    if not match or text[:match.start()].rstrip().endswith(("-", "−")):
        return None  # no number, or a negative amount such as a discount "-5 Kč"
    num = re.sub(r"[\s']", "", match.group(0)).rstrip(".,")
    if "," in num and "." in num:
        # Whichever separator comes last is the decimal separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        whole, _, frac = num.rpartition(",")
        num = whole.replace(",", "") + ("." + frac if len(frac) != 3 else frac)
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.rpartition(".")[2]) == 3 and len(num) > 4):
        num = num.replace(".", "")
    return _to_decimal(num)


def _to_decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return value.quantize(Decimal("0.01"))


def detect_currency(text: object) -> str | None:
    if not text:
        return None
    text = str(text)
    for symbol, code in _CURRENCY_SYMBOLS:
        if symbol in text:
            return code
    match = _CURRENCY_CODE_RE.search(text)
    return match.group(1).upper() if match else None


# ---------- availability ----------

_SCHEMA_AVAILABILITY = {
    "instock": "in_stock", "instoreonly": "in_stock", "onlineonly": "in_stock", "limitedavailability": "in_stock",
    "outofstock": "out_of_stock", "soldout": "out_of_stock", "discontinued": "out_of_stock",
    "preorder": "preorder", "presale": "preorder", "backorder": "preorder",
}
# Checked in order: negative phrases first ("není skladem" contains "skladem").
_TEXT_AVAILABILITY = [
    ("out_of_stock", ("není skladem", "nie je na sklade", "nie je skladom", "vyprodáno", "vypredané", "vypredaný",
                      "nedostupn", "out of stock", "sold out", "nelze objednat", "nedá sa objednať")),
    ("preorder", ("předprodej", "predpredaj", "předobjednávk", "predobjednávk", "pre-order", "preorder",
                  "připravujeme", "pripravujeme", "očekáváme", "očakávame")),
    ("in_stock", ("skladem", "skladom", "na sklade", "na skladě", "in stock", "dostupné", "dostupný", "k dispozici")),
]


def normalize_availability(value: object) -> str:
    """Map schema.org URLs and Czech/Slovak/English shop texts to in_stock/out_of_stock/preorder/unknown."""
    if not value:
        return "unknown"
    text = str(value).strip().lower()
    key = text.rsplit("/", 1)[-1].replace(" ", "").replace("_", "")
    if key in _SCHEMA_AVAILABILITY:
        return _SCHEMA_AVAILABILITY[key]
    for status, phrases in _TEXT_AVAILABILITY:
        if any(p in text for p in phrases):
            return status
    return "unknown"


# ---------- identifiers ----------

_LABELLED_ID = re.compile(r"\b(ISBN(?:-1[03])?|EAN|GTIN(?:-?1[34])?|SKU|kód zboží|kód tovaru|katalogové číslo)"
                          r"\s*[:\-]?\s*([0-9Xx][0-9Xx \-]{6,20}[0-9Xx])", re.I)
_BARE_GTIN = re.compile(r"(?<!\d)(\d{13})(?!\d)")


def find_identifiers(text: str | None) -> dict[str, str]:
    """ISBN/EAN/SKU found in page text. Only valid codes (check digit) are returned.

    Unlabelled 13-digit numbers count only as ISBN (prefix 978/979): any other bare number could be
    unrelated, and a wrong identifier would assign the offer to a wrong product automatically.
    """
    found: dict[str, str] = {}
    if not text:
        return found
    for label, raw in _LABELLED_ID.findall(text):
        label = label.lower()
        if label.startswith("isbn"):
            if (isbn := normalize_isbn(raw)) and "isbn" not in found:
                found["isbn"] = isbn
        elif label.startswith(("ean", "gtin")):
            if (ean := normalize_ean(raw)) and "ean" not in found:
                found["ean"] = ean
        elif sku := normalize_sku(raw):
            found.setdefault("sku", sku)
    if "ean" not in found and "isbn" not in found:
        for code in _BARE_GTIN.findall(text):
            if code[:3] in ("978", "979") and normalize_ean(code):
                found["ean"] = code
                break
    if (ean := found.get("ean")) and ean[:3] in ("978", "979") and "isbn" not in found:
        found["isbn"] = normalize_isbn(ean) or ean
    return found
