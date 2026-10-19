from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import Settings
from app.models import (
    Alert,
    AlertRule,
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    Product,
    ProductPrice,
    ScrapeConfig,
)
from app.scraping.fetcher import Fetcher
from app.scraping.parsers.base import Extracted
from app.services import alerts, pricing
from app.worker import queue
from app.worker.worker import Worker
from tests.conftest import login, make_user

T0 = datetime.now(UTC) - timedelta(hours=3)


def setup(db, own_price="100", rules=(("price_drop", {"percent": 10}),)):
    product = Product(name="Hravá prvouka 1", prices=[ProductPrice(market="CZ", price=Decimal(own_price),
                                                                   currency="CZK", vat_mode="with_vat")])
    competitor = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz", country="CZ",
                            market="CZ", scrape_config=ScrapeConfig(parser_type="alter_cz", min_interval_seconds=1))
    db.add_all([product, competitor])
    db.flush()
    offer = CompetitorProduct(competitor_id=competitor.id, product_id=product.id, match_status="matched",
                              matching_method="manual", product_url="https://www.alter.cz/produkt/1111")
    db.add(offer)
    for rule_type, params in rules:
        db.add(AlertRule(rule_type=rule_type, params=params))
    db.commit()
    return product, competitor, offer


def observe(db, offer, price, availability="in_stock", at=None, vat="with_vat"):
    data = Extracted(price=Decimal(price), currency="CZK", availability=availability, vat_mode=vat)
    outcome = pricing.record_success(db, offer, data, at or datetime.now(UTC))
    db.flush()
    alerts.evaluate_offer(db, offer, alerts.Event(outcome.change_types, outcome.previous_price))
    db.commit()


def active(db, rule_type=None):
    query = select(Alert).where(Alert.status.in_(("open", "acknowledged")))
    if rule_type:
        query = query.where(Alert.alert_type == rule_type)
    db.expire_all()
    return list(db.scalars(query))


def test_price_drop_event_rule(db):
    _, _, offer = setup(db)
    observe(db, offer, "100", at=T0)
    assert active(db) == []                        # first observation is not a drop
    observe(db, offer, "95", at=T0 + timedelta(minutes=1))
    assert active(db) == []                        # 5 % is below the 10 % threshold
    observe(db, offer, "80", at=T0 + timedelta(minutes=2))
    [alert] = active(db)
    assert alert.message.startswith("Cena klesla o 15.8 % (95.00 CZK → 80.00 CZK)")
    observe(db, offer, "85", at=T0 + timedelta(minutes=3))
    assert len(active(db)) == 1                    # a price rise does not resolve an event alert
    observe(db, offer, "60", at=T0 + timedelta(minutes=4))
    [alert] = active(db)                           # the same alert is updated, not duplicated
    assert "85.00 CZK → 60.00 CZK" in alert.message


def test_cheaper_than_us_follows_our_price(client, db):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    product, _, offer = setup(db, rules=(("cheaper_than_us", {"percent": 0}),))
    observe(db, offer, "90")
    [alert] = active(db)
    assert "lacnejší o 10.0 %" in alert.message
    client.patch(f"/api/products/{product.id}", json={"prices": [{"market": "CZ", "price": "85"}]})
    assert active(db) == [] and db.get(Alert, alert.id).status == "resolved"


def test_cheaper_threshold_and_vat_mismatch(db):
    _, _, offer = setup(db, rules=(("cheaper_than_us", {"percent": 15}),))
    observe(db, offer, "90")
    assert active(db) == []                        # 10 % cheaper is below the 15 % threshold
    observe(db, offer, "50", vat="without_vat")
    assert active(db) == []                        # different VAT mode: not comparable, no alert


def test_unavailable_stale_and_failing_conditions(db):
    _, competitor, offer = setup(db, rules=(("unavailable", {}), ("stale", {}), ("scraper_failing", {"count": 2})))
    observe(db, offer, "100", availability="out_of_stock")
    assert [a.alert_type for a in active(db)] == ["unavailable"]
    observe(db, offer, "100", availability="in_stock")
    assert active(db) == []

    for _ in range(2):
        pricing.record_failure(db, offer, competitor, "HTTP 503", datetime.now(UTC))
        alerts.evaluate_offer(db, offer)
        db.commit()
    assert [a.alert_type for a in active(db)] == ["scraper_failing"]
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    current.is_stale = True
    alerts.evaluate_offer(db, offer)
    db.commit()
    assert sorted(a.alert_type for a in active(db)) == ["scraper_failing", "stale"]
    observe(db, offer, "100")
    assert active(db) == []                        # one success resolves both conditions


