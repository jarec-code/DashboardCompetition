"""Automatic product discovery (docs/AUTO_DISCOVERY_PLAN.md), against fake websites only."""
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    CatalogItem,
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    DiscoveryRun,
    DiscoveryUrl,
    PriceHistory,
    Product,
    ProductPrice,
    ScrapeConfig,
    ScrapeRun,
)
from app.scraping import crawl
from app.scraping.fetcher import Fetcher
from app.scraping.urlnorm import template
from app.services import discovery, scheduling
from app.worker.scheduler import due_offers_query

ALTER = Path(__file__).parent.parent / "fixtures" / "html" / "alter_cz"
PAGE_0 = (ALTER / "shop_page_0.html").read_text(encoding="utf-8")
PAGE_LAST = (ALTER / "shop_page_11.html").read_text(encoding="utf-8")


class FakeSite:
    """A website: path (with query) -> (status, body). Records every request."""

    def __init__(self, pages: dict[str, tuple]):
        self.pages = pages
        self.requests: list[str] = []

    def fetcher(self) -> Fetcher:
        def handle(request: httpx.Request) -> httpx.Response:
            target = request.url.raw_path.decode()
            self.requests.append(target)
            status, body, *kind = self.pages.get(target, (404, "<h1>Nenalezeno</h1>"))
            if isinstance(body, bytes):  # a saved page in its own encoding, e.g. windows-1250
                return httpx.Response(status, content=body, headers={"content-type": kind[0]})
            kind = "application/xml" if body.lstrip().startswith("<?xml") else "text/html; charset=utf-8"
            return httpx.Response(status, text=body, headers={"content-type": kind})
        return Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"], transport=httpx.MockTransport(handle))


def competitor(db, name: str, url: str, domain: str, mode: str = "shadow", **settings) -> Competitor:
    c = Competitor(name=name, base_url=url, domain=domain, country="CZ", market="CZ",
                   scrape_config=ScrapeConfig(min_interval_seconds=5))
    db.add(c)
    db.flush()
    discovery.save(c.scrape_config, discovery.DiscoverySettings(
        mode=mode, terms_confirmed_at=datetime.now(UTC), run_requested=True, **settings))
    db.commit()
    return c


def product(db, name: str, price: str, **fields) -> Product:
    p = Product(name=name, **fields)
    p.prices = [ProductPrice(market="CZ", price=Decimal(price), currency="CZK")]
    db.add(p)
    db.commit()
    return p


def run_all(db, site: FakeSite, now: datetime | None = None, limit: int = 300) -> DiscoveryRun | None:
    """Advance discovery as the worker would; time between requests is skipped."""
    politeness = discovery.Politeness({}, {})
    fetcher = site.fetcher()
    for _ in range(limit):
        discovery.step(db, fetcher, politeness, now)
        politeness.last_request.clear()
        if not db.scalar(select(func.count(DiscoveryRun.id)).where(DiscoveryRun.status == "running")):
            break
    return db.scalar(select(DiscoveryRun).order_by(DiscoveryRun.id.desc()))


def settings_of(db, c: Competitor) -> discovery.DiscoverySettings:
    db.refresh(c.scrape_config)
    return discovery.load(c.scrape_config)


def request_again(db, c: Competitor) -> None:
    discovery.request_run(c.scrape_config)
    db.commit()


def alter_site(last_page: str = PAGE_LAST) -> FakeSite:
    return FakeSite({"/robots.txt": (200, "User-agent: *\nAllow: /\n"), "/shop": (200, PAGE_0),
                     "/shop?page=1": (200, last_page)})


def test_alter_listing_shadow_then_automatic_then_daily_prices(db):
    alter = competitor(db, "Alter", "https://www.alter.cz/", "alter.cz")
    citanka = product(db, "Hravá čítanka 9", "279", attributes={"grade": "9. ročník", "subject": "Čeština",
                                                                 "type": "Učebnice"})
    product(db, "Záložka Abeceda", "14")  # no grade: never a candidate
    site = alter_site()

    run = run_all(db, site)
    assert run.status == "succeeded" and run.source_type == "adapter"
    assert site.requests == ["/robots.txt", "/shop", "/shop?page=1"]  # the adapter knows the listing: no guessing
    assert run.stats["found"] == 31 and run.stats["new"] == 31 and run.stats["matched"] == 1
    assert run.stats["would_track"] == 1 and run.stats["quality_ok"] is True, run.stats.get("quality_note")
    item = db.scalar(select(CatalogItem).where(CatalogItem.key == "polozka:14"))
    assert (item.title, item.price, item.price_from_listing, item.hints) == (
        "ČÍTANKA 9", Decimal("180.00"), True, {"grade": "9. ročník"})
    assert (item.match_status, item.match_method, item.product_id) == ("matched", "attributes", citanka.id)
    review = db.scalar(select(CatalogItem).where(CatalogItem.key == "polozka:132"))  # LITERÁRNÍ VÝCHOVA k Čítance 9
    assert review.match_status == "review" and review.candidates[0]["product_id"] == citanka.id
    assert db.scalar(select(func.count(CompetitorProduct.id))) == 0  # shadow mode: nothing tracked
    state = settings_of(db, alter)
    assert state.mode == "shadow" and state.shadow_ok_runs == 1
    assert timedelta(hours=23) < state.next_run_at - run.started_at < timedelta(hours=25)  # 2nd run a day later

    request_again(db, alter)
    run = run_all(db, site)
    assert run.stats["promoted"] is True and run.stats["tracked_new"] == 1
    assert settings_of(db, alter).mode == "auto"
    offer = db.scalar(select(CompetitorProduct))
    assert (offer.product_id, offer.matching_method, offer.price_source, offer.external_product_id) == (
        citanka.id, "attributes", "listing", "14")
    assert db.scalar(select(CurrentPrice.price).where(CurrentPrice.competitor_product_id == offer.id)) == 180
    assert db.scalar(select(func.count()).select_from(due_offers_query(datetime.now(UTC)).subquery())) == 0

    # Daily prices run: the listing only, price change recorded once, logged as a scrape run.
    changed = PAGE_LAST.replace('class="priceNum">180<', 'class="priceNum">185<')
    assert changed != PAGE_LAST
    site = alter_site(changed)
    run = run_all(db, site, now=datetime.now(UTC) + timedelta(hours=25))
    assert run.kind == "prices" and run.status == "succeeded" and site.requests == ["/robots.txt", "/shop",
                                                                                  "/shop?page=1"]
    assert [h.price for h in db.scalars(select(PriceHistory).order_by(PriceHistory.id))] == [180, 185]
    log = db.scalar(select(ScrapeRun))
    assert (log.total_count, log.success_count, log.price_change_count) == (1, 1, 1)


