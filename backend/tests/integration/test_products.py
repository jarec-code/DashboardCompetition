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


ETAKTIK_CSV = (
    "name;price;sku;currency\n"
    "Hravý početník 6 – 2. díl – revidované RVP ZV;62,0;;CZK\n"
    "Kadeřník & kadeřnice 2 – technologie a materiály;223,00;;CZK\n"
    "Bez ceny;;;CZK\n"
    "Neznámy trh;10;;USD\n"
    "Hravý početník 6 – 2. díl – revidované RVP ZV;62;;CZK\n"
    "Kniha v eurách;10;;EUR\n"
)


def upload(client, content: bytes, name="produkty.csv"):
    return client.post("/api/products/import", files={"file": (name, content, "text/csv")})


def test_csv_import_reports_rows(client):
    admin(client)
    r = upload(client, ETAKTIK_CSV.encode("cp1250"))  # Windows-1250, as exported by Excel
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"] == 3
    assert [d["row"] for d in result["duplicates"]] == [6]
    assert {e["row"]: e["reason"] for e in result["errors"]}.keys() == {4, 5}
    products = client.get("/api/products", params={"sort": "name"}).json()["items"]
    initial = [p for p in products if p["name"].startswith("Hravý")][0]
    assert initial["prices"] == [{"market": "CZ", "price": "62.00", "currency": "CZK", "vat_mode": "with_vat"}]
    assert any(p["name"] == "Kniha v eurách" and p["prices"][0]["market"] == "SK" for p in products)

    again = upload(client, ETAKTIK_CSV.encode("utf-8")).json()
    assert again["imported"] == 0 and len(again["duplicates"]) == 4


def test_csv_import_rejects_bad_files(client):
    admin(client)
    assert upload(client, b"nazov,cena_x\nA,1\n").status_code == 422
    assert upload(client, b"").status_code == 422
    assert upload(client, b"name,price\n" + b"x,1\n" * 6000).status_code == 422
    r = upload(client, "name,price,isbn\nKniha,10 €,123\n".encode())
    assert r.json()["errors"][0]["reason"].startswith("isbn")
