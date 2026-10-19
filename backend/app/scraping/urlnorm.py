"""URL normalization and URL templates for product discovery (docs/SCRAPING_ROBUSTNESS_PLAN.md, 5.2).

`canonical()` gives one spelling per page: lowercase host, no fragment, no session or tracking parameters,
parameters sorted. It is the basis of de-duplication and of protection against cycles.

`template()` generalizes a URL so that pages of one kind share it, e.g.
    /produkt/716                                 -> /produkt/{N}
    /index.php?zobraz=detail&id_katalog=306      -> /index.php?id_katalog={N}&zobraz=detail
    /hrava-abeceda/                              -> /{S}/
Discovery counts per template how many pages were products, listings or nothing, and learns from it.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.urls import InvalidUrl, normalize_url

SESSION_PARAMS = {"phpsessid", "sid", "sessionid", "jsessionid", "oscsid", "zenid", "session_id"}
TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "srsltid"}
PRINT_PARAMS = {"tisk", "print", "printable"}
_NUMBER = re.compile(r"^\d+$")
_HAS_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"^[a-z_]{1,24}$")


def _dropped(key: str, extra: set[str]) -> bool:
    k = key.lower()
    return k in SESSION_PARAMS or k in TRACKING_PARAMS or k in PRINT_PARAMS or k.startswith("utm_") or k in extra


def canonical(url: str, drop_params: set[str] | frozenset[str] = frozenset()) -> str:
    """One spelling of a page URL. Raises InvalidUrl for URLs that cannot be fetched (mailto:, ftp:, …)."""
    url = normalize_url(url)
    parts = urlsplit(url)
    extra = {p.lower() for p in drop_params}
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _dropped(k, extra))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), ""))


def safe_canonical(url: str, drop_params: set[str] | frozenset[str] = frozenset()) -> str | None:
    try:
        return canonical(url, drop_params)
    except (InvalidUrl, ValueError):
        return None


def _segment(segment: str) -> str:
    if not segment:
        return segment
    if _NUMBER.match(segment):
        return "{N}"
    if _HAS_DIGIT.search(segment) or "-" in segment or len(segment) > 24:
        return "{S}"  # a slug or an identifier, e.g. hrava-abeceda, kniha-123
    return segment.lower()


def _value(value: str) -> str:
    if _NUMBER.match(value):
        return "{N}"
    return value.lower() if _WORD.match(value.lower()) else "{V}"


def template(url: str) -> str:
    """The path and query of a URL with identifiers replaced, e.g. /index.php?id_katalog={N}&zobraz=detail."""
    parts = urlsplit(url)
    path = "/".join(_segment(s) for s in (parts.path or "/").split("/"))
    query = sorted((k.lower(), _value(v)) for k, v in parse_qsl(parts.query, keep_blank_values=True))
    return path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")