def test_items_gone_after_two_runs_and_suspicious_drop_is_not_trusted(db):
    alter = competitor(db, "Alter", "https://www.alter.cz/", "alter.cz", mode="auto")
    product(db, "Hravá čítanka 9", "279", attributes={"grade": "9. ročník", "subject": "Čeština"})
    run_all(db, alter_site())
    offer = db.scalar(select(CompetitorProduct))
    assert offer.status == "active"

    without_last = alter_site(last_page="")  # the second page disappears: 7 of 31 products missing
    without_last.pages["/shop"] = (200, PAGE_0.replace('rel="next"', 'rel="nofollow"'))
    request_again(db, alter)
    run = run_all(db, without_last)
    assert run.status == "succeeded" and run.stats.get("gone") is None  # missing once: not gone yet
    request_again(db, alter)
    run = run_all(db, without_last)
    assert run.stats["gone"] == 7 and run.stats["archived"] == 1
    db.refresh(offer)
    assert offer.status == "archived"  # its history is kept

    empty = alter_site()
    empty.pages["/shop"] = (200, "<html><body>Údržba</body></html>")
    request_again(db, alter)
    run = run_all(db, empty)
    assert run.status == "stopped" and "nevrátil žiadne produkty" in run.error
    assert db.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.status == "gone")) == 7  # unchanged


def test_blocking_pauses_discovery_for_a_week(db):
    alter = competitor(db, "Alter", "https://www.alter.cz/", "alter.cz")
    site = alter_site()
    site.pages["/shop"] = (429, "Too many requests")
    run = run_all(db, site)
    assert run.status == "stopped" and "pozastavuje" in run.error
    state = settings_of(db, alter)
    assert state.paused_until > datetime.now(UTC) + timedelta(days=6)
    site.requests.clear()
    run_all(db, alter_site())
    assert site.requests == []  # paused: no request at all


def jsonld_page(name: str, price: str, isbn: str | None = None) -> str:
    data = {"@context": "https://schema.org", "@type": "Product", "name": name,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "CZK",
                       "availability": "https://schema.org/InStock"}}
    if isbn:
        data["isbn"] = isbn
    return f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body>' \
           f"<h1>{name}</h1></body></html>"


def test_generic_site_with_sitemap_from_robots(db):
    shop = competitor(db, "Fraus", "https://shop.example.cz/", "shop.example.cz", mode="auto")
    ours = product(db, "Hravá matematika 2", "114", isbn="9780306406157")
    sitemap = """<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://shop.example.cz/kniha/matematika-2</loc></url>
      <url><loc>https://shop.example.cz/kniha/prvouka-3</loc></url>
      <url><loc>https://shop.example.cz/o-nas</loc></url>
      <url><loc>https://jinde.cz/kniha/cizi</loc></url></urlset>"""
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\nAllow: /\nSitemap: https://shop.example.cz/mapa.xml\n"),
        "/mapa.xml": (200, sitemap),
        "/kniha/matematika-2": (200, jsonld_page("Matematika 2 (Fraus)", "129", isbn="978-0-306-40615-7")),
        "/kniha/prvouka-3": (200, jsonld_page("Prvouka 3 – učebnice pro 3. ročník", "99")),
        "/o-nas": (200, "<h1>O nás</h1><p>Jsme nakladatelství.</p>"),
    })
    run = run_all(db, site)
    assert run.source_type == "sitemap" and run.stats["detected"] == "sitemap"
    assert run.stats["found"] == 3 and run.stats["enriched"] == 3 and run.stats["not_product"] == 1
    assert "/kniha/cizi" not in "".join(site.requests)  # other domains are never read
    items = {i.key.rsplit("/", 1)[1]: i for i in db.scalars(select(CatalogItem))}
    assert (items["matematika-2"].match_status, items["matematika-2"].match_method) == ("matched", "identifier")
    assert items["o-nas"].match_status == "unrelated"
    offer = db.scalar(select(CompetitorProduct))
    assert (offer.product_id, offer.price_source, offer.matching_method) == (ours.id, "page", "identifier")
    assert settings_of(db, shop).source.urls == ["https://shop.example.cz/mapa.xml"]


def test_generic_site_without_sitemap_is_crawled_from_the_catalogue(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    cards = "".join(f'<div class="card"><a href="/produkt/{n}">Kniha {n}</a></div>' for n in range(1, 15))
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\nDisallow: /kosik/\n"),
        "/": (200, '<a href="/kontakt">Kontakt</a> <a href="/katalog">Katalog učebnic</a>'),
        "/katalog": (200, cards + '<a rel="next" href="/katalog?strana=2">další</a>'),
        "/katalog?strana=2": (200, '<div class="card"><a href="/produkt/15">Kniha 15</a></div>'),
        **{f"/produkt/{n}": (200, jsonld_page(f"Kniha {n}", str(100 + n))) for n in range(1, 16)},
    })
    run = run_all(db, site)
    assert run.status == "succeeded" and run.stats["detected"] == "crawl"
    assert run.stats["found"] == 15 and run.stats["products_read"] == 15  # pagination followed, details read
    assert site.requests.count("/produkt/1") == 1  # every page once
    attempts = [a["what"] for a in run.stats["attempts"]]
    assert attempts == ["robots.txt", "/sitemap.xml", "domovská stránka", "plán", "prechádzanie webu"]
    assert run.stats["methods"]["crawl"]["sufficient"] is True
    item = db.scalar(select(CatalogItem).where(CatalogItem.key == "https://knihy.example.cz/produkt/3"))
    assert (item.found_by, item.fetch_status, item.parsed_by, item.completeness) == ("crawl", "ok", "jsonld", 70)

    # Next run: listings are read again, product pages read before are not.
    site.requests.clear()
    db.refresh(db.scalar(select(Competitor).where(Competitor.domain == "knihy.example.cz")).scrape_config)
    c = db.scalar(select(Competitor).where(Competitor.domain == "knihy.example.cz"))
    request_again(db, c)
    run = run_all(db, site)
    assert run.stats["found"] == 15 and not any(r.startswith("/produkt/") for r in site.requests)
    assert run.stats.get("new") is None


