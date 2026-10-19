from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.scraping.fetcher import Fetcher, FetchError
from app.scraping.normalize import find_identifiers, normalize_availability
from app.scraping.parsers.base import PageContext, ParseError
from app.scraping.parsers.registry import get_parser, validate_parser_config
from app.scraping.urlsafety import UnsafeUrl, check_url, is_public_ip

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
PUBLIC_IP = "93.184.216.34"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse(parser: str, fixture: str, config: dict | None = None, variant: str = ""):
    return get_parser(parser).parse(PageContext(html=page(fixture), url="https://www.alter.cz/produkt/1",
                                                config=config or {}, variant=variant))


# ---------- SSRF ----------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
])
def test_private_addresses_blocked(address):
    assert not is_public_ip(address)
    with pytest.raises(UnsafeUrl):
        check_url("https://shop.example.cz/x", resolver=lambda host, port: [address])


def test_public_address_allowed_and_any_private_record_blocks():
    assert check_url("https://shop.example.cz/x", resolver=lambda h, p: [PUBLIC_IP]) == "https://shop.example.cz/x"
    with pytest.raises(UnsafeUrl):
        check_url("https://shop.example.cz/x", resolver=lambda h, p: [PUBLIC_IP, "10.0.0.7"])
    for url in ("ftp://shop.example.cz", "http://127.0.0.1/", "https://intranet/", "https://a.cz:8443/"):
        with pytest.raises(UnsafeUrl):
            check_url(url, resolver=lambda h, p: [PUBLIC_IP])


# ---------- fetcher ----------

def make_fetcher(routes: dict, resolver=None) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request) if callable(route) else route
    settings = Settings(scraper_max_bytes=100 * 1024)
    return Fetcher(settings, resolver=resolver or (lambda h, p: [PUBLIC_IP]), transport=httpx.MockTransport(handler))


HTML = {"content-type": "text/html; charset=utf-8"}


def test_fetch_ok_and_conditional_headers():
    seen = {}

    def ok(request):
        seen.update(request.headers)
        return httpx.Response(200, text="<h1>ok</h1>", headers={**HTML, "etag": '"v1"'})
    result = make_fetcher({"https://shop.cz/p": ok}).fetch("https://shop.cz/p", timeout=5, etag='"v0"')
    assert (result.status, result.text, result.etag) == (200, "<h1>ok</h1>", '"v1"')
    assert seen["if-none-match"] == '"v0"' and "CompetitionChecker" in seen["user-agent"]


def test_redirect_to_private_address_is_blocked():
    routes = {"https://shop.cz/p": httpx.Response(302, headers={"location": "https://evil.cz/meta"})}
    resolver = lambda host, port: ["169.254.169.254"] if host == "evil.cz" else [PUBLIC_IP]  # noqa: E731
    with pytest.raises(FetchError) as exc:
        make_fetcher(routes, resolver).fetch("https://shop.cz/p", timeout=5)
    assert exc.value.kind == "permanent" and "neverejnú" in str(exc.value)


def test_redirect_loop_limited():
    routes = {f"https://shop.cz/{i}": httpx.Response(301, headers={"location": f"/{i + 1}"}) for i in range(10)}
    with pytest.raises(FetchError, match="Príliš veľa presmerovaní"):
        make_fetcher(routes).fetch("https://shop.cz/0", timeout=5)


def test_relative_redirect_followed():
    routes = {"https://shop.cz/old": httpx.Response(301, headers={"location": "/new"}),
              "https://shop.cz/new": httpx.Response(200, text="new", headers=HTML)}
    result = make_fetcher(routes).fetch("https://shop.cz/old", timeout=5)
    assert (result.url, result.redirects, result.text) == ("https://shop.cz/new", ["https://shop.cz/old"], "new")


