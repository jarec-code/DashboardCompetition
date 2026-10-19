"""URL normalization, URL templates, product links by query, page text parser, robots.txt Request-rate."""
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.scraping.discover import discover_links, query_says_product
from app.scraping.fetcher import request_rate_interval
from app.scraping.parsers.base import PageContext, ParseError
from app.scraping.parsers.heuristic import parse_heuristic
from app.scraping.urlnorm import canonical, template
from app.services.discovery import _catalogue_links

PROMETHEUS = Path(__file__).parent.parent / "fixtures" / "html" / "prometheus"


def page(name: str) -> str:
    return (PROMETHEUS / name).read_bytes().decode("cp1250")


def test_canonical_and_template():
    assert canonical("HTTPS://Prometheus-Nakl.cz/index.php?zobraz=detail&tisk=ano&PHPSESSID=x&id_katalog=306#a") == \
        "https://prometheus-nakl.cz/index.php?id_katalog=306&zobraz=detail"
    assert canonical("https://x.cz/a?b=2&utm_source=n&a=1&sort=p", {"sort"}) == "https://x.cz/a?a=1&b=2"
    assert template("https://prometheus-nakl.cz/index.php?zobraz=detail&id_katalog=306") == \
        "/index.php?id_katalog={N}&zobraz=detail"
    assert template("https://www.alter.cz/produkt/716") == "/produkt/{N}"
    assert template("https://www.etaktik.cz/hrava-abeceda/") == "/{S}/"
    assert template("https://x.cz/hledat?q=Matematika 2") == "/hledat?q={V}"


def test_products_addressed_by_query_and_catalogue_behind_an_image():
    assert query_says_product("zobraz=detail&id_katalog=306") and query_says_product("product_id=5")
    assert not query_says_product("zobraz=id2&id2=1") and not query_says_product("cast=5")
    links = discover_links(page("katalog.html"), "https://prometheus-nakl.cz/index.php?zobraz=katalog",
                           "prometheus-nakl.cz", set())
    assert len(links) == 22 and all("id_katalog=" in c["url"] for c in links)
    assert _catalogue_links(page("home.html"), "https://prometheus-nakl.cz/", "prometheus-nakl.cz") == [
        "https://prometheus-nakl.cz/index.php?zobraz=katalog"]  # the menu is images with alt text


def test_heuristic_parser_reads_prices_from_page_text():
    data = parse_heuristic(PageContext(html=page("detail_306.html"), url="https://prometheus-nakl.cz/", config={}))
    assert (data.price, data.currency, data.title, data.identifiers) == (
        Decimal("238.00"), "CZK", "Matematika pro SŠ – Stereometrie – také E-KNIHA", {"isbn": "9788071965237"})
    two = parse_heuristic(PageContext(html=page("detail_2.html"), url="https://prometheus-nakl.cz/", config={}))
    assert (two.price, two.title) == (Decimal("69.00"), "Matematika pro 1. ročník základní školy – Svět čísel a "
                                                        "tvarů, pracovní sešit 1")
    for listing in ("katalog.html", "id2_1.html", "home.html"):  # many prices, no label: not one product
        with pytest.raises(ParseError):
            parse_heuristic(PageContext(html=page(listing), url="https://prometheus-nakl.cz/", config={}))
    single = "<html><body><h1>Kniha</h1><p>Jen 1 299,- Kč</p></body></html>"
    assert parse_heuristic(PageContext(html=single, url="https://x.cz/", config={})).price == Decimal("1299")


def test_request_rate_from_robots():
    robots = (PROMETHEUS / "robots.txt").read_text(encoding="cp1250")
    night, day = datetime(2026, 10, 19, 23, 0, tzinfo=UTC), datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    assert request_rate_interval(robots, "CompetitionChecker", night) == pytest.approx(60 / 18)
    assert request_rate_interval(robots, "CompetitionChecker", day) == 10
    other_group = "User-agent: Googlebot\nRequest-rate: 1/1s\n\nUser-agent: *\nDisallow: /kosik/\n"
    assert request_rate_interval(other_group, "CompetitionChecker", day) is None
    assert request_rate_interval("User-agent: *\nRequest-rate: 1/5\n", "CompetitionChecker", day) == 5


