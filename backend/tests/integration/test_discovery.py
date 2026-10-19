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
    PriceHistory,
    Product,
    ProductPrice,
    ScrapeConfig,
    ScrapeRun,
)
from app.scraping.fetcher import Fetcher
from app.services import discovery, scheduling
from app.worker.scheduler import due_offers_query

ALTER = Path(__file__).parent.parent / "fixtures" / "html" / "alter_cz"
PAGE_0 = (ALTER / "shop_page_0.html").read_text(encoding="utf-8")
PAGE_LAST = (ALTER / "shop_page_11.html").read_text(encoding="utf-8")


class FakeSite:
    """A website: path (with query) -> (status, body). Records every request."""

    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages = pages
        self.requests: list[str] = []

    def fetcher(self) -> Fetcher:
        def handle(request: httpx.Request) -> httpx.Response:
            target = request.url.raw_path.decode()
            self.requests.append(target)
            status, body = self.pages.get(target, (404, "<h1>Nenalezeno</h1>"))
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


def test_generic_site_listing_found_from_home_page(db):
    competitor(db, "Nakladatel", "https://knihy.example.cz/", "knihy.example.cz")
    cards = "".join(f'<div class="card"><a href="/produkt/{n}">Kniha {n}</a></div>' for n in range(1, 15))
    site = FakeSite({
        "/robots.txt": (200, "User-agent: *\nDisallow: /kosik/\n"),
        "/": (200, '<a href="/kontakt">Kontakt</a> <a href="/katalog">Katalog učebnic</a>'),
        "/katalog": (200, cards + '<a rel="next" href="/katalog?strana=2">další</a>'),
        "/katalog?strana=2": (200, '<div class="card"><a href="/produkt/15">Kniha 15</a></div>'),
        **{f"/produkt/{n}": (200, jsonld_page(f"Kniha {n}", "100")) for n in range(1, 16)},
    })
    run = run_all(db, site)
    assert run.status == "succeeded" and run.stats["detected"] == "listing"
    assert run.stats["pages"] == 2 and run.stats["found"] == 15  # pagination followed
    assert "/kontakt" not in site.requests


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
