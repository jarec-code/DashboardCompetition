from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import (
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    PriceHistory,
    Product,
    ProductPrice,
    ScrapeConfig,
    ScrapeJob,
)
from tests.conftest import login, make_user

NOW = datetime.now(UTC)


def competitor(db, name, market, status="active"):
    c = Competitor(name=name, base_url=f"https://www.{name.lower()}.cz/", domain=f"{name.lower()}.cz", country=market,
                   market=market, status=status, scrape_config=ScrapeConfig())
    db.add(c)
    db.flush()
    return c


def offer(db, comp, product, path, price=None, currency="CZK", vat="with_vat", stale=False, status="active",
          availability="in_stock", checked=NOW):
    o = CompetitorProduct(competitor_id=comp.id, product_url=f"https://www.{comp.domain}/{path}",
                          product_id=product.id if product else None, status=status,
                          match_status="matched" if product else "unmatched", matching_method="manual")
    db.add(o)
    db.flush()
    if price is not None or stale:
        db.add(CurrentPrice(competitor_product_id=o.id, price=Decimal(price) if price else None, currency=currency,
                            vat_mode=vat, availability=availability, is_stale=stale,
                            last_success_at=checked if price else None, last_checked_at=checked))
    db.flush()
    return o


def seed(db):
    math = Product(name="Hravá matematika 2", priority="strategic",
                   prices=[ProductPrice(market="CZ", price=Decimal("114"), currency="CZK"),
                           ProductPrice(market="SK", price=Decimal("4.90"), currency="EUR")])
    guide = Product(name="Hravá prvouka 1 – metodická příručka",
                    prices=[ProductPrice(market="CZ", price=Decimal("189"), currency="CZK")])
    sk_only = Product(name="Písanka 1", prices=[ProductPrice(market="SK", price=Decimal("2.50"), currency="EUR")])
    db.add_all([math, guide, sk_only])
    db.flush()
    alter, fraus = competitor(db, "Alter", "CZ"), competitor(db, "Fraus", "CZ")
    didaktis = competitor(db, "Didaktis", "SK")
    old = competitor(db, "Stary", "CZ", status="archived")
    offer(db, alter, math, "m2", "99.00")                          # cheaper than us: -15, -13.2 %
    offer(db, fraus, math, "m2", "129.00", vat="unknown")          # dearer, VAT not verified
    offer(db, didaktis, math, "m2", "5.20", currency="EUR")       # SK market: +0.30, +6.1 %
    offer(db, alter, guide, "g1", "65.00", vat="without_vat")     # known different VAT: not compared
    offer(db, fraus, guide, "g1", "7.00", currency="EUR")         # different currency: not compared
    offer(db, alter, sk_only, "p1", "30.00")                      # no own CZ price
    offer(db, fraus, sk_only, "p1", stale=True)                   # never succeeded: no price
    offer(db, alter, guide, "archived", "1.00", status="archived")
    offer(db, old, guide, "x", "1.00")                            # archived competitor
    offer(db, alter, None, "unassigned", "1.00")
    db.commit()
    return math, guide, sk_only


def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def test_comparison_rows_and_rules(client, db):
    admin(client)
    seed(db)
    data = client.get("/api/comparison").json()
    rows = {(r["product_name"], r["market"]): r for r in data["items"]}
    assert set(rows) == {("Hravá matematika 2", "CZ"), ("Hravá matematika 2", "SK"),
                         ("Hravá prvouka 1 – metodická příručka", "CZ"), ("Písanka 1", "CZ")}

    math_cz = rows[("Hravá matematika 2", "CZ")]
    cells = {c["competitor_name"]: c for c in math_cz["offers"]}
    assert (cells["Alter"]["diff_abs"], cells["Alter"]["diff_pct"]) == ("-15.00", "-13.2")
    assert (cells["Fraus"]["diff_abs"], cells["Fraus"]["diff_pct"], cells["Fraus"]["vat_unverified"]) == (
        "15.00", "13.2", True)
    assert (math_cz["min_price"], math_cz["max_price"], math_cz["cheaper_count"], math_cz["position"]) == (
        "99.00", "129.00", 1, "not_cheapest")
    assert math_cz["max_undercut_pct"] == "-13.2"

    math_sk = rows[("Hravá matematika 2", "SK")]
    assert (math_sk["own_price"], math_sk["offers"][0]["diff_pct"], math_sk["position"]) == ("4.90", "6.1", "cheapest")

    guide = {c["competitor_name"]: c for c in rows[("Hravá prvouka 1 – metodická příručka", "CZ")]["offers"]}
    assert (guide["Alter"]["comparable"], guide["Alter"]["reason"]) == (False, "vat_mismatch")
    assert (guide["Fraus"]["comparable"], guide["Fraus"]["reason"]) == (False, "currency_mismatch")
    assert rows[("Hravá prvouka 1 – metodická příručka", "CZ")]["position"] == "unknown"
    assert rows[("Hravá prvouka 1 – metodická příručka", "CZ")]["min_price"] is None  # nothing comparable

    pisanka = {c["competitor_name"]: c for c in rows[("Písanka 1", "CZ")]["offers"]}
    assert pisanka["Alter"]["reason"] == "no_own_price"
    assert (pisanka["Fraus"]["reason"], pisanka["Fraus"]["price"], pisanka["Fraus"]["is_stale"]) == (
        "no_price", None, True)

    assert data["markets"] == ["CZ", "SK"]
    assert [c["name"] for c in data["competitors"]] == ["Alter", "Didaktis", "Fraus"]  # archived one not listed
    assert data["summary"] == {"cheapest": 1, "not_cheapest": 1, "unknown": 2}