def test_heuristic_prefers_the_price_with_vat_for_everyone():
    html = ("<html><body><h1>Český slabikář 1</h1><p>ISBN: 978-80-7230-170-6 cena bez DPH: 97.14 Kč "
            "cena vč. DPH: 102.00 Kč Vaše cena vč. DPH: 91.80 Kč (sleva 10 % pro školy)</p></body></html>")
    data = parse_heuristic(PageContext(html=html, url="https://x.cz/", config={}))
    assert (data.price, data.vat_mode, data.identifiers) == (Decimal("102.00"), "with_vat", {"isbn": "9788072301706"})
    only_net = "<h1>Kniha</h1><p>Cena bez DPH: 90 Kč</p>"
    net = parse_heuristic(PageContext(html=only_net, url="https://x.cz/", config={}))
    assert (net.price, net.vat_mode) == (Decimal("90"), "without_vat")


def test_a_condition_amount_is_not_a_price():
    order = parse_heuristic  # "Poštovné ani balné neúčtujeme, je-li fakturovaná částka vyšší než 500,- Kč"
    with pytest.raises(ParseError, match="nie je cena"):
        order(PageContext(html=page("jak_objednavat.html"), url="https://prometheus-nakl.cz/", config={}))
    far = "<h1>Kniha</h1>" + "<p>text</p>" * 200 + "<p>Doprava zdarma od 700 Kč</p>"
    with pytest.raises(ParseError):
        parse_heuristic(PageContext(html=far, url="https://x.cz/", config={}))


def test_cart_and_login_pages_are_never_entry_pages_nor_followed():
    home = ('<a href="/obchod/index.php?item=nakupni-kosik">Košík</a><a href="/obchod/?item=prihlasit">Přihlásit</a>'
            '<a href="/obchod/?item=zaregistrovat">Registrace</a><a href="/obchod/?item=nabidka">E-shop</a>')
    assert _catalogue_links(home, "https://www.nns.cz/", "nns.cz") == ["https://www.nns.cz/obchod/?item=nabidka"]


def test_tab_segments_need_many_listed_parents():
    from app.scraping.catalog import is_tab, tab_segments

    books = [f"/fyzika/kniha-{n}" for n in range(12)]
    paths = books + [f"{b}/{t}" for b in books for t in ("obsah", "ukazky")] + ["/fyzika", "/o-nas/kontakt"]
    assert tab_segments(paths) == {"obsah", "ukazky"}
    assert is_tab("/fyzika/kniha-3/obsah", {"obsah"}) and not is_tab("/obsah", {"obsah"})
    few = books[:5] + [f"{b}/obsah" for b in books[:5]]  # five books with a tab: an ordinary category
    assert tab_segments(few) == set()
    assert tab_segments([f"/kategorie/{n}" for n in range(30)] + ["/kategorie"]) == set()  # children of one page


def test_grade_from_the_number_after_the_subject_or_the_pupils():
    from app.services.comparable import _grades, plain

    cases = {"Barevná čeština 1": {"1"}, "Barevná čeština pro prvňáčky, pracovní sešit": {"1"},
             "Barevná čeština pro třeťáky": {"3"}, "Prvouka 2 pro ZŠ, pracovní sešit": {"2"},
             "Matematika 1 – 3. díl": {"1"}, "Český jazyk 9, 1. díl: Učivo o jazyce": {"9"},
             "Přírodopis 6 – Rostliny": {"6"}, "Hudební výchova pro 2. stupeň ZŠ": set(),
             "Angličtina 2 díl": set(), "Matematika pro 3. ročník": {"3"}}
    for title, grades in cases.items():
        assert _grades(plain(title)) == grades, title