def test_scope_and_disabled_rules(client, db):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    _, competitor, offer = setup(db, rules=())
    other = Competitor(name="Fraus", base_url="https://www.fraus.cz/", domain="fraus.cz", scrape_config=ScrapeConfig())
    db.add(other)
    db.commit()
    db.add(AlertRule(rule_type="unavailable", params={}, competitor_id=other.id))
    db.commit()
    observe(db, offer, "100", availability="out_of_stock")
    assert active(db) == []                        # rule limited to another competitor

    r = client.post("/api/alert-rules", json={"rule_type": "unavailable", "competitor_id": competitor.id})
    assert r.status_code == 201 and r.json()["open_alerts"] == 1  # evaluated right away
    rule_id = r.json()["id"]
    r = client.put(f"/api/alert-rules/{rule_id}", json={"rule_type": "unavailable", "competitor_id": competitor.id,
                                                         "enabled": False})
    assert r.json()["enabled"] is False and active(db) == []


def test_archiving_offer_resolves_alerts(client, db):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    _, _, offer = setup(db, rules=(("unavailable", {}),))
    observe(db, offer, "100", availability="out_of_stock")
    assert len(active(db)) == 1
    client.patch(f"/api/offers/{offer.id}", json={"status": "archived"})
    assert active(db) == []


def test_alert_api_states_and_permissions(client, db):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    _, _, offer = setup(db, rules=(("unavailable", {}),))
    observe(db, offer, "100", availability="out_of_stock")
    listing = client.get("/api/alerts").json()
    assert listing["total"] == 1 and client.get("/api/alerts/count").json() == {"open": 1}
    item = listing["items"][0]
    assert (item["competitor_name"], item["product_name"], item["alert_type"]) == ("Alter", "Hravá prvouka 1",
                                                                                  "unavailable")
    aid = item["id"]
    assert client.post(f"/api/alerts/{aid}/acknowledge").json()["status"] == "acknowledged"
    assert client.get("/api/alerts/count").json() == {"open": 0}
    assert client.post(f"/api/alerts/{aid}/acknowledge").status_code == 409
    assert client.post(f"/api/alerts/{aid}/reopen").json()["status"] == "open"
    assert client.post(f"/api/alerts/{aid}/resolve").json()["status"] == "resolved"
    assert client.get("/api/alerts").json()["total"] == 0
    assert client.get("/api/alerts", params={"status": "resolved"}).json()["total"] == 1

    for bad in ({"rule_type": "price_drop", "params": {"percent": 0}}, {"rule_type": "ai_magic"},
                {"rule_type": "scraper_failing", "params": {"count": "many"}},
                {"rule_type": "stale", "competitor_id": 999}):
        assert client.post("/api/alert-rules", json=bad).status_code == 422, bad

    client.post("/api/auth/logout")
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/alerts", params={"status": "resolved"}).status_code == 200
    assert client.get("/api/alert-rules").status_code == 200
    assert client.post(f"/api/alerts/{aid}/reopen").status_code == 403
    assert client.post("/api/alert-rules", json={"rule_type": "stale"}).status_code == 403


def test_worker_raises_price_drop_alert(db, monkeypatch):
    monkeypatch.setattr("app.worker.processor.time.sleep", lambda s: None)
    page = (Path(__file__).parent.parent / "fixtures" / "html" / "alter_cz" / "produkt_1111.html").read_text("utf-8")
    pages = {"https://www.alter.cz/produkt/1111": page}

    def handler(request):
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body, headers={"content-type": "text/html"}) if body else httpx.Response(404)
    worker = Worker(Settings(scheduler_in_worker=False),
                    fetcher=Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"],
                                    transport=httpx.MockTransport(handler)))
    worker._last_alerts = float("inf")  # no periodic sweep in this test
    _, competitor, offer = setup(db)
    for price in ("44,00", "39,00"):
        pages["https://www.alter.cz/produkt/1111"] = page.replace('<div class="vatPricePD">44,00',
                                                                 f'<div class="vatPricePD">{price}', 1)
        queue.enqueue(db, competitor.id, "manual", offer_id=offer.id)
        db.commit()
        while worker.run_once():
            pass
    [alert] = active(db)
    assert alert.alert_type == "price_drop" and "44.00 CZK → 39.00 CZK" in alert.message


def test_many_alerts_on_one_connection(db):
    """Regression: from the 5th execution psycopg uses a prepared statement; the ON CONFLICT clause
    must still match the partial unique index (it failed with a bound-parameter predicate)."""
    _, competitor, _ = setup(db, rules=(("cheaper_than_us", {"percent": 0}),))
    product = db.scalar(select(Product))
    for i in range(12):
        offer = CompetitorProduct(competitor_id=competitor.id, product_id=product.id, match_status="matched",
                                  matching_method="manual", product_url=f"https://www.alter.cz/produkt/{i + 2}")
        db.add(offer)
        db.flush()
        db.add(CurrentPrice(competitor_product_id=offer.id, price=Decimal("50"), currency="CZK", vat_mode="with_vat",
                            availability="in_stock", last_success_at=datetime.now(UTC)))
    db.commit()
    for _ in range(2):  # twice: the second run updates instead of duplicating
        alerts.evaluate_all(db)
        db.commit()
    assert len(active(db, "cheaper_than_us")) == 12