def test_nothing_happens_without_consent_or_outside_the_window(db):
    c = competitor(db, "Alter", "https://www.alter.cz/", "alter.cz")
    settings = discovery.load(c.scrape_config)
    discovery.save(c.scrape_config, settings.model_copy(update={"terms_confirmed_at": None}))
    db.commit()
    site = alter_site()
    run_all(db, site)
    assert site.requests == []

    discovery.save(c.scrape_config, settings.model_copy(update={"run_requested": False}))
    scheduling.save_settings(db, scheduling.SchedulingSettings(window_start_hour=1, window_end_hour=2))
    db.commit()
    noon = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)  # 12:00 in Bratislava, outside 01–02
    run_all(db, site, now=noon)
    assert site.requests == []


def test_listing_offers_cannot_be_checked_by_hand(client, db):
    from tests.conftest import login, make_user

    c = competitor(db, "Alter", "https://www.alter.cz/", "alter.cz")
    offer = CompetitorProduct(competitor_id=c.id, product_url="https://www.alter.cz/shop#polozka-14",
                              price_source="listing")
    db.add(offer)
    db.commit()
    make_user("admin@example.com")
    login(client, "admin@example.com")
    r = client.post(f"/api/offers/{offer.id}/check")
    assert r.status_code == 409 and "zoznamu" in r.json()["detail"]


def test_settings_catalogue_and_decisions_api(client, db):
    from tests.conftest import login, make_user

    make_user("admin@example.com")
    login(client, "admin@example.com")
    c = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz", country="CZ", market="CZ",
                   scrape_config=ScrapeConfig())
    db.add(c)
    db.commit()
    url = f"/api/competitors/{c.id}/discovery"
    assert client.get(url).json()["settings"]["mode"] == "off"
    body = {"mode": "shadow", "terms_confirmed": False}
    assert client.put(url, json=body).status_code == 422  # terms first
    assert client.post(f"{url}/run").status_code == 409
    state = client.put(url, json={**body, "terms_confirmed": True, "interval_days": 7}).json()
    assert state["settings"]["terms_confirmed_by"] == "admin@example.com"
    assert client.post(f"{url}/run").json()["settings"]["run_requested"] is True
    bad = client.put(url, json={**body, "terms_confirmed": True, "source": {"type": "sitemap", "urls": ["ftp://x"]}})
    assert bad.status_code == 422

    db.expire_all()  # settings were changed through the API (another session)
    run_all(db, alter_site())
    page = client.get(f"/api/competitors/{c.id}/catalog", params={"match_status": "unrelated"}).json()
    assert page["total"] > 0 and page["items"][0]["attributes"]["note"]
    citanka = db.scalar(select(CatalogItem).where(CatalogItem.key == "polozka:14"))
    ours = product(db, "Čítanka pro 9. ročník", "250")
    r = client.post(f"/api/catalog-items/{citanka.id}/assign", json={"product_id": ours.id})
    assert r.status_code == 200 and (r.json()["match_method"], r.json()["product_name"]) == ("manual", ours.name)
    offer = db.get(CompetitorProduct, r.json()["offer_id"])
    assert (offer.price_source, offer.matching_method) == ("listing", "manual")
    assert db.scalar(select(CurrentPrice.price).where(CurrentPrice.competitor_product_id == offer.id)) == 180
    offer_out = client.get("/api/offers", params={"competitor_id": c.id}).json()["items"][0]
    assert offer_out["price_source"] == "listing"

    # An automatic match is undone with one click: the item is ignored and its offer archived.
    offer.matching_method = "attributes"
    db.commit()
    assert client.post(f"/api/catalog-items/{citanka.id}/ignore").json()["match_status"] == "ignored"
    db.refresh(offer)
    assert offer.status == "archived"
    assert client.post(f"/api/catalog-items/{citanka.id}/reset").json()["match_status"] == "new"
    assert client.get(url).json()["counts"]["new"] == 1
    monitoring = client.get("/api/monitoring").json()["discovery"]
    assert monitoring[0]["competitor_name"] == "Alter" and monitoring[0]["mode"] == "shadow"

    assert client.put(url, json={"mode": "off", "terms_confirmed": True}).json()["settings"]["mode"] == "off"


PROMETHEUS = Path(__file__).parent.parent / "fixtures" / "html" / "prometheus"


def prometheus_site() -> FakeSite:
    """Saved pages of prometheus-nakl.cz (windows-1250): products only behind query URLs, menu of images,
    the catalogue split into series; no sitemap, no structured data."""
    cp1250 = "text/html; charset=windows-1250"
    raw = lambda name: (PROMETHEUS / name).read_bytes()  # noqa: E731
    pages = {
        "/robots.txt": (200, (PROMETHEUS / "robots.txt").read_text(encoding="cp1250")),
        "/": (200, raw("home.html"), cp1250),
        "/index.php?zobraz=katalog": (200, raw("katalog.html"), cp1250),
        "/index.php?id2=1&zobraz=id2": (200, raw("id2_1.html"), cp1250),
        "/index.php?id3=1&zobraz=id3": (200, raw("id3_1.html"), cp1250),
    }
    detail = raw("detail_2.html")
    for n in range(1, 40):  # every detail answers with a saved detail page whose title and ISBN are made unique
        page = detail.replace("pracovní sešit 1".encode("cp1250"), f"díl {n}".encode("cp1250")) \
                     .replace(b"978-80-7196-310-3", f"978-80-7196-{n:03d}-0".encode())
        pages[f"/index.php?id_katalog={n}&zobraz=detail"] = (200, page, cp1250)
    pages["/index.php?id_katalog=306&zobraz=detail"] = (200, raw("detail_306.html"), cp1250)
    return FakeSite(pages)