@pytest.mark.parametrize("response, kind, fragment", [
    (httpx.Response(404), "permanent", "neexistuje"),
    (httpx.Response(403, text="forbidden"), "permanent", "odmietol"),
    (httpx.Response(503, text="down"), "temporary", "Chyba servera"),
    (httpx.Response(429, headers={"retry-after": "120"}), "temporary", "429"),
    (httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}), "permanent", "nie je HTML"),
    (httpx.Response(200, content=b"x" * (200 * 1024), headers=HTML), "permanent", "väčšia"),
    (httpx.Response(403, headers={"cf-mitigated": "challenge"}), "permanent", "Ochranu neobchádzame"),
])
def test_error_classification(response, kind, fragment):
    with pytest.raises(FetchError) as exc:
        make_fetcher({"https://shop.cz/p": response}).fetch("https://shop.cz/p", timeout=5)
    assert exc.value.kind == kind and fragment in str(exc.value)
    if response.status_code == 429:
        assert exc.value.retry_after == 120


def test_timeout_is_temporary():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(FetchError) as exc:
        make_fetcher({"https://shop.cz/p": slow}).fetch("https://shop.cz/p", timeout=1)
    assert exc.value.kind == "temporary"


def test_not_modified():
    result = make_fetcher({"https://shop.cz/p": httpx.Response(304)}).fetch("https://shop.cz/p", timeout=5,
                                                                         etag='"v1"')
    assert result.not_modified and result.text is None


def test_charset_from_meta():
    body = '<meta charset="windows-1250"><h1>Žluťoučký kůň</h1>'.encode("cp1250")
    result = make_fetcher({"https://shop.cz/p": httpx.Response(200, content=body,
                                                               headers={"content-type": "text/html"})}).fetch(
        "https://shop.cz/p", timeout=5)
    assert "Žluťoučký kůň" in result.text


def test_robots_rules_and_cache():
    calls = []

    def robots(request):
        calls.append(1)
        return httpx.Response(200, text="User-agent: *\nDisallow: /kosik/\n", headers={"content-type": "text/plain"})
    fetcher = make_fetcher({"https://shop.cz/robots.txt": robots})
    assert fetcher.robots_allowed("https://shop.cz/produkt/1")
    assert not fetcher.robots_allowed("https://shop.cz/kosik/obsah")
    assert fetcher.robots_cached("https://shop.cz/x") and len(calls) == 1
    assert make_fetcher({}).robots_allowed("https://shop.cz/produkt/1")  # missing robots.txt: allowed
    with pytest.raises(FetchError) as exc:
        make_fetcher({"https://shop.cz/robots.txt": httpx.Response(503)}).robots_allowed("https://shop.cz/p")
    assert exc.value.kind == "temporary"


# ---------- normalization ----------

@pytest.mark.parametrize("raw, expected", [
    ("https://schema.org/InStock", "in_stock"), ("http://schema.org/OutOfStock", "out_of_stock"),
    ("PreOrder", "preorder"), ("Skladem", "in_stock"), ("Na sklade", "in_stock"), ("Není skladem", "out_of_stock"),
    ("Vypredané", "out_of_stock"), ("Připravujeme", "preorder"), ("", "unknown"), (None, "unknown"),
    ("Informace v e-shopu", "unknown"),
])
def test_availability(raw, expected):
    assert normalize_availability(raw) == expected


def test_find_identifiers():
    assert find_identifiers("Popis… EAN: 8594165541796 …") == {"ean": "8594165541796"}
    assert find_identifiers("ISBN 978-0-306-40615-7") == {"isbn": "9780306406157"}
    assert find_identifiers("Kód 9780306406157 na obálke") == {"ean": "9780306406157", "isbn": "9780306406157"}
    assert find_identifiers("tel. 4006381333931") == {}           # bare non-ISBN numbers are ignored
    assert find_identifiers("EAN: 8594165541797") == {}           # wrong check digit


# ---------- parsers ----------

def test_alter_printed_variant_by_default():
    data = parse("alter_cz", "alter_cz/produkt_1111.html")
    assert (data.price, data.currency, data.availability, data.variant) == (Decimal("44.00"), "CZK", "in_stock",
                                                                            "tištěná")
    assert data.title == "PRACOVNÍ LISTY k učebnici NAŠE VLAST (nové vydání)"
    assert data.identifiers == {"ean": "8594165541796"}