def test_comparison_filters_and_sort(client, db):
    admin(client)
    seed(db)

    def names(**params):
        return [(r["product_name"], r["market"]) for r in client.get("/api/comparison", params=params).json()["items"]]

    assert names(market="sk") == [("Hravá matematika 2", "SK")]
    assert names(position="not_cheapest") == [("Hravá matematika 2", "CZ")]
    assert names(q="písanka") == [("Písanka 1", "CZ")]
    didaktis = [c for c in client.get("/api/comparison").json()["competitors"] if c["name"] == "Didaktis"][0]
    assert names(competitor_id=didaktis["id"]) == [("Hravá matematika 2", "SK")]
    assert names(sort="undercut")[0] == ("Hravá matematika 2", "CZ")
    page = client.get("/api/comparison", params={"page_size": 2, "page": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2


def test_comparison_csv_export(client, db):
    admin(client)
    seed(db)
    r = client.get("/api/comparison/export.csv", params={"market": "CZ"})
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("Produkt;Trh;Vlastná cena")
    assert any(line.startswith("Hravá matematika 2;CZ;114.00;CZK;Alter;99.00;CZK;-15.00;-13.2;") for line in lines)
    assert any("iný režim DPH" in line for line in lines) and any("DPH neoverené" in line for line in lines)
    assert not any(";SK;" in line for line in lines[1:])


def test_dashboard(client, db):
    admin(client)
    math, guide, _ = seed(db)
    alter_offer = db.query(CompetitorProduct).filter_by(product_url="https://www.alter.cz/m2").one()
    fraus_offer = db.query(CompetitorProduct).filter_by(product_url="https://www.fraus.cz/m2").one()
    # Alter dropped an hour ago, Fraus three days ago; both were first seen a week ago.
    for o, prices, changed in ((alter_offer, ("120", "99"), timedelta(hours=1)),
                               (fraus_offer, ("130", "129"), timedelta(days=3))):
        db.add(PriceHistory(competitor_product_id=o.id, price=Decimal(prices[0]), currency="CZK",
                            availability="in_stock", vat_mode="with_vat", observed_at=NOW - timedelta(days=6),
                            change_types=["initial"]))
        db.add(PriceHistory(competitor_product_id=o.id, price=Decimal(prices[1]), currency="CZK",
                            availability="in_stock", vat_mode="with_vat", observed_at=NOW - changed,
                            change_types=["price_down"]))
    db.add(ScrapeJob(competitor_id=alter_offer.competitor_id, competitor_product_id=alter_offer.id, kind="scheduled",
                     priority=100, status="succeeded", attempt_count=1, finished_at=NOW - timedelta(hours=1)))
    db.commit()

    d = client.get("/api/dashboard").json()
    assert (d["competitors_total"], d["competitors_active"]) == (3, 3)
    assert d["offers_tracked"] == 8          # active offers of active competitors (incl. unassigned)
    assert d["checks_24h"] == 1 and d["failed_checks_24h"] == 0
    assert d["missing_or_stale"] == 1        # the offer that never got a price
    assert (d["price_changes_24h"], d["price_changes_7d"]) == (1, 2)
    assert (d["cheaper_competitor_rows"], d["comparable_rows"]) == (1, 2)
    drops = [(x["competitor_name"], x["change_pct"]) for x in d["biggest_drops"]]
    assert drops == [("Alter", "-17.5"), ("Fraus", "-0.8")]
    assert d["biggest_drops"][0]["previous_price"] == "120.00" and d["biggest_drops"][0]["product_name"] == math.name
    assert [x["change_types"] for x in d["recent_changes"]] == [["price_down"], ["price_down"]]


def test_viewer_can_read(client, db):
    seed(db)
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/comparison").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/comparison/export.csv").status_code == 200