def test_prometheus_is_crawled_through_its_catalogue_and_series(db):
    competitor(db, "Prometheus", "https://prometheus-nakl.cz/", "prometheus-nakl.cz", max_pages=120)
    prometheus = db.scalar(select(Competitor).where(Competitor.domain == "prometheus-nakl.cz"))
    prometheus.scrape_config.parser_type = "alter_cz"  # misconfigured as in production: the automatic parser helps
    db.commit()
    site = prometheus_site()
    run = run_all(db, site, limit=1000)
    assert run.stats["detected"] == "crawl", run.stats.get("attempts")
    assert run.status == "succeeded" and run.stats["broken_links"] > 0  # links to pages the fake site lacks
    details = {r for r in site.requests if "zobraz=detail" in r}
    items = list(db.scalars(select(CatalogItem)))
    assert len(items) >= 30 and all(i.price is not None and i.found_by == "crawl" for i in items)
    assert len(details) == len({r.split("&")[0] for r in details})  # no detail page read twice
    assert "/index.php?zobraz=katalog" in site.requests or "/index.php?zobraz=katalog" in str(site.requests)
    stereometrie = next(i for i in items if i.title == "Matematika pro SŠ – Stereometrie – také E-KNIHA")
    assert (stereometrie.price, stereometrie.identifiers, stereometrie.parsed_by) == (
        Decimal("238.00"), {"isbn": "9788071965237"}, "heuristic")
    assert not any("tisk=ano" in r or "hledany_text" in r for r in site.requests)  # print pages and search skipped
    rows = {r.url: r for r in db.scalars(select(DiscoveryUrl))}
    assert rows["https://prometheus-nakl.cz/index.php?zobraz=katalog"].kind == "listing"
    assert run.stats["templates"]["/index.php?id_katalog={N}&zobraz=detail"]["products"] >= 30


def test_crawl_limit_gives_a_partial_run_without_marking_anything_gone(db):
    competitor(db, "Prometheus", "https://prometheus-nakl.cz/", "prometheus-nakl.cz", max_pages=10)
    run = run_all(db, prometheus_site(), limit=1000)
    assert run.status == "partial" and "limit 10 stránok" in run.stats["incomplete"][0]
    assert run.stats["skipped_urls"] > 0 and run.stats["gone_check"].startswith("vynechané")
    skipped = db.scalar(select(func.count(DiscoveryUrl.id)).where(DiscoveryUrl.status == "skipped"))
    assert skipped == run.stats["skipped_urls"]


def test_sitemap_of_the_whole_website_skips_page_types_without_products(db):
    competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    locs = [f"https://ucebnice.example.cz/clanky/clanek-{n}" for n in range(1, 21)] + \
           [f"https://ucebnice.example.cz/kniha/{n}" for n in range(1, 4)]
    sitemap = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
              "".join(f"<url><loc>{u}</loc></url>" for u in locs) + "</urlset>"
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\nSitemap: https://ucebnice.example.cz/sitemap.xml\n"),
        "/sitemap.xml": (200, sitemap),
        **{f"/clanky/clanek-{n}": (200, f"<h1>Článek {n}</h1><p>Text bez ceny.</p>") for n in range(1, 21)},
        **{f"/kniha/{n}": (200, jsonld_page(f"Kniha {n}", "99")) for n in range(1, 4)},
    })
    run = run_all(db, site)
    articles = [r for r in site.requests if r.startswith("/clanky/")]
    assert len(articles) == 5  # after five articles without a price the rest is skipped without a request
    assert run.stats["skipped_urls"] == 15 and run.status == "succeeded"
    assert db.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.fetch_status == "skipped")) == 15
    assert db.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.price.is_not(None))) == 3


def test_an_empty_source_falls_back_to_the_crawler(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    empty = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' \
            "<url><loc>https://jinde.cz/a</loc></url></urlset>"  # only another domain: nothing usable
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\nSitemap: https://knihy.example.cz/sitemap.xml\n"),
        "/sitemap.xml": (200, empty),
        "/": (200, '<a href="/produkt/1">Kniha 1</a><a href="/produkt/2">Kniha 2</a>'),
        "/produkt/1": (200, jsonld_page("Kniha 1", "100")),
        "/produkt/2": (200, jsonld_page("Kniha 2", "120")),
    })
    run = run_all(db, site)
    assert run.status == "succeeded" and run.stats["plan"] == ["sitemap", "crawl"] and run.stats["found"] == 2
    assert run.stats["methods"]["sitemap"]["sufficient"] is False and run.stats["methods"]["crawl"]["found"] == 2
    assert "pokračuje prechádzanie webu" in next(a["result"] for a in run.stats["attempts"] if a["what"] == "sitemap")


def test_diagnostics_api(client, db):
    from tests.conftest import login, make_user

    c = competitor(db, "Prometheus", "https://prometheus-nakl.cz/", "prometheus-nakl.cz", max_pages=10)
    run_all(db, prometheus_site(), limit=1000)
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    urls = client.get(f"/api/competitors/{c.id}/discovery/urls").json()
    assert urls["by_status"]["skipped"] > 0 and urls["by_kind"]["listing"] >= 1
    assert urls["problems"][0]["status"] == "skipped" and "limit" in urls["problems"][0]["reason"]
    runs = client.get(f"/api/competitors/{c.id}/discovery").json()["runs"]
    assert runs[0]["status"] == "partial" and runs[0]["stats"]["attempts"]
    items = client.get(f"/api/competitors/{c.id}/catalog", params={"match_status": "unrelated"}).json()["items"]
    assert items and items[0]["found_by"] == "crawl" and items[0]["completeness"] == 85
    assert client.get(f"/api/competitors/{c.id}/catalog", params={"incomplete": True}).json()["total"] == 0


def bookshop(prices: list[int], sitemap: list[int] | None = None, home_extra: str = "") -> FakeSite:
    """A shop with books /kniha/N, a home page linking all of them, and optionally a sitemap of some books."""
    pages = {"/robots.txt": (200, "User-agent: *\n" + ("Sitemap: https://knihy.example.cz/sitemap.xml\n"
                                                         if sitemap else "")),
             "/": (200, "".join(f'<a href="/kniha/{n}">Kniha {n}</a>' for n in range(1, len(prices) + 1)) + home_extra),
             **{f"/kniha/{n}": (200, jsonld_page(f"Kniha {n}", str(p))) for n, p in enumerate(prices, start=1)}}
    if sitemap:
        pages["/sitemap.xml"] = (200, '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/'
                                      'sitemap/0.9">' + "".join(f"<url><loc>https://knihy.example.cz/kniha/{n}</loc>"
                                                                f"</url>" for n in sitemap) + "</urlset>")
    return FakeSite(pages)


