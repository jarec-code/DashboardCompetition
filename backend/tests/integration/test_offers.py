from datetime import UTC, datetime

from app.models import CompetitorProduct, CurrentPrice, PriceHistory
from app.services import matching
from tests.conftest import login, make_user


def setup(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    r = client.post("/api/competitors", json={"name": "Alter", "base_url": "https://www.alter.cz/", "country": "CZ"})
    return r.json()["id"]


def product(client, name, **extra):
    r = client.post("/api/products", json={"name": name, "prices": [{"market": "CZ", "price": "100"}], **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add(client, cid, urls, variant=""):
    r = client.post(f"/api/competitors/{cid}/offers", json={"urls": urls, "variant": variant})
    assert r.status_code == 201, r.text
    return r.json()


def test_bulk_add_validates_urls(client):
    cid = setup(client)
    result = add(client, cid, [
        "https://www.alter.cz/produkt/1111", " www.alter.cz/produkt/1112#popis ", "https://alter.cz/produkt/1113",
        "https://www.alter.cz/produkt/1111", "https://www.fraus.cz/produkt/1", "http://10.0.0.1/x", "",
    ])
    assert [o["product_url"] for o in result["created"]] == [
        "https://www.alter.cz/produkt/1111", "https://www.alter.cz/produkt/1112", "https://alter.cz/produkt/1113"]
    assert result["duplicates"] == ["https://www.alter.cz/produkt/1111"]
    assert [i["url"] for i in result["invalid"]] == ["https://www.fraus.cz/produkt/1", "http://10.0.0.1/x"]
    created = result["created"][0]
    assert created["match_status"] == "unmatched" and created["current"] is None and created["status"] == "active"

    again = add(client, cid, ["https://www.alter.cz/produkt/1111"])
    assert again["created"] == [] and again["duplicates"] == ["https://www.alter.cz/produkt/1111"]
    digital = add(client, cid, ["https://www.alter.cz/produkt/1111"], variant="elektronická")
    assert len(digital["created"]) == 1 and digital["created"][0]["variant"] == "elektronická"


def test_cannot_add_to_archived_competitor(client):
    cid = setup(client)
    client.patch(f"/api/competitors/{cid}", json={"status": "archived"})
    r = client.post(f"/api/competitors/{cid}/offers", json={"urls": ["https://www.alter.cz/produkt/1"]})
    assert r.status_code == 409


def test_list_and_filters(client):
    cid = setup(client)
    pid = product(client, "Naše vlast – pracovní listy")
    a, b = add(client, cid, ["https://www.alter.cz/produkt/1", "https://www.alter.cz/produkt/2"])["created"]
    client.post(f"/api/offers/{a['id']}/assignment", json={"action": "assign", "product_id": pid})

    listing = client.get(f"/api/competitors/{cid}/offers").json()
    assert listing["total"] == 2
    assert client.get(f"/api/competitors/{cid}/offers", params={"match_status": "matched"}).json()["total"] == 1
    assert client.get(f"/api/competitors/{cid}/offers", params={"q": "produkt/2"}).json()["items"][0]["id"] == b["id"]
    by_product = client.get("/api/offers", params={"product_id": pid}).json()["items"]
    assert [(o["id"], o["product"]["name"], o["competitor_name"]) for o in by_product] == [
        (a["id"], "Naše vlast – pracovní listy", "Alter")]
    assert client.get(f"/api/products/{pid}").json()["offer_count"] == 1


def test_manual_assignment_ignore_and_reset(client, db):
    cid = setup(client)
    pid = product(client, "Naše vlast – pracovní listy")
    offer = add(client, cid, ["https://www.alter.cz/produkt/1"])["created"][0]
    url = f"/api/offers/{offer['id']}/assignment"

    r = client.post(url, json={"action": "assign", "product_id": pid}).json()
    assert (r["product"]["id"], r["matching_method"], r["match_status"]) == (pid, "manual", "matched")
    assert client.post(url, json={"action": "assign", "product_id": 999}).status_code == 422

    # automatic matching never overrides a manual decision
    db_offer = db.get(CompetitorProduct, offer["id"])
    matching.rematch_open_offers(db)
    matching.auto_match(db, db_offer)
    assert db_offer.product_id == pid

    r = client.post(url, json={"action": "ignore"}).json()
    assert (r["product"], r["match_status"]) == (None, "ignored")
    r = client.post(url, json={"action": "auto"}).json()
    assert (r["matching_method"], r["match_status"]) == (None, "unmatched")


def _scraped(db, offer_id, title, identifiers=None):
    db.add(CurrentPrice(competitor_product_id=offer_id, title=title, found_identifiers=identifiers or {},
                        price=44, currency="CZK", last_success_at=datetime.now(UTC)))
    db.commit()


def test_auto_match_by_identifier_and_name(client, db):
    cid = setup(client)
    by_isbn = product(client, "Úplne iný názov", isbn="978-0-306-40615-7")
    by_name = product(client, "PRACOVNÍ LISTY k učebnici NAŠE VLAST (nové vydání)")
    product(client, "Matematika 3 pro SOŠ – pracovní sešit")
    offers = add(client, cid, [f"https://www.alter.cz/produkt/{i}" for i in (1, 2, 3, 4)])["created"]

    _scraped(db, offers[0]["id"], "Kniha", {"isbn": "0306406152"})                         # ISBN-10 on the page
    _scraped(db, offers[1]["id"], "Pracovní listy k učebnici Naše vlast – nové vydání")
    _scraped(db, offers[2]["id"], "Matematika 2 pro SOŠ – pracovní sešit")                 # other grade
    _scraped(db, offers[3]["id"], "Naše vlast – pracovní listy k učebnici")              # close, not certain
    for o in offers:
        matching.auto_match(db, db.get(CompetitorProduct, o["id"]))
    db.commit()

    got = {o["id"]: o for o in client.get(f"/api/competitors/{cid}/offers").json()["items"]}
    assert (got[offers[0]["id"]]["product"]["id"], got[offers[0]["id"]]["matching_method"]) == (by_isbn, "identifier")
    assert (got[offers[1]["id"]]["product"]["id"], got[offers[1]["id"]]["matching_method"]) == (by_name, "name")
    assert got[offers[2]["id"]]["match_status"] == "unmatched"
    assert got[offers[3]["id"]]["match_status"] == "pending_review" and got[offers[3]["id"]]["product"] is None

    candidates = client.get(f"/api/offers/{offers[3]['id']}/candidates").json()
    assert candidates[0]["product"]["id"] == by_name and candidates[0]["method"] == "name"
    searched = client.get(f"/api/offers/{offers[2]['id']}/candidates", params={"q": "Matematika 3 SOŠ"}).json()
    assert searched[0]["product"]["name"] == "Matematika 3 pro SOŠ – pracovní sešit"


def test_new_product_resolves_waiting_offer(client, db):
    cid = setup(client)
    offer = add(client, cid, ["https://www.alter.cz/produkt/1"])["created"][0]
    _scraped(db, offer["id"], "Kniha", {"ean": "8594165541796"})
    matching.auto_match(db, db.get(CompetitorProduct, offer["id"]))
    db.commit()
    assert client.get("/api/offers").json()["items"][0]["match_status"] == "unmatched"

    pid = product(client, "Naše vlast", ean="8594165541796")
    assert client.get("/api/offers").json()["items"][0]["product"]["id"] == pid


def test_update_and_remove(client, db):
    cid = setup(client)
    a, b = add(client, cid, ["https://www.alter.cz/produkt/1", "https://www.alter.cz/produkt/2"])["created"]
    r = client.patch(f"/api/offers/{a['id']}", json={"status": "paused", "custom_frequency_hours": 12})
    assert (r.json()["status"], r.json()["custom_frequency_hours"]) == ("paused", 12)
    add(client, cid, ["https://www.alter.cz/produkt/1"], variant="elektronická")
    assert client.patch(f"/api/offers/{a['id']}", json={"variant": "elektronická"}).status_code == 409

    db.add(PriceHistory(competitor_product_id=b["id"], price=44, currency="CZK", availability="in_stock",
                        vat_mode="unknown", observed_at=datetime.now(UTC), change_types=["initial"]))
    db.commit()
    assert client.delete(f"/api/offers/{a['id']}").json() == {"result": "deleted"}
    assert client.delete(f"/api/offers/{b['id']}").json() == {"result": "archived"}
    assert [o["id"] for o in client.get(f"/api/competitors/{cid}/offers").json()["items"]] == [
        o["id"] for o in client.get(f"/api/competitors/{cid}/offers").json()["items"] if o["id"] != b["id"]]
    assert client.get(f"/api/competitors/{cid}/offers", params={"status": "archived"}).json()["total"] == 1


def test_viewer_cannot_change_offers(client):
    cid = setup(client)
    offer = add(client, cid, ["https://www.alter.cz/produkt/1"])["created"][0]
    client.post("/api/auth/logout")
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get(f"/api/competitors/{cid}/offers").status_code == 200
    assert client.post(f"/api/competitors/{cid}/offers", json={"urls": ["https://www.alter.cz/x"]}).status_code == 403
    assert client.post(f"/api/offers/{offer['id']}/assignment", json={"action": "ignore"}).status_code == 403
    assert client.delete(f"/api/offers/{offer['id']}").status_code == 403
