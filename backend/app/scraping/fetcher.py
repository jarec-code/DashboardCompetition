"""HTTP fetching for scraping: SSRF-checked, size-limited, redirect-checked, robots.txt-aware.

Errors are classified so the worker can decide whether to retry:
  temporary  timeouts, connection errors, HTTP 429 and 5xx, robots.txt temporarily unavailable
  permanent  blocked URLs, 4xx, not-HTML content, oversized pages, bot protection challenges
"""
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import Settings, get_settings
from app.scraping.urlsafety import Resolver, UnsafeUrl, check_url, is_public_ip, system_resolver

MAX_REDIRECTS = 5
TEXT_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml", "application/json",
              "application/ld+json", "text/plain")
TEST_HOST_SUFFIX = ".e2e.test"
_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


class FetchError(Exception):
    def __init__(self, message: str, kind: str = "temporary", status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


@dataclass
class FetchResult:
    url: str
    status: int
    text: str | None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    elapsed_ms: int = 0
    redirects: list[str] = field(default_factory=list)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _decode(body: bytes, response: httpx.Response) -> str:
    charset = response.charset_encoding
    if not charset:
        match = _META_CHARSET.search(body[:4096])
        charset = match.group(1).decode() if match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, settings: Settings | None = None, resolver: Resolver = system_resolver,
                 transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.resolver = resolver
        self.client = httpx.Client(
            transport=transport, follow_redirects=False,
            headers={"User-Agent": self.settings.scraper_user_agent,
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                     "Accept-Language": "cs,sk;q=0.9,en;q=0.5"},
        )
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._robots_text: dict[str, str] = {}

    def close(self) -> None:
        self.client.close()

    # ---------- public API ----------

    def fetch(self, url: str, timeout: float, etag: str | None = None, last_modified: str | None = None,
              max_bytes: int | None = None) -> FetchResult:
        started = time.monotonic()
        redirects: list[str] = []
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            target = self._safe_target(current)
            response = self._request(target, current, timeout, headers, max_bytes)
            if isinstance(response, _Redirect):
                if not response.location:
                    raise FetchError(f"Presmerovanie (HTTP {response.status}) bez cieľovej adresy.", "permanent")
                redirects.append(current)
                current = urljoin(current, response.location)  # checked again at the top of the loop
                continue
            response.url, response.redirects = current, redirects
            response.elapsed_ms = int((time.monotonic() - started) * 1000)
            return response
        raise FetchError(f"Príliš veľa presmerovaní (viac ako {MAX_REDIRECTS}).", "permanent")

    def robots_cached(self, url: str) -> bool:
        parts = urlsplit(url)
        cached = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        return cached is not None and cached[0] >= time.monotonic()

    def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached is None or cached[0] < time.monotonic():
            self._robots[origin] = (time.monotonic() + self.settings.robots_cache_seconds, self._load_robots(origin))
        parser = self._robots[origin][1]
        return parser is None or parser.can_fetch(self._agent, url)

    @property
    def _agent(self) -> str:
        return self.settings.scraper_user_agent.split("/")[0]

    def _robots_parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        self.robots_allowed(url)  # loads (and caches) robots.txt of the origin
        parts = urlsplit(url)
        return self._robots[f"{parts.scheme}://{parts.netloc}"][1]

    def request_interval(self, url: str, now: datetime | None = None) -> float | None:
        """Seconds between requests demanded by robots.txt Request-rate (e.g. "18/1m 2100-0529", times in UTC)."""
        self._robots_parser(url)
        parts = urlsplit(url)
        text = self._robots_text.get(f"{parts.scheme}://{parts.netloc}") or ""
        return request_rate_interval(text, self._agent, now or datetime.now(UTC))

    def min_interval(self, url: str) -> float:
        """The longer of Crawl-delay and Request-rate (0 when robots.txt says nothing)."""
        return max(self.crawl_delay(url) or 0, self.request_interval(url) or 0)

    def crawl_delay(self, url: str) -> float | None:
        """Crawl-delay for our user agent from robots.txt (None when not set)."""
        parser = self._robots_parser(url)
        delay = parser.crawl_delay(self._agent) if parser is not None else None
        return float(delay) if delay is not None else None

    def sitemaps(self, url: str) -> list[str]:
        """Sitemap URLs announced in robots.txt."""
        parser = self._robots_parser(url)
        return list(parser.site_maps() or []) if parser is not None else []

    # ---------- internals ----------

    def _is_test_host(self, host: str) -> bool:
        return bool(self.settings.scraper_test_origin) and host.endswith(TEST_HOST_SUFFIX)

    def _safe_target(self, url: str) -> str:
        """Return the URL to request (checked against SSRF)."""
        parts = urlsplit(url)
        if self._is_test_host(parts.hostname or ""):
            return self.settings.scraper_test_origin.rstrip("/") + (parts.path or "/") + (
                f"?{parts.query}" if parts.query else "")
        if self.settings.scraper_test_origin:
            raise FetchError("V testovacom režime sa načítavajú iba domény *.e2e.test.", "permanent")
        try:
            return check_url(url, self.resolver)
        except UnsafeUrl as exc:
            raise FetchError(str(exc), "permanent") from None
        except OSError as exc:
            raise FetchError(str(exc), "temporary") from None

    def _request(self, target: str, original: str, timeout: float, headers: dict, max_bytes: int | None):
        limit = max_bytes or self.settings.scraper_max_bytes
        try:
            with self.client.stream("GET", target, headers=headers, timeout=timeout) as resp:
                self._check_peer(resp, original)
                status = resp.status_code
                if status in (301, 302, 303, 307, 308):
                    return _Redirect(status, resp.headers.get("location"))
                if status == 304:
                    return FetchResult(url=original, status=304, text=None, etag=resp.headers.get("etag"),
                                       last_modified=resp.headers.get("last-modified"), not_modified=True)
                body = self._read_limited(resp, limit) if status < 400 or status in (403, 503) else b""
                self._raise_for_status(resp, body)
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type and content_type not in TEXT_TYPES:
                    raise FetchError(f"Stránka nie je HTML (typ obsahu {content_type}).", "permanent", status)
                return FetchResult(url=original, status=status, text=_decode(body, resp),
                                   etag=resp.headers.get("etag"), last_modified=resp.headers.get("last-modified"))
        except httpx.TimeoutException:
            raise FetchError(f"Časový limit {timeout:g} s vypršal.", "temporary") from None
        except httpx.TransportError as exc:
            raise FetchError(f"Chyba spojenia: {exc.__class__.__name__}.", "temporary") from None

    def _check_peer(self, resp: httpx.Response, original: str) -> None:
        if self._is_test_host(urlsplit(original).hostname or ""):
            return
        stream = resp.extensions.get("network_stream")
        server = stream.get_extra_info("server_addr") if stream is not None else None
        if server and not is_public_ip(server[0]):
            raise FetchError(f"Spojenie smerovalo na neverejnú adresu ({server[0]}), požiadavka zablokovaná.",
                             "permanent")

    @staticmethod
    def _read_limited(resp: httpx.Response, limit: int) -> bytes:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise FetchError(f"Stránka je väčšia ako {limit // 1024} kB.", "permanent", resp.status_code)
        chunks, size = [], 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise FetchError(f"Stránka je väčšia ako {limit // 1024} kB.", "permanent", resp.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _raise_for_status(resp: httpx.Response, body: bytes) -> None:
        status = resp.status_code
        challenge = resp.headers.get("cf-mitigated") == "challenge" or (
            status in (403, 503) and b"challenge-platform" in body[:20000])
        if challenge:
            raise FetchError("Web vyžaduje overenie proti botom (napr. Cloudflare). Ochranu neobchádzame.",
                             "permanent", status)
        if status == 429:
            raise FetchError("Web hlási príliš veľa požiadaviek (HTTP 429).", "temporary", status,
                             _retry_after(resp.headers.get("retry-after")))
        if status >= 500:
            raise FetchError(f"Chyba servera webu (HTTP {status}).", "temporary", status,
                             _retry_after(resp.headers.get("retry-after")))
        if status in (404, 410):
            raise FetchError(f"Stránka neexistuje (HTTP {status}).", "permanent", status)
        if status >= 400:
            raise FetchError(f"Web odmietol požiadavku (HTTP {status}).", "permanent", status)

    def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        """None means 'no rules' (robots.txt missing)."""
        try:
            result = self.fetch(origin + "/robots.txt", timeout=10, max_bytes=512 * 1024)
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                return None  # no robots.txt: everything allowed
            raise FetchError(f"robots.txt nie je dostupný ({exc}); kontrola sa odkladá.", "temporary",
                             exc.status, exc.retry_after) from None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse((result.text or "").splitlines())
        self._robots_text[origin] = result.text or ""
        return parser


_RATE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*([smh]?)\s*(?:(\d{4})\s*-\s*(\d{4}))?", re.I)


def request_rate_interval(robots: str, agent: str, now: datetime) -> float | None:
    """Request-rate of the group for our agent (or "*"): the interval valid at `now` (UTC, the window of the
    extension is given in GMT), or the strictest one when no window matches. None without Request-rate."""
    groups: list[tuple[set[str], list[str]]] = []
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not groups or groups[-1][1] or groups[-1][1] is None:
                groups.append((set(), []))
            groups[-1][0].add(value.lower())
        elif groups:
            groups[-1][1].append(value if key == "request-rate" else "")
    own = [rules for agents, rules in groups if agent.lower() in agents]
    chosen = own[0] if own else next((rules for agents, rules in groups if "*" in agents), [])
    intervals: list[tuple[float, str | None, str | None]] = []
    for value in chosen:
        match = _RATE.match(value) if value else None
        if not match or int(match.group(1)) == 0:
            continue
        seconds = int(match.group(2)) * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(3).lower()]
        intervals.append((seconds / int(match.group(1)), match.group(4), match.group(5)))
    if not intervals:
        return None
    hhmm = now.astimezone(UTC).strftime("%H%M")
    for interval, start, end in intervals:
        if start and end and (start <= hhmm <= end if start <= end else hhmm >= start or hhmm <= end):
            return interval
    return max(i for i, _, _ in intervals)


@dataclass
class _Redirect:
    status: int
    location: str | None