def test_diagnosis_is_reused_and_renewed_after_thirty_days(db):
    c = competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    site = bookshop([100 + n for n in range(8)])
    run_all(db, site)
    diagnosis = settings_of(db, c).diagnosis
    assert diagnosis["chosen"] == ["crawl"] and diagnosis["stale"] is False and diagnosis["quality"]["found"] == 8

    site.requests.clear()
    request_again(db, c)
    run = run_all(db, site)
    assert "/sitemap.xml" not in site.requests and "detect" not in run.stats  # no new detection
    assert run.stats["plan"] == ["crawl"]

    site.requests.clear()
    request_again(db, c)
    run = run_all(db, site, now=datetime.now(UTC) + timedelta(days=31))
    assert "/sitemap.xml" in site.requests and run.stats["detect"]  # detected anew


def test_insufficient_sitemap_is_combined_with_the_crawler(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    # The sitemap lists five books, all with the same price: a wrong price on the website or a wrong parser.
    site = bookshop([99, 99, 99, 99, 99, 150, 160, 170], sitemap=[1, 2, 3, 4, 5])
    run = run_all(db, site)
    assert run.stats["plan"] == ["sitemap", "crawl"]
    assert run.stats["methods"]["sitemap"]["sufficient"] is False
    assert "vierohodné" in run.stats["methods"]["sitemap"]["reason"]
    assert run.stats["found"] == 8 and run.status == "succeeded"  # both methods together
    assert settings_of(db, db.scalar(select(Competitor))).diagnosis["chosen"] == ["sitemap", "crawl"]


def test_recipe_forced_crawler_patterns_and_shop_on_another_domain(db):
    c = competitor(db, "SPN", "https://spn.example.cz/", "spn.example.cz")
    shop_links = "".join(f'<a href="https://obchod.example.cz/titul/{n}">Titul {n}</a>' for n in range(1, 13))
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\n"),
        "/": (200, '<a href="/o-nas">O nás</a>' + shop_links),
        "/o-nas": (200, "<h1>O nás</h1>"),
        **{f"/titul/{n}": (200, jsonld_page(f"Titul {n}", str(80 + n))) for n in range(1, 13)},
        "/titul/1?tisk=1": (200, jsonld_page("Titul 1", "81")),
    })
    run = run_all(db, site)
    assert not any(r.startswith("/titul/") for r in site.requests)  # another domain: never without the recipe
    assert "obchod.example.cz" in run.stats["suggestions"][0]

    recipe = discovery.Recipe(strategy="crawl", entry_urls=["https://obchod.example.cz/"],
                              extra_domains=["obchod.example.cz"], exclude_patterns=["^/o-nas"],
                              product_pattern=r"^/titul/\d+$")
    settings = settings_of(db, c).model_copy(update={**recipe.model_dump(), "source": None, "diagnosis": {},
                                                     "run_requested": True})
    discovery.save(c.scrape_config, settings)
    db.commit()
    site.requests.clear()
    run = run_all(db, site)
    assert run.stats["plan"] == ["crawl"] and run.stats["found"] == 12 and run.status == "succeeded"
    assert "/o-nas" not in site.requests and "/sitemap.xml" not in site.requests


def test_recipe_api_validates_and_renews_the_diagnosis(client, db):
    from tests.conftest import login, make_user

    make_user("admin@example.com")
    login(client, "admin@example.com")
    c = competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    run_all(db, bookshop([100 + n for n in range(6)]))
    url = f"/api/competitors/{c.id}/discovery"
    assert client.get(url).json()["settings"]["diagnosis"]["chosen"] == ["crawl"]
    body = {"mode": "shadow", "terms_confirmed": True}
    assert client.put(url, json={**body, "recipe": {"include_patterns": ["(["]}}).status_code == 422
    assert client.put(url, json={**body, "recipe": {"extra_domains": ["nie je domena"]}}).status_code == 422
    state = client.put(url, json={**body, "recipe": {"strategy": "crawl", "extra_domains": ["https://www.Shop.cz/x"],
                                                      "drop_params": ["Sort"]}}).json()["settings"]
    assert (state["strategy"], state["extra_domains"], state["drop_params"]) == ("crawl", ["shop.cz"], ["sort"])
    assert state["diagnosis"] == {} and state["source"] is None  # found and judged anew


PRODOS_PAGE = (Path(__file__).parent.parent / "fixtures" / "html" / "prodos" / "cesky-slabikar-1.html").read_bytes()


PRODOS_TABS = ("obsah", "namety", "ukazky", "ke_stazeni", "rada", "reseni-uloh", "klicove-kompetence", "vysledky")


def prodos_site(products: int = 6, forbidden: list[str] | None = None, tabs: bool = False) -> FakeSite:
    """Like ucebnice.org: a sitemap starting with information pages (one of them, the forum, is closed: 403),
    product pages without structured data ("cena vč. DPH: 102.00 Kč")."""
    info = ["nakupovani", "obchodni-podminky", "reklamacni-rad", "forum", "odkazy", "uvod", "mapa-webu"]
    books = [f"cesky-jazyk-1st/slabikar-{n}" for n in range(1, products + 1)]
    tab_pages = [f"{b}/{t}" for b in books for t in PRODOS_TABS] if tabs else []  # each tab repeats the product
    locs = [f"https://ucebnice.example.cz/{p}" for p in info + books + tab_pages]
    sitemap = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
              "".join(f"<url><loc>{u}</loc></url>" for u in locs) + "</urlset>"
    pages = {"/robots.txt": (200, "User-agent: *\nSitemap: https://ucebnice.example.cz/sitemap.xml\n"),
             "/sitemap.xml": (200, sitemap), "/": (200, "<h1>Učebnice</h1>"),
             **{f"/{p}": (200, f"<h1>{p}</h1><p>Informace.</p>") for p in info},
             "/forum": (403, "<h1>403 Forbidden</h1>")}
    for n in range(1, products + 1):
        page = PRODOS_PAGE.replace("Český slabikář 1".encode(), f"Český slabikář {n}".encode()) \
                          .replace(b"102.00", f"{100 + n}.00".encode())
        if n > 1:  # one ISBN per title: only the first keeps the real one
            page = page.replace(b"978-80-7230-170-6", b"")
        pages[f"/cesky-jazyk-1st/slabikar-{n}"] = (200, page, "text/html; charset=UTF-8")
        for tab in PRODOS_TABS if tabs else ():
            pages[f"/cesky-jazyk-1st/slabikar-{n}/{tab}"] = (200, page, "text/html; charset=UTF-8")
    for path in forbidden or []:
        pages[path] = (403, "<h1>403 Forbidden</h1>")
    return FakeSite(pages)


