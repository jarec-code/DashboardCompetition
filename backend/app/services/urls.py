"""Validation and normalization of user-entered website URLs.

This is input validation only. Network-level SSRF protection (DNS resolution, private IP ranges,
redirect checks) happens in the fetcher before any request is made (stage 4).
"""
import ipaddress
from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (None, 80, 443)
MAX_URL_LENGTH = 2048


class InvalidUrl(ValueError):
    pass


def normalize_url(raw: str) -> str:
    """Return a canonical http(s) URL: lowercase scheme and host, no credentials, query kept, fragment dropped."""
    value = (raw or "").strip()
    if not value:
        raise InvalidUrl("Zadajte URL.")
    if len(value) > MAX_URL_LENGTH:
        raise InvalidUrl("URL je príliš dlhá.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise InvalidUrl("Neplatná URL.") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrl("URL musí začínať http:// alebo https://.")
    if parts.username or parts.password:
        raise InvalidUrl("URL nesmie obsahovať meno ani heslo.")
    if port not in ALLOWED_PORTS:
        raise InvalidUrl("Povolené sú iba štandardné porty 80 a 443.")
    scheme = parts.scheme.lower()
    host = _normalize_host(parts.hostname or "")
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _normalize_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    if not host:
        raise InvalidUrl("V URL chýba doména.")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise InvalidUrl("Zadajte doménu, nie IP adresu.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise InvalidUrl("Neplatná doména.") from None
    labels = host.split(".")
    if len(labels) < 2 or not all(labels) or len(host) > 253:
        raise InvalidUrl("Neplatná doména (napr. www.priklad.cz).")
    if labels[-1] in ("local", "localhost", "internal", "lan", "home", "corp") or host == "localhost":
        raise InvalidUrl("Interné domény nie sú povolené.")
    return host


def domain_of(url: str) -> str:
    """Registrable-ish domain used for duplicate detection: host without a leading 'www.'."""
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host