def test_alter_prvouka_guide():
    data = parse("alter_cz", "alter_cz/produkt_716.html")
    assert (data.price, data.availability) == (Decimal("65.00"), "in_stock")
    assert data.title.startswith("PRŮVODCE učebnicí PRVOUKY pro 1. ročník")
    assert data.identifiers == {"ean": "8594165541536"}


def test_alter_electronic_only_and_missing_variant():
    data = parse("alter_cz", "alter_cz/produkt_181.html")
    assert data.price == Decimal("1300.00") and data.variant.startswith("elektronická")
    assert data.availability == "in_stock"
    with pytest.raises(ParseError, match="Variant „tištěná“ sa na stránke nenašiel"):
        parse("alter_cz", "alter_cz/produkt_181.html", variant="tištěná")


def test_alter_changed_layout_is_a_parse_error():
    with pytest.raises(ParseError, match="Web mohol zmeniť štruktúru"):
        parse("alter_cz", "generic/no_data.html")


def test_css_and_xpath_on_alter_page():
    css = validate_parser_config("css", {"price": ".vatPricePD", "name": "h1",
                                         "availability": '.detail_key:-soup-contains("Dostupnost") + .detail_value'})
    data = parse("css", "alter_cz/produkt_1111.html", css)
    assert (data.price, data.currency, data.availability) == (Decimal("44.00"), "CZK", "in_stock")
    xpath = validate_parser_config("xpath", {"price": "//div[contains(@class,'vatPricePD')]", "name": "//h1"})
    data = parse("xpath", "alter_cz/produkt_716.html", xpath)
    assert data.price == Decimal("65.00") and data.title.startswith("PRŮVODCE")
    with pytest.raises(ParseError, match="Cena sa nenašla"):
        parse("css", "generic/no_data.html", {"price": ".price"})


def test_structured_data_parsers():
    j = parse("jsonld", "generic/jsonld_product.html")
    assert (j.price, j.original_price, j.currency, j.availability, j.vat_mode) == (
        Decimal("129.90"), Decimal("149.00"), "CZK", "in_stock", "with_vat")
    assert j.identifiers == {"ean": "9780306406157", "isbn": "9780306406157", "sku": "MAT-2"}
    m = parse("microdata", "generic/microdata_product.html")
    assert (m.price, m.currency, m.availability, m.title, m.identifiers) == (
        Decimal("3.50"), "EUR", "out_of_stock", "Písanka 1", {"ean": "8594165541796"})
    o = parse("opengraph", "generic/opengraph_product.html")
    assert (o.price, o.currency, o.availability) == (Decimal("7.20"), "EUR", "preorder")
    assert parse("auto", "generic/microdata_product.html").source == "microdata"
    # Without structured data the text of the page is read (the same result as the alter.cz parser).
    page = parse("auto", "alter_cz/produkt_1111.html")
    assert (page.price, page.source) == (Decimal("44.00"), "heuristic")
    with pytest.raises(ParseError, match="Nastavte CSS selektory"):
        parse("auto", "alter_cz/home.html")


def test_parser_config_validation():
    with pytest.raises(ValueError):
        validate_parser_config("xpath", {"price": "//div[("})
    with pytest.raises(ValueError):
        validate_parser_config("alter_cz", {"price": ".x"})
    with pytest.raises(ValueError):
        validate_parser_config("python", {})


def test_test_mode_fetches_only_test_hosts():
    fetcher = Fetcher(Settings(scraper_test_origin="http://127.0.0.1:9"), resolver=lambda h, p: [PUBLIC_IP],
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x", headers=HTML)))
    with pytest.raises(FetchError, match="testovacom režime"):
        fetcher.fetch("https://www.alter.cz/produkt/1", timeout=5)
    assert fetcher.fetch("https://shop.e2e.test/p", timeout=5).text == "x"
