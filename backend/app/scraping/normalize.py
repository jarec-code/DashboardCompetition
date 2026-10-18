"""Normalization of scraped values. Ported from the original scraper.py, using Decimal for prices."""
import re
from decimal import Decimal, InvalidOperation

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
