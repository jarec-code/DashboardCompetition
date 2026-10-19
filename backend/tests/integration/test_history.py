from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Competitor, CompetitorProduct, PriceHistory, Product, ProductPrice, ScrapeConfig
from tests.conftest import login, make_user

NOW = datetime.now(UTC)


def history(db, offer, *entries):
    for days_ago, price, currency, types in entries:
        db.add(PriceHistory(competitor_product_id=offer.id, price=Decimal(price), currency=currency,
                            availability="in_stock", vat_mode="with_vat", observed_at=NOW - timedelta(days=days_ago),
                            change_types=types))


def seed(db):
    product = Product(name="Hravá prvouka 1", prices=[ProductPrice(market="CZ", price=Decimal("189"), currency="CZK"),
                                                      ProductPrice(market="SK", price=Decimal("7.90"), currency="EUR")])
    db.add(product)
    comps = {}
    for name, market in (("Alter", "CZ"), ("Fraus", "CZ"), ("Didaktis", "SK"), ("Euro", "CZ")):
        c = Competitor(name=name, base_url=f"https://www.{name.lower()}.cz/", domain=f"{name.lower()}.cz",
                       country=market, market=market, scrape_config=ScrapeConfig())
        db.add(c)
        db.flush()
        comps[name] = c
    offers = {}
    for name, c in comps.items():
        o = CompetitorProduct(competitor_id=c.id, product_id=product.id, product_url=f"https://www.{c.domain}/p",
                              match_status="matched", matching_method="manual")
        db.add(o)
        db.flush()
        offers[name] = o
    history(db, offers["Alter"], (200, "70", "CZK", ["initial"]), (20, "65", "CZK", ["price_down"]),
            (5, "69", "CZK", ["price_up"]))
    history(db, offers["Fraus"], (10, "180", "CZK", ["initial"]))
    history(db, offers["Didaktis"], (3, "6.50", "EUR", ["initial"]))
    history(db, offers["Euro"], (3, "3.00", "EUR", ["initial"]))   # CZ market but EUR prices
    db.commit()
    return product, comps, offers


def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def test_chart_data(client, db):
    admin(client)
    product, comps, _ = seed(db)
    data = client.get("/api/history/chart", params={"product_id": product.id, "market": "CZ"}).json()
    assert (data["market"], data["markets"], data["axis_currency"], data["own_price"]) == ("CZ", ["CZ", "SK"],
                                                                                            "CZK", "189.00")
    series = {s["competitor_name"]: s for s in data["series"]}
    assert set(series) == {"Alter", "Fraus", "Euro"}
    alter = series["Alter"]["points"]
    # value from before the 90-day window carried to its start, two changes, extended to "now"
    assert [(p["price"], p["synthetic"]) for p in alter] == [("70.00", True), ("65.00", False), ("69.00", False),
                                                              ("69.00", True)]
    assert series["Euro"]["plottable"] is False and series["Alter"]["plottable"] is True

    filtered = client.get("/api/history/chart", params={"product_id": product.id, "market": "CZ",
                                                        "competitor_ids": str(comps["Fraus"].id)}).json()
    assert [s["competitor_name"] for s in filtered["series"]] == ["Fraus"]
    assert filtered["series"][0]["slot"] == series["Fraus"]["slot"]  # color follows the competitor

    sk = client.get("/api/history/chart", params={"product_id": product.id, "market": "sk",
                                                  "from": (NOW - timedelta(days=400)).isoformat()}).json()
    assert [s["competitor_name"] for s in sk["series"]] == ["Didaktis"] and sk["own_price"] == "7.90"

    assert client.get("/api/history/chart", params={"product_id": 999}).status_code == 404
    bad = {"product_id": product.id, "from": NOW.isoformat(), "to": (NOW - timedelta(days=1)).isoformat()}
    assert client.get("/api/history/chart", params=bad).status_code == 422


def test_changes_list(client, db):
    admin(client)
    product, comps, offers = seed(db)
    rows = client.get("/api/history/changes").json()
    assert rows["total"] == 6
    alter = client.get("/api/history/changes", params={"competitor_id": comps["Alter"].id}).json()["items"]
    assert [(r["price"], r["previous_price"], r["change_types"]) for r in alter] == [
        ("69.00", "65.00", ["price_up"]), ("65.00", "70.00", ["price_down"]), ("70.00", None, ["initial"])]
    recent = client.get("/api/history/changes", params={"from": (NOW - timedelta(days=7)).isoformat()}).json()
    assert recent["total"] == 3
    drops = client.get("/api/history/changes", params={"change_type": "price_down"}).json()["items"]
    assert [r["competitor_name"] for r in drops] == ["Alter"] and drops[0]["product_name"] == "Hravá prvouka 1"
    assert client.get("/api/history/changes", params={"product_id": product.id, "page_size": 2}).json()["total"] == 6
