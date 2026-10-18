"""Product identifiers: ISBN and EAN/GTIN normalization with check-digit validation.

An ISBN-13 is also an EAN-13 (prefix 978/979), so matching compares both as one GTIN-13 code.
"""
import re

_NON_DIGITS = re.compile(r"[^0-9Xx]")


def _gtin_check_digit_ok(digits: str) -> bool:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(reversed(digits[:-1]), start=1))
    return (10 - total % 10) % 10 == int(digits[-1])


def normalize_isbn(value: str | None) -> str | None:
    """Return a valid ISBN-13 (ISBN-10 is converted), or None when the value is not a valid ISBN."""
    if not value:
        return None
    raw = _NON_DIGITS.sub("", value).upper()
    if len(raw) == 10 and re.fullmatch(r"\d{9}[\dX]", raw):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(raw))
        if total % 11:
            return None
        core = "978" + raw[:9]
        check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(core)) % 10) % 10
        return core + str(check)
    if len(raw) == 13 and raw.isdigit() and raw[:3] in ("978", "979") and _gtin_check_digit_ok(raw):
        return raw
    return None


def normalize_ean(value: str | None) -> str | None:
    """Return a valid EAN-8/UPC-A/EAN-13/GTIN-14 as digits (UPC-A padded to 13), or None."""
    if not value:
        return None
    raw = re.sub(r"\D", "", value)
    if len(raw) not in (8, 12, 13, 14) or not _gtin_check_digit_ok(raw):
        return None
    return raw.zfill(13) if len(raw) == 12 else raw


def normalize_sku(value: str | None) -> str | None:
    if not value:
        return None
    sku = re.sub(r"\s+", "", value).upper()
    return sku[:64] or None


def gtin_keys(isbn: str | None, ean: str | None) -> set[str]:
    """Comparable codes of a product: its ISBN-13 and EAN (an ISBN-13 doubles as EAN-13)."""
    return {code for code in (normalize_isbn(isbn), normalize_ean(ean)) if code}