def test_one_closed_page_does_not_stop_discovery_and_products_are_read_first(db):
    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    site = prodos_site()
    run = run_all(db, site)
    assert run.status in ("succeeded", "partial") and settings_of(db, c).paused_until is None
    reads = [r for r in site.requests if r not in ("/robots.txt", "/sitemap.xml", "/")]
    assert all(r.startswith("/cesky-jazyk-1st/") for r in reads[:6])  # product page type first, not sitemap order
    items = {i.key.rsplit("/", 1)[1]: i for i in db.scalars(select(CatalogItem))}
    assert (items["slabikar-1"].price, items["slabikar-1"].identifiers, items["slabikar-1"].title) == (
        Decimal("101.00"), {"isbn": "9788072301706"}, "Český slabikář 1")
    if "/forum" in site.requests:  # read at the end (if at all): recorded as a failure of that page
        assert items["forum"].fetch_status == "failed" and "403" in items["forum"].enrich_error


def test_tabs_of_product_pages_in_the_sitemap_are_not_read(db):
    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    tab = "https://ucebnice.example.cz/cesky-jazyk-1st/slabikar-1/obsah"
    queued = CatalogItem(competitor_id=c.id, key=tab, url=tab, first_seen_at=datetime.now(UTC),
                         match_status="new", status="active", identifiers={}, hints={}, attributes={}, candidates=[],
                         missing_runs=0, price_from_listing=False)  # queued by an earlier run, before tabs were known
    db.add(queued)
    db.commit()
    site = prodos_site(products=12, tabs=True)  # 12 products, 96 tabs
    run = run_all(db, site)
    assert run.status in ("succeeded", "partial"), run.error
    assert not [r for r in site.requests if r.rsplit("/", 1)[1] in PRODOS_TABS]  # not one tab read
    assert run.stats["tabs"] == sorted(PRODOS_TABS)
    db.refresh(queued)
    assert queued.fetch_status == "skipped" and "záložka" in queued.enrich_error
    keys = {i.key for i in db.scalars(select(CatalogItem).where(CatalogItem.price.is_not(None)))}
    assert len(keys) == 12


def test_forbidden_on_several_pages_in_a_row_means_blocking(db):
    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    every_page = [f"/cesky-jazyk-1st/slabikar-{n}" for n in range(1, 7)] + \
        ["/nakupovani", "/obchodni-podminky", "/reklamacni-rad", "/odkazy", "/uvod", "/mapa-webu"]
    site = prodos_site(forbidden=every_page)
    run = run_all(db, site)
    assert run.status == "stopped" and "obmedzil prístup" in run.error
    assert settings_of(db, c).paused_until is not None
    pages = [r for r in site.requests if r not in ("/robots.txt", "/sitemap.xml", "/")]
    assert len(pages) == 3  # three different pages closed in a row: the website blocks us


def test_queued_pages_of_a_type_without_products_are_skipped(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    # "How to order" pages carry a product parameter, so they look like products and are queued first.
    order_pages = "".join(f'<a href="/index.php?cast=1&id_katalog={n}">Jak objednat</a>' for n in range(1, 21))
    books = "".join(f'<a href="/index.php?zobraz=detail&id_katalog={n}">Kniha {n}</a>' for n in range(1, 6))
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\n"), "/": (200, order_pages + books),
        **{f"/index.php?cast=1&id_katalog={n}": (200, "<h1>Jak objednávat</h1>") for n in range(1, 21)},
        **{f"/index.php?id_katalog={n}&zobraz=detail": (200, jsonld_page(f"Kniha {n}", str(100 + n)))
           for n in range(1, 6)},
    })
    run = run_all(db, site)
    order_reads = [r for r in site.requests if r.startswith("/index.php?cast=1")]
    assert len(order_reads) == 5 and run.stats["found"] == 5  # after five such pages the rest is skipped


def test_ignored_domain_is_never_suggested_nor_read_and_does_not_restart_detection(client, db):
    from tests.conftest import login, make_user

    make_user("admin@example.com")
    login(client, "admin@example.com")
    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    interactive = "".join(f'<a href="https://flexi.example.cz/kniha/{n}">E-kniha {n}</a>' for n in range(1, 13))
    site = bookshop([100 + n for n in range(6)], home_extra=interactive)
    site.pages["/"] = (200, site.pages["/"][1])
    db.refresh(c.scrape_config)
    c.base_url = "https://knihy.example.cz/"
    c.domain = "knihy.example.cz"
    db.commit()
    run_all(db, site)
    url = f"/api/competitors/{c.id}/discovery"
    state = client.get(url).json()["settings"]
    assert "flexi.example.cz" in state["diagnosis"]["suggestions"][0]

    body = {"mode": "shadow", "terms_confirmed": True}
    assert client.put(url, json={**body, "recipe": {"extra_domains": ["flexi.example.cz"],
                                                     "ignored_domains": ["flexi.example.cz"]}}).status_code == 422
    after = client.put(url, json={**body, "recipe": {"ignored_domains": ["flexi.example.cz"]}}).json()["settings"]
    assert after["ignored_domains"] == ["flexi.example.cz"] and after["diagnosis"]["suggestions"] == []
    assert after["diagnosis"]["chosen"] == ["crawl"] and after["source"] is not None  # not detected anew

    db.expire_all()  # the API changed the settings in another session
    discovery.save(c.scrape_config, discovery.load(c.scrape_config).model_copy(update={"diagnosis": {}}))
    db.commit()
    request_again(db, c)
    run = run_all(db, site)
    assert not run.stats.get("suggestions")  # detected anew: the ignored domain is not suggested again


def test_the_same_page_under_many_addresses_is_not_a_product(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    # "How to order" pages: the same heading and a labelled amount on every one, linked from every product.
    order_links = "".join(f'<a href="/objednavka?id_katalog={n}">Jak objednat</a>' for n in range(1, 9))
    books = "".join(f'<a href="/kniha/{n}">Kniha {n}</a>' for n in range(1, 5))
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\n"), "/": (200, order_links + books),
        **{f"/objednavka?id_katalog={n}": (200, "<h1>JAK OBJEDNÁVAT</h1><p>Cena: 500 Kč</p>") for n in range(1, 9)},
        **{f"/kniha/{n}": (200, jsonld_page(f"Kniha {n}", str(100 + n))) for n in range(1, 5)},
    })
    run = run_all(db, site)
    items = list(db.scalars(select(CatalogItem)))
    assert {i.title for i in items if i.fetch_status != "not_product"} == {f"Kniha {n}" for n in range(1, 5)}
    assert len([r for r in site.requests if r.startswith("/objednavka")]) <= 5  # the page type is dropped early
    assert run.stats["boilerplate"] >= 3


