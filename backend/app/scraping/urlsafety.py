"""SSRF protection: a URL is fetched only if every address its host resolves to is public.

Checked before each request and each redirect; the fetcher additionally verifies the address it
actually connected to (protects against DNS rebinding between the check and the connection).
"""
import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from app.services.urls import InvalidUrl, normalize_url

Resolver = Callable[[str, int], list[str]]

# Ranges that are not covered by ipaddress' is_global (or are worth listing explicitly).
_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "192.0.0.0/24", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "64:ff9b::/96", "fc00::/7", "fe80::/10", "ff00::/8",
)]


class UnsafeUrl(ValueError):
    """The URL must not be fetched (permanent error)."""


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError(f"Doménu {host} sa nepodarilo preložiť na IP adresu.") from exc
    return sorted({info[4][0] for info in infos})


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not any(ip in net for net in _BLOCKED if net.version == ip.version)


def check_url(url: str, resolver: Resolver = system_resolver) -> str:
    """Return the normalized URL if it may be fetched; raise UnsafeUrl otherwise.

    DNS failures raise OSError (a temporary error: the domain may resolve again later).
    """
    try:
        url = normalize_url(url)
    except InvalidUrl as exc:
        raise UnsafeUrl(str(exc)) from None
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = resolver(parts.hostname or "", port)
    if not addresses:
        raise OSError(f"Doména {parts.hostname} nemá žiadnu IP adresu.")
    blocked = [a for a in addresses if not is_public_ip(a)]
    if blocked:
        raise UnsafeUrl(f"Doména {parts.hostname} smeruje na neverejnú adresu ({blocked[0]}), požiadavka zablokovaná.")
    return url
