from datetime import UTC, datetime, timedelta

from app.models import CompetitorProduct, CurrentPrice, ScrapeRun
from tests.conftest import login, make_user

ALTER = {"name": "Alter", "base_url": "https://www.alter.cz/", "country": "cz", "competitor_type": "direct"}


def admin_client(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    return client


def create(client, **overrides):
    r = client.post("/api/competitors", json={**ALTER, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_normalizes_and_sets_defaults(client):
    admin_client(client)
    c = create(client, base_url=" HTTPS://WWW.ALTER.CZ ")
    assert c["base_url"] == "https://www.alter.cz/"
    assert c["domain"] == "alter.cz"
    assert c["country"] == "CZ" and c["market"] == "CZ"
    assert c["status"] == "active"
    cfg = c["scrape_config"]
    assert (cfg["parser_type"], cfg["timeout_seconds"], cfg["max_concurrency"]) == ("auto", 20, 1)
    assert cfg["parser_version"] == 1
    assert client.get(f"/api/competitors/{c['id']}").json()["name"] == "Alter"


def test_invalid_input_rejected(client):
    admin_client(client)
    for payload in ({"base_url": "ftp://alter.cz"}, {"base_url": "http://192.168.1.1"}, {"country": "CZE"},
                    {"name": "   "}, {"default_frequency_hours": 0}, {"competitor_type": "friend"}):
        assert client.post("/api/competitors", json={**ALTER, **payload}).status_code == 422, payload


def test_duplicate_domain_needs_reason(client):
    admin_client(client)
    first = create(client)
    r = client.post("/api/competitors", json={**ALTER, "name": "Alter eshop", "base_url": "https://alter.cz/shop"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate_domain" and detail["existing"]["id"] == first["id"]

    second = create(client, name="Alter eshop", base_url="https://alter.cz/shop",
                    duplicate_domain_reason="Samostatný B2B obchod")
    assert second["duplicate_domain_reason"] == "Samostatný B2B obchod"


def test_update_and_duplicate_check_on_url_change(client):
    admin_client(client)
    alter = create(client)
    other = create(client, name="Fraus", base_url="https://www.fraus.cz/")
    alter_url, other_url = f"/api/competitors/{alter['id']}", f"/api/competitors/{other['id']}"
    r = client.patch(alter_url, json={"notes": "  Hlavný konkurent  ", "default_frequency_hours": 24})
    assert r.status_code == 200
    assert r.json()["notes"] == "Hlavný konkurent" and r.json()["default_frequency_hours"] == 24
    assert client.patch(other_url, json={"base_url": "https://alter.cz"}).status_code == 409
    assert client.patch(alter_url, json={"name": None}).status_code == 422
    assert client.patch(alter_url, json={"base_url": "https://www.alter.cz/shop"}).status_code == 200


def test_pause_and_archive_keep_offers_and_prices(client, db):
    admin_client(client)
    c = create(client)
    offer = CompetitorProduct(competitor_id=c["id"], product_url="https://www.alter.cz/produkt/1111")
    db.add(offer)
    db.flush()
    db.add(CurrentPrice(competitor_product_id=offer.id, price=44, currency="CZK",
                        last_success_at=datetime.now(UTC)))
    db.commit()

    for new_status in ("paused", "archived", "active"):
        assert client.patch(f"/api/competitors/{c['id']}", json={"status": new_status}).json()["status"] == new_status
    db.expire_all()
    assert db.get(CompetitorProduct, offer.id) is not None
    assert client.delete(f"/api/competitors/{c['id']}").status_code == 409


def test_delete_without_data(client):
    admin_client(client)
    c = create(client)
    assert client.delete(f"/api/competitors/{c['id']}").status_code == 204
    assert client.get(f"/api/competitors/{c['id']}").status_code == 404


def test_list_search_filter_sort_and_paginate(client, db):
    admin_client(client)
    alter = create(client)
    create(client, name="Fraus", base_url="https://www.fraus.cz/")
    create(client, name="Didaktis", base_url="https://www.didaktis.sk/", country="SK")
    archived = create(client, name="Starý obchod", base_url="https://stary.cz/")
    client.patch(f"/api/competitors/{archived['id']}", json={"status": "archived"})
    paused = create(client, name="Nová škola", base_url="https://www.nns.cz/")
    client.patch(f"/api/competitors/{paused['id']}", json={"status": "paused"})

    offer = CompetitorProduct(competitor_id=alter["id"], product_url="https://www.alter.cz/produkt/1")
    db.add(offer)
    db.flush()
    db.add(CurrentPrice(competitor_product_id=offer.id, price=10, currency="CZK",
                        last_success_at=datetime.now(UTC) - timedelta(hours=1)))
    db.add(ScrapeRun(competitor_id=alter["id"], status="partial", failure_count=2,
                     started_at=datetime.now(UTC)))
    db.commit()

    def names(**params):
        r = client.get("/api/competitors", params=params)
        assert r.status_code == 200, r.text
        return [i["name"] for i in r.json()["items"]]

    assert names() == ["Alter", "Didaktis", "Fraus", "Nová škola"]  # archived hidden by default
    assert names(status="archived") == ["Starý obchod"]
    assert len(names(status="all")) == 5
    assert names(status="paused") == ["Nová škola"]
    assert names(q="FRAUS") == ["Fraus"]
    assert names(q="didaktis.sk") == ["Didaktis"]
    assert names(q="100%_") == []  # LIKE wildcards are escaped
    assert names(country="sk") == ["Didaktis"]
    assert names(sort="-name") == ["Nová škola", "Fraus", "Didaktis", "Alter"]
    assert names(sort="-offers")[0] == "Alter"

    first = client.get("/api/competitors", params={"page_size": 2}).json()
    assert first["total"] == 4 and len(first["items"]) == 2
    assert names(page_size=2, page=2) == ["Fraus", "Nová škola"]

    item = client.get("/api/competitors", params={"q": "alter"}).json()["items"][0]
    assert item["offer_count"] == 1 and item["last_run_error_count"] == 2 and item["last_success_at"]
    assert client.get("/api/competitors/filters").json() == {"countries": ["CZ", "SK"]}
    assert client.get("/api/competitors", params={"sort": "password"}).status_code == 422


def test_scrape_config_validation_and_versioning(client):
    admin_client(client)
    c = create(client)
    url = f"/api/competitors/{c['id']}/scrape-config"
    css = {"parser_type": "css", "parser_config": {"price": ".vatPricePD", "name": "h1"}, "timeout_seconds": 15}
    r = client.put(url, json=css)
    assert r.status_code == 200, r.text
    assert r.json()["parser_version"] == 2 and r.json()["parser_config"] == {"price": ".vatPricePD", "name": "h1"}
    assert client.put(url, json=css).json()["parser_version"] == 2  # unchanged parser keeps its version

    bad = [
        {"parser_type": "css", "parser_config": {"name": "h1"}},                       # price missing
        {"parser_type": "css", "parser_config": {"price": "div[["}},                   # invalid selector
        {"parser_type": "css", "parser_config": {"price": ".p", "script": "rm -rf"}},  # unknown key
        {"parser_type": "auto", "parser_config": {"price": ".p"}},                     # auto takes no config
        {"parser_type": "python", "parser_config": {}},
        {"timeout_seconds": 0}, {"max_concurrency": 10}, {"max_retries": 9},
    ]
    for payload in bad:
        assert client.put(url, json=payload).status_code == 422, payload


def test_runs_listed_newest_first(client, db):
    admin_client(client)
    c = create(client)
    now = datetime.now(UTC)
    db.add_all([ScrapeRun(competitor_id=c["id"], status="succeeded", started_at=now - timedelta(days=1)),
                ScrapeRun(competitor_id=c["id"], status="failed", started_at=now, error_summary="HTTP 503")])
    db.commit()
    runs = client.get(f"/api/competitors/{c['id']}/runs").json()
    assert [r["status"] for r in runs] == ["failed", "succeeded"]


def test_viewer_can_read_but_not_change(client):
    admin_client(client)
    c = create(client)
    client.post("/api/auth/logout")
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/competitors").status_code == 200
    assert client.get(f"/api/competitors/{c['id']}").status_code == 200
    assert client.post("/api/competitors", json={**ALTER, "base_url": "https://x.cz"}).status_code == 403
    assert client.patch(f"/api/competitors/{c['id']}", json={"status": "paused"}).status_code == 403
    assert client.put(f"/api/competitors/{c['id']}/scrape-config", json={}).status_code == 403
    assert client.delete(f"/api/competitors/{c['id']}").status_code == 403


def test_requires_login(client):
    assert client.get("/api/competitors").status_code == 401