def test_boilerplate_items_already_in_the_catalogue_are_cleaned_up(db):
    c = competitor(db, "Prometheus", "https://prometheus-nakl.cz/", "prometheus-nakl.cz")
    for n in range(1, 6):  # left by an earlier version: "JAK OBJEDNÁVAT" for 500 Kč as five products
        db.add(CatalogItem(competitor_id=c.id, key=f"https://prometheus-nakl.cz/index.php?cast=1&id_katalog={n}",
                           url=f"https://prometheus-nakl.cz/index.php?cast=1&id_katalog={n}", title="JAK OBJEDNÁVAT",
                           price=Decimal("500"), currency="CZK", match_status="new", status="active",
                           found_by="crawl", fetch_status="ok", enriched_at=datetime.now(UTC), identifiers={},
                           hints={}, attributes={}, candidates=[], missing_runs=0, price_from_listing=False))
    db.commit()
    run_all(db, prometheus_site(), limit=1000)
    left = db.scalars(select(CatalogItem).where(CatalogItem.title == "JAK OBJEDNÁVAT")).all()
    assert left and all(i.match_status == "unrelated" and i.fetch_status == "not_product" for i in left)


def test_page_type_dropped_earlier_is_not_followed_by_a_running_crawl(db):
    c = competitor(db, "Prometheus", "https://prometheus-nakl.cz/", "prometheus-nakl.cz")
    tpl = template("https://prometheus-nakl.cz/index.php?cast=1&id_katalog=1")
    for n in range(1, 4):  # dropped by an older version: marked in the catalogue, the page type not marked
        db.add(CatalogItem(competitor_id=c.id, key=f"https://prometheus-nakl.cz/index.php?cast=1&id_katalog={n}",
                           url=f"https://prometheus-nakl.cz/index.php?cast=1&id_katalog={n}", title="JAK OBJEDNÁVAT",
                           price=Decimal("500"), currency="CZK", match_status="unrelated", status="active",
                           found_by="crawl", fetch_status="not_product", enriched_at=datetime.now(UTC),
                           enrich_error=f"{discovery.BOILERPLATE_NOTE} 3 stránkach tohto typu (napr. návod).",
                           identifiers={}, hints={}, attributes={}, candidates=[], missing_runs=0,
                           price_from_listing=False))
    db.commit()
    assert discovery.clean_all(db) == {"duplicates": 0, "boilerplate": 0}
    db.expire_all()
    learned = settings_of(db, c).learned_templates
    assert learned[tpl]["not_products"] == 1
    running = {tpl: {"pages": 48, "products": 48, "listings": 0}}  # what the running crawl had counted
    stats = crawl.merged(learned, running)
    assert stats[tpl]["products"] == 0 and crawl.dead(tpl, stats)
    assert not crawl.product_like("https://prometheus-nakl.cz/index.php?cast=1&id_katalog=99", stats)
    assert crawl.merged(stats)[tpl]["not_products"] == 1  # kept when the run's statistics are learned


def test_tabs_of_one_product_are_one_product_and_unread_addresses_are_not_listed(client, db):
    from tests.conftest import login, make_user

    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz", budget_per_night=12)
    site = prodos_site(products=2)
    tabs = ("obsah", "namety", "ukazky")  # tabs of the product page: the same title, ISBN and price
    for tab in tabs:
        site.pages[f"/cesky-jazyk-1st/slabikar-1/{tab}"] = site.pages["/cesky-jazyk-1st/slabikar-1"]
    locs = "".join(f"<url><loc>https://ucebnice.example.cz/cesky-jazyk-1st/slabikar-1/{t}</loc></url>" for t in tabs)
    site.pages["/sitemap.xml"] = (200, site.pages["/sitemap.xml"][1].replace("</urlset>", locs + "</urlset>"))
    run_all(db, site)
    by_url = {i.url.removeprefix("https://ucebnice.example.cz"): i for i in db.scalars(select(CatalogItem))}
    assert by_url["/cesky-jazyk-1st/slabikar-1"].fetch_status == "ok"
    for tab in tabs:
        item = by_url[f"/cesky-jazyk-1st/slabikar-1/{tab}"]
        if item.fetch_status:  # read within the budget: a duplicate of the product page
            assert item.fetch_status == "not_product" and "Duplicitná stránka" in item.enrich_error

    make_user("admin@example.com")
    login(client, "admin@example.com")
    state = client.get(f"/api/competitors/{c.id}/discovery").json()
    new = client.get(f"/api/competitors/{c.id}/catalog", params={"match_status": "new"}).json()
    pending = client.get(f"/api/competitors/{c.id}/catalog", params={"pending": True}).json()
    assert all(i["fetch_status"] for i in new["items"])  # only pages actually read
    assert pending["total"] == state["counts"]["pending"] and all(i["title"] is None for i in pending["items"])


def test_worker_housekeeping_corrects_duplicates_also_of_a_paused_competitor(db):
    c = competitor(db, "Prodos", "https://ucebnice.org/", "ucebnice.org", mode="shadow")
    settings = discovery.load(c.scrape_config)
    discovery.save(c.scrape_config, settings.model_copy(update={"paused_until": datetime.now(UTC) + timedelta(days=7)}))
    for path in ("/prirodopis/9novy", "/prirodopis/9novy/obsah", "/prirodopis/9novy/namety",
                 "/prirodopis/9novy/ke_stazeni", "/prirodopis/9psnovy"):
        isbn = "9788072305353" if path.endswith("9psnovy") else "9788072305346"
        db.add(CatalogItem(competitor_id=c.id, key=f"https://ucebnice.org{path}", url=f"https://ucebnice.org{path}",
                           title="Přírodopis 9 – Geologie, Ekologie", price=Decimal("159"), currency="CZK",
                           identifiers={"isbn": isbn}, match_status="new", status="active", fetch_status="ok",
                           found_by="sitemap", enriched_at=datetime.now(UTC), hints={}, attributes={}, candidates=[],
                           missing_runs=0, price_from_listing=False))
    db.commit()
    assert discovery.clean_all(db) == {"duplicates": 3, "boilerplate": 0}
    products = db.scalars(select(CatalogItem.url).where(CatalogItem.fetch_status == "ok")).all()
    assert sorted(products) == ["https://ucebnice.org/prirodopis/9novy", "https://ucebnice.org/prirodopis/9psnovy"]
    assert discovery.clean_all(db) == {"duplicates": 0, "boilerplate": 0}  # nothing more to do


