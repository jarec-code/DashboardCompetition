from app.models import Competitor, CompetitorProduct, ScrapeConfig
from tests.conftest import login, make_user

PRODUCT = {
    "name": "Hravá matematika 2 – pracovní učebnice – 1. díl",
    "isbn": "978-0-306-40615-7",
    "priority": "strategic",
    "attributes": {"subject": "matematika", "grade": "2", "type": "pracovná učebnica"},
    "prices": [{"market": "cz", "price": "114"}, {"market": "SK", "price": "4.90"}],
}


def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def create(client, **overrides):
    r = client.post("/api/products", json={**PRODUCT, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_with_market_prices_and_defaults(client):
    admin(client)
    p = create(client)
    assert p["isbn"] == "9780306406157"
    assert p["attributes"] == {"subject": "matematika", "grade": "2", "type": "pracovná učebnica"}
    assert [(x["market"], x["price"], x["currency"], x["vat_mode"]) for x in p["prices"]] == [
        ("CZ", "114.00", "CZK", "with_vat"), ("SK", "4.90", "EUR", "with_vat")]
    assert client.get(f"/api/products/{p['id']}").json()["offer_count"] == 0


def test_validation(client):
    admin(client)
    for payload in ({"isbn": "978-0-306-40615-8"}, {"ean": "123"}, {"name": " "},
                    {"prices": [{"market": "CZ", "price": "-1"}]},
                    {"prices": [{"market": "CZ", "price": "1"}, {"market": "cz", "price": "2"}]},
                    {"prices": [{"market": "XX", "price": "1"}]},          # unknown market needs a currency
                    {"attributes": {"color": "red"}}, {"priority": "top"}):
        assert client.post("/api/products", json={**PRODUCT, **payload}).status_code == 422, payload
    ok = client.post("/api/products", json={**PRODUCT, "prices": [{"market": "XX", "price": "1", "currency": "usd"}]})
    assert ok.status_code == 201 and ok.json()["prices"][0]["currency"] == "USD"


def test_identifiers_are_unique(client):
    admin(client)
    first = create(client, sku="T-100")
    r = client.post("/api/products", json={**PRODUCT, "name": "Iný", "isbn": "0-306-40615-2"})
    assert r.status_code == 409 and "ISBN" in r.json()["detail"]
    same_sku = {**PRODUCT, "name": "Iný", "isbn": None, "sku": "t-100"}
    assert client.post("/api/products", json=same_sku).status_code == 409
    other = create(client, name="Iný", isbn=None, sku="T-200")
    assert client.patch(f"/api/products/{other['id']}", json={"isbn": PRODUCT["isbn"]}).status_code == 409
    assert client.patch(f"/api/products/{first['id']}", json={"isbn": PRODUCT["isbn"]}).status_code == 200


def test_update_replaces_prices_and_keeps_others(client):
    admin(client)
    p = create(client)
    r = client.patch(f"/api/products/{p['id']}", json={"prices": [{"market": "CZ", "price": "119"}]})
    assert [(x["market"], x["price"]) for x in r.json()["prices"]] == [("CZ", "119.00")]
    r = client.patch(f"/api/products/{p['id']}", json={"category": "Matematika"})
    assert r.json()["category"] == "Matematika" and len(r.json()["prices"]) == 1
    assert client.patch(f"/api/products/{p['id']}", json={"name": None}).status_code == 422


def test_search_filter_sort(client):
    admin(client)
    create(client)
    create(client, name="Hravý sloh 6", isbn=None, sku="HS-6", priority="regular",
           prices=[{"market": "CZ", "price": "119"}])
    create(client, name="Detektiv Písmenko 5", isbn=None, priority="stable", prices=[{"market": "SK", "price": "6"}])

    def names(**params):
        return [p["name"] for p in client.get("/api/products", params=params).json()["items"]]

    assert names(q="0-306-40615-2") == [PRODUCT["name"]]       # ISBN in any format
    assert names(q="sloh") == ["Hravý sloh 6"]
    assert names(q="hs-6") == ["Hravý sloh 6"]
    assert names(market="sk") == ["Detektiv Písmenko 5", PRODUCT["name"]]
    assert names(priority="stable") == ["Detektiv Písmenko 5"]
    assert names(sort="-name")[0] == "Hravý sloh 6"
    assert client.get("/api/products", params={"page_size": 2}).json()["total"] == 3


def test_delete_returns_offers_to_matching(client, db):
    admin(client)
    p = create(client)
    competitor = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz",
                            scrape_config=ScrapeConfig())
    db.add(competitor)
    db.flush()
    offer = CompetitorProduct(competitor_id=competitor.id, product_url="https://www.alter.cz/produkt/1",
                              product_id=p["id"], matching_method="manual", match_status="matched")
    db.add(offer)
    db.commit()
    assert client.get(f"/api/products/{p['id']}").json()["offer_count"] == 1
    assert client.delete(f"/api/products/{p['id']}").status_code == 204
    db.expire_all()
    offer = db.get(CompetitorProduct, offer.id)
    assert offer.product_id is None and offer.match_status == "unmatched"


def test_viewer_read_only(client):
    admin(client)
    p = create(client)
    client.post("/api/auth/logout")
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/products").status_code == 200
    assert client.post("/api/products", json=PRODUCT).status_code == 403
    assert client.patch(f"/api/products/{p['id']}", json={"name": "x"}).status_code == 403
    assert client.delete(f"/api/products/{p['id']}").status_code == 403


def test_eshop_url_availability_and_source_fields(client):
    admin(client)
    p = create(client, url="https://WWW.etaktik.cz/hrava-matematika-2/#detail", availability="in_stock")
    assert (p["url"], p["availability"], p["source_kind"], p["source_synced_at"]) == (
        "https://www.etaktik.cz/hrava-matematika-2/", "in_stock", None, None)  # entered by hand
    r = client.post("/api/products", json={**PRODUCT, "name": "Iný", "isbn": None,
                                           "url": "https://www.etaktik.cz/hrava-matematika-2/"})
    assert r.status_code == 409 and "URL" in r.json()["detail"]
    assert client.post("/api/products", json={**PRODUCT, "url": "ftp://x.cz/a"}).status_code == 422
    assert client.post("/api/products", json={**PRODUCT, "availability": "maybe"}).status_code == 422


def test_bundle_flag_and_filter(client):
    admin(client)
    single = create(client)
    bundle = create(client, name="Balíček pro 1. ročník", isbn=None, is_bundle=True)
    assert (single["is_bundle"], bundle["is_bundle"]) == (False, True)  # undecided counts as a single product
    names = lambda **f: [p["name"] for p in client.get("/api/products", params=f).json()["items"]]  # noqa: E731
    assert names() == ["Balíček pro 1. ročník", PRODUCT["name"]]
    assert names(bundles="exclude") == [PRODUCT["name"]]
    assert names(bundles="only") == ["Balíček pro 1. ročník"]
    assert client.get("/api/products", params={"bundles": "maybe"}).status_code == 422
    assert client.patch(f"/api/products/{bundle['id']}", json={"is_bundle": False}).json()["is_bundle"] is False