def test_a_temporarily_unavailable_website_is_paused_for_a_day_only(db):
    c = competitor(db, "Prodos", "https://ucebnice.example.cz/", "ucebnice.example.cz")
    site = prodos_site()
    for n in range(1, 7):
        site.pages[f"/cesky-jazyk-1st/slabikar-{n}"] = (503, "Service unavailable")
    for p in ("nakupovani", "obchodni-podminky", "reklamacni-rad", "odkazy", "uvod", "mapa-webu"):
        site.pages[f"/{p}"] = (503, "Service unavailable")
    run = run_all(db, site)
    assert run.status == "stopped" and "dočasne nedostupný" in run.error
    paused = settings_of(db, c).paused_until - datetime.now(UTC)
    assert timedelta(hours=23) < paused < timedelta(hours=25)


def test_a_website_drawn_by_javascript_is_reported_as_such(db):
    competitor(db, "Didaktis", "https://www.didaktis.example.cz/", "didaktis.example.cz")
    shell = (Path(__file__).parent.parent / "fixtures" / "html" / "didaktis_home.html").read_text(encoding="utf-8")
    site = FakeSite({"/robots.txt": (200, "User-agent: *\nDisallow: /Data/\n"), "/": (200, shell)})
    run = run_all(db, site)
    assert run.status == "failed" and "JavaScriptom" in run.error and run.stats["js_app"] is True
    assert any("JavaScript" in a["result"] for a in run.stats["attempts"])


class JsonSite(FakeSite):
    """A shop whose listing is a JSON interface read by POST (nns.cz); records method, path and body."""

    def __init__(self, pages: dict[str, tuple], listing):
        super().__init__(pages)
        self.listing = listing  # (offset, tags) -> rows
        self.posts: list[tuple[str, dict]] = []

    def fetcher(self) -> Fetcher:
        def handle(request: httpx.Request) -> httpx.Response:
            target = request.url.raw_path.decode()
            self.requests.append(target)
            if request.method == "POST":
                body = json.loads(request.content)
                self.posts.append((target, body))
                offset = int(target.rsplit("/", 1)[1])
                return httpx.Response(200, json=self.listing(offset, body["tags"]))
            status, body, *_ = self.pages.get(target, (404, "<h1>Nenalezeno</h1>"))
            return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})
        return Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"], transport=httpx.MockTransport(handle))


def test_nns_json_listing_per_grade_with_paging(db):
    nns = competitor(db, "NNS", "https://www.nns.cz/", "nns.cz")
    math = product(db, "Matematika 1 – 3. díl", "79", attributes={"grade": "1. ročník", "subject": "Matematika"})

    def listing(offset, tags):
        if tags == [5, 17]:
            return [{"type": "item", "id": 1649, "name": "Zápis je tu", "code": "P-50", "price": 99,
                     "status": "skladem", "tag_matches": 2}]
        if tags == [5, 7] and offset == 0:  # a full page of the grade: the next one is read too
            return [{"type": "item", "id": 2000 + i, "name": f"Matematika 1 – {i}. díl", "price": 75 + i,
                     "status": "skladem", "tag_matches": 2} for i in range(60)]
        if tags == [5, 7] and offset == 60:  # the grade ends here (items of one tag follow): no further page
            return [{"type": "item", "id": 3000, "name": "Písanka 1", "price": "39.0", "status": "prodej-ukoncen",
                     "tag_matches": 2},
                    {"type": "item", "id": 3001, "name": "Okénko do češtiny 1", "price": 149, "discount_price": 141.55,
                     "discount_str": "5%", "status": "skladem", "tag_matches": 2},
                    {"type": "item", "id": 3002, "name": "Pracovní listy pro 1. ročník", "price": 0,
                     "discount_price": 0, "status": "prodej-ukoncen", "tag_matches": 2}] + [
                {"type": "item", "id": 9000 + i, "name": "Jiný", "price": 10, "tag_matches": 1} for i in range(57)]
        assert offset == 0, (offset, tags)
        return []

    site = JsonSite({"/robots.txt": (200, "User-agent: *\nDisallow: /admin/\n")}, listing)
    run = run_all(db, site)
    assert run.status == "succeeded" and run.source_type == "adapter", run.stats
    assert site.requests[0] == "/robots.txt" and len(site.posts) == 11  # 10 grades + the 2nd page of grade 1
    assert site.posts[0] == ("/obchod/api/inventory/60/0", {"tags": [5, 17], "query": "", "packages": False,
                                                             "ids": []})
    assert ("/obchod/api/inventory/60/60", {"tags": [5, 7], "query": "", "packages": False, "ids": []}) in site.posts
    assert run.stats["found"] == 63  # 1 preschool + 61 of grade 1; items of one tag only are left out
    stored = settings_of(db, nns).source.urls  # the grades survive storing the source
    assert len(set(stored)) == 10 and "https://www.nns.cz/obchod/api/inventory/60/0?tags=5,17" in stored
    item = db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:1649"))
    assert (item.url, item.title, item.price, item.price_from_listing) == (
        "https://www.nns.cz/obchod/index.php?item=nab_det&sort_id=1649", "Zápis je tu", Decimal("99.00"), True)
    assert item.hints == {"grade": "předškolní vzdělávání", "availability": "in_stock", "code": "P-50"}
    third = db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:2003"))
    assert third.price == Decimal("78.00") and third.match_status in ("matched", "review")
    assert third.product_id == math.id or third.candidates[0]["product_id"] == math.id
    sold_out = db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:3000"))
    assert sold_out.hints["grade"] == "1. ročník" and sold_out.price == Decimal("39.00")
    assert sold_out.hints["availability"] == "out_of_stock"
    discounted = db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:3001"))
    assert discounted.price == Decimal("141.55")  # the price the shop shows (the regular one is struck through)
    assert db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:3002")) is None  # free / withdrawn
    assert db.scalar(select(CatalogItem).where(CatalogItem.key == "nns:9000")) is None
