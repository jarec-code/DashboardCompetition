import io
from decimal import Decimal
from pathlib import Path

import httpx
from openpyxl import Workbook
from sqlalchemy import delete, select

from app.config import Settings
from app.models import Competitor, CompetitorProduct, Product, ProductPrice, ScrapeConfig
from app.scraping.discover import discover_links
from app.scraping.fetcher import Fetcher
from app.worker import queue
from app.worker.worker import Worker
from tests.conftest import login, make_user

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"

PRODUCTS_CSV = (
    "name;price;currency;isbn\n"
    "Hravý početník 6 – 2. díl;62,0;CZK;\n"
    "Kadeřník & kadeřnice 2;223,00;CZK;\n"
    "Bez ceny;;CZK;\n"
    "Neznámy trh;10;USD;\n"
    "Hravý početník 6 – 2. díl;62;CZK;\n"
    "Zlé ISBN;10 €;;978-0-306-40615-8\n"
    "Kniha v eurách;10 €;;\n"
)


def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def upload(client, content: bytes, name="produkty.csv", kind="products", competitor_id=None):
    data = {"kind": kind}
    if competitor_id is not None:
        data["competitor_id"] = str(competitor_id)
    return client.post("/api/imports", data=data, files={"file": (name, content, "application/octet-stream")})


def statuses(batch):
    return [(r["row"], r["status"]) for r in batch["rows"]]


def test_products_csv_preview_then_commit(client, db):
    admin(client)
    db.add(Product(name="Kadeřník & kadeřnice 2"))
    db.commit()
    r = upload(client, PRODUCTS_CSV.encode("cp1250"))  # Windows-1250, as exported by Excel
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["status"] == "preview" and batch["file_format"] == "csv"
    assert statuses(batch) == [(2, "ok"), (3, "review"), (4, "error"), (5, "error"), (6, "duplicate"),
                               (7, "warning"), (8, "ok")]
    reasons = {r["row"]: r["reason"] for r in batch["rows"]}
    assert reasons[3].startswith("Možná zhoda s produktom „Kadeřník & kadeřnice 2“")  # a name alone is not enough
    assert reasons[6] == "Rovnaký produkt je už v riadku 2."
    assert reasons[4].startswith("Bez ceny a bez URL") and reasons[5].startswith("Chýba trh")
    assert reasons[7] == "Neplatné ISBN „978-0-306-40615-8“ sa ignoruje."  # the row is kept without it
    assert batch["stats"] == {"ok": 2, "warning": 1, "update": 0, "unchanged": 0, "review": 1, "duplicate": 1,
                              "error": 2}
    assert db.scalar(select(Product).where(Product.name == "Kniha v eurách")) is None  # nothing saved yet

    result = client.post(f"/api/imports/{batch['id']}/commit").json()
    assert result == {"processed": 7, "imported": 3, "updated": 0, "unchanged": 0, "review": 1, "duplicates": 1,
                      "errors": 2, "skipped_at_commit": []}
    euro = client.get("/api/products", params={"q": "eurách"}).json()["items"][0]
    assert euro["prices"] == [{"market": "SK", "price": "10.00", "currency": "EUR", "vat_mode": "with_vat"}]
    assert (euro["source_kind"], euro["source"]) == ("file", "produkty.csv") and euro["source_synced_at"]
    assert client.post(f"/api/imports/{batch['id']}/commit").status_code == 409  # only once


def test_commit_rechecks_against_current_catalogue_and_discard(client, db):
    admin(client)
    batch = upload(client, b"name,price,currency,isbn\nNova kniha,10,EUR,978-0-306-40615-7\n").json()
    assert statuses(batch) == [(2, "ok")]
    db.add(Product(name="Nova kniha (iný názov)", isbn="9780306406157"))  # created by someone else meanwhile
    db.commit()
    result = client.post(f"/api/imports/{batch['id']}/commit").json()
    assert (result["imported"], result["updated"]) == (0, 1)  # updated instead of a duplicate
    assert len(db.scalars(select(Product)).all()) == 1

    other = upload(client, b"name,price,currency\nIna kniha,10,EUR\n").json()
    assert client.post(f"/api/imports/{other['id']}/discard").status_code == 204
    assert client.get(f"/api/imports/{other['id']}").json()["status"] == "discarded"
    assert client.post(f"/api/imports/{other['id']}/commit").status_code == 409


def test_excel_and_feeds(client):
    admin(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["Názov", "Cena", "Mena", "EAN"])
    ws.append(["Písanka 1", 28, "CZK", "8594165541796"])
    ws.append(["Písanka 2", "28,50", "CZK", None])
    buffer = io.BytesIO()
    wb.save(buffer)
    batch = upload(client, buffer.getvalue(), name="katalog.xlsx").json()
    assert batch["file_format"] == "xlsx" and statuses(batch) == [(2, "ok"), (3, "ok")]
    assert batch["rows"][0]["data"]["ean"] == "8594165541796"

    heureka = """<?xml version="1.0" encoding="utf-8"?><SHOP>
      <SHOPITEM><ITEM_ID>T-1</ITEM_ID><PRODUCTNAME>Hravá matematika 2</PRODUCTNAME><PRICE_VAT>114</PRICE_VAT>
        <ISBN>978-0-306-40615-7</ISBN><URL>https://www.etaktik.cz/hrava-matematika-2/</URL></SHOPITEM>
      <SHOPITEM><ITEM_ID>T-2</ITEM_ID><PRODUCTNAME>Bez ceny</PRODUCTNAME></SHOPITEM></SHOP>"""
    batch = upload(client, heureka.encode(), name="heureka.xml").json()
    assert batch["file_format"] == "heureka" and statuses(batch) == [(1, "error"), (2, "error")]
    assert batch["rows"][0]["reason"].startswith("Chýba trh")  # Heureka has no currency: market must be given
    batch = client.post("/api/imports", data={"kind": "products", "market": "cz"},
                        files={"file": ("heureka.xml", heureka.encode(), "application/xml")}).json()
    assert statuses(batch) == [(1, "ok"), (2, "error")]
    assert batch["rows"][0]["data"]["prices"][0] == {"market": "CZ", "price": "114.00", "currency": "CZK",
                                                    "vat_mode": "with_vat"}
    google = """<?xml version="1.0"?><rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>
      <item><g:id>T-1</g:id><title>Hravá matematika 2</title><g:price>114.00 CZK</g:price>
        <g:gtin>9780306406157</g:gtin><link>https://www.etaktik.cz/m2</link>
        <g:availability>out of stock</g:availability></item></channel></rss>"""
    batch = upload(client, google.encode(), name="google.xml").json()
    assert batch["file_format"] == "google" and statuses(batch) == [(1, "ok")]
    data = batch["rows"][0]["data"]
    assert (data["isbn"], data["ean"], data["internal_id"]) == ("9780306406157", None, "T-1")  # 978… = ISBN
    assert (data["prices"][0]["market"], data["availability"]) == ("CZ", "out_of_stock")


ETAKTIK = Path(__file__).parent.parent / "fixtures" / "etaktik"


def upload_fixture(client, name: str, market: str | None = "CZ"):
    data = {"kind": "products", **({"market": market} if market else {})}
    r = client.post("/api/imports", data=data, files={"file": (name, (ETAKTIK / name).read_bytes(), "text/plain")})
    assert r.status_code == 201, r.text
    return r.json()


def commit(client, batch):
    r = client.post(f"/api/imports/{batch['id']}/commit")
    assert r.status_code == 200, r.text
    return r.json()


def test_etaktik_feed_file_and_reruns(client, db):
    """Real samples of etaktik.cz: Heureka feed, Google feed (CSV) and the attached product export."""
    admin(client)
    feed = upload_fixture(client, "heureka.xml")
    assert dict(statuses(feed)) == {1: "ok", 2: "ok", 3: "ok", 4: "warning", 5: "ok", 6: "ok", 7: "ok", 8: "ok"}
    rows = {r["data"]["internal_id"]: r for r in feed["rows"]}
    assert rows["94"]["reason"] == "Cena v zdroji je 0, produkt bude bez ceny."  # no price is invented
    assert rows["30"]["data"]["ean"] == "0761742976519"  # the valid one of two EAN elements
    assert rows["2849"]["data"]["name"] == "Keep calm & draw - animals"  # double-escaped in the feed
    assert commit(client, feed)["imported"] == 8

    abeceda = db.scalar(select(Product).where(Product.internal_id == "3"))
    assert (abeceda.isbn, abeceda.ean, abeceda.url) == ("9788077034524", None, "https://www.etaktik.cz/hrava-abeceda/")
    assert abeceda.image_url.startswith("https://www.etaktik.cz/UserFiles/Image/")
    assert (abeceda.category, abeceda.source_kind, abeceda.source) == (
        "1. stupeň ZŠ | Český jazyk | Hravá čeština", "feed", "heureka.xml")
    assert [(p.market, str(p.price), p.currency) for p in abeceda.prices] == [("CZ", "92.00", "CZK")]
    assert db.scalar(select(Product).where(Product.internal_id == "94")).prices == []

    again = upload_fixture(client, "heureka.xml")  # idempotent: the same feed again changes nothing
    assert {s for _, s in statuses(again)} == {"unchanged"}
    assert commit(client, again)["unchanged"] == 8

    google = upload_fixture(client, "google.csv", market=None)  # CSV variant of the Google feed
    assert {s for _, s in statuses(google)} == {"update"}
    assert all("dostupnosť" in r["reason"] for r in google["rows"])
    assert commit(client, google)["updated"] == 8
    db.expire_all()
    assert db.scalar(select(Product).where(Product.internal_id == "3")).availability == "in_stock"

    export = upload_fixture(client, "export_products.csv")
    by_id = {r["data"].get("internal_id"): r for r in export["rows"]}
    assert by_id["3"]["status"] == "update" and "parametre" in by_id["3"]["reason"]
    assert by_id["30"]["reason"].endswith("Neplatné ISBN „0031“ sa ignoruje.")
    assert by_id["2374"]["status"] == by_id["3223"]["status"] == "error"  # archive item / worksheet: no price, no URL
    assert by_id["2849"]["status"] == "unchanged"  # the file's tag list does not replace the feed category
    assert by_id["3619"]["status"] == "ok"  # in the file only (not on the web): kept, marked by its source
    assert by_id["2676"]["status"] == "ok" and by_id["2676"]["data"]["is_bundle"] is True  # Balíček pro 1. ročník
    result = commit(client, export)
    assert (result["imported"], result["updated"], result["errors"]) == (2, 5, 2)
    db.expire_all()
    abeceda = db.scalar(select(Product).where(Product.internal_id == "3"))
    assert abeceda.attributes == {"type": "Pracovní sešit", "grade": "1. ročník", "subject": "Čeština"}
    assert abeceda.category == "1. stupeň ZŠ | Český jazyk | Hravá čeština" and abeceda.source_kind == "feed"
    letsrock = db.scalar(select(Product).where(Product.internal_id == "3619"))
    assert (letsrock.url, letsrock.source_kind, letsrock.source) == (None, "file", "export_products.csv")

    rerun = upload_fixture(client, "export_products.csv")
    assert {s for _, s in statuses(rerun)} == {"unchanged", "error"}
    assert len(db.scalars(select(Product)).all()) == 10


def test_bundles_are_recognized_by_name_and_manual_decision_is_kept(client, db):
    admin(client)
    db.add(Product(name="Balíček Hravá čeština 1", internal_id="121", is_bundle=False))  # decided by hand
    db.commit()
    feed = upload_fixture(client, "heureka.xml")
    rows = {r["data"]["internal_id"]: r for r in feed["rows"]}
    assert rows["121"]["data"]["is_bundle"] is True and "balíček" not in rows["121"]["reason"]
    assert rows["3"]["data"]["is_bundle"] is None  # a single title stays undecided
    commit(client, feed)
    db.expire_all()
    assert db.scalar(select(Product).where(Product.internal_id == "121")).is_bundle is False
    assert db.scalar(select(Product).where(Product.internal_id == "3")).is_bundle is None


def test_existing_manual_products_are_matched_safely(client, db):
    admin(client)
    tracked = Product(name="Hravá prvouka 1 - metodická příručka pro učitele – revidované RVP ZV")
    tracked.prices = [ProductPrice(market="CZ", price=Decimal("189"), currency="CZK")]
    other_price = Product(name="Matika a já 1 – metodická příručka")
    other_price.prices = [ProductPrice(market="CZ", price=Decimal("199"), currency="CZK")]
    edition = Product(name="Hravá abeceda", isbn="9788075631688")  # same name, another ISBN
    db.add_all([tracked, other_price, edition])
    db.commit()
    feed = upload_fixture(client, "heureka.xml")
    rows = {r["data"]["internal_id"]: r for r in feed["rows"]}
    assert rows["3965"]["status"] == "update" and rows["3965"]["product_id"] == tracked.id
    assert "Spárované podľa názvu a rovnakej ceny." in rows["3965"]["reason"]
    assert rows["4058"]["status"] == "review"  # same name, different price: a person decides
    assert rows["3"]["status"] == "ok"  # another edition: a new product
    commit(client, feed)
    db.expire_all()
    tracked = db.get(Product, tracked.id)  # the same row (ID and its offers and history are kept)
    assert (tracked.internal_id, tracked.isbn, tracked.source_kind) == ("3965", "9788077037372", "feed")
    assert db.get(Product, other_price.id).internal_id is None


def test_conflicting_identifiers_and_source_priority(client, db):
    admin(client)
    db.add_all([Product(name="A", internal_id="3"), Product(name="B", isbn="9788077034524")])
    db.commit()
    feed = upload_fixture(client, "heureka.xml")
    row = next(r for r in feed["rows"] if r["data"]["internal_id"] == "3")
    assert row["status"] == "review" and "patria rôznym produktom" in row["reason"]

    db.execute(delete(Product))
    db.commit()
    commit(client, upload_fixture(client, "heureka.xml"))
    cheaper = b"ID;Nazev;Cena Eshop\n3;Hrava abeceda;95\n"
    # an uploaded file does not replace the price that came from the e-shop feed
    batch = client.post("/api/imports", data={"kind": "products", "market": "CZ"},
                        files={"file": ("ceny.csv", cheaper, "text/csv")}).json()
    assert statuses(batch) == [(2, "unchanged")]
    newer = (ETAKTIK / "heureka.xml").read_bytes().replace(b"<PRICE_VAT>92.00", b"<PRICE_VAT>95.00")
    batch = client.post("/api/imports", data={"kind": "products", "market": "CZ"},
                        files={"file": ("feed.xml", newer, "application/xml")}).json()
    row = next(r for r in batch["rows"] if r["data"]["internal_id"] == "3")
    assert row["status"] == "update" and "cena CZ 92.00 CZK → 95.00 CZK" in row["reason"]


def test_rejects_unsafe_and_oversized_files(client):
    admin(client)
    xxe = b"""<?xml version="1.0"?><!DOCTYPE SHOP [<!ENTITY x SYSTEM "file:///etc/passwd">]>
              <SHOP><SHOPITEM><PRODUCTNAME>&x;</PRODUCTNAME></SHOPITEM></SHOP>"""
    r = upload(client, xxe, name="feed.xml")
    assert r.status_code == 422 and "XML sa nedá spracovať" in r.json()["detail"]
    assert upload(client, b"<root><a/></root>", name="x.xml").status_code == 422
    assert upload(client, b"x" * (5 * 1024 * 1024 + 10)).status_code == 422
    assert upload(client, b"foo,bar\n1,2\n").status_code == 422          # no known column
    assert upload(client, b"name,price\n" + b"x,1\n" * 10_001).status_code == 422
    assert upload(client, b"").status_code == 422
    assert upload(client, b"PK\x03\x04broken", name="a.xlsx").status_code == 422


def test_offers_import(client, db):
    admin(client)
    alter = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz", scrape_config=ScrapeConfig())
    fraus = Competitor(name="Fraus", base_url="https://www.fraus.cz/", domain="fraus.cz", scrape_config=ScrapeConfig())
    db.add_all([alter, fraus, Product(name="Hravá prvouka 1", ean="8594165541536")])
    db.commit()
    db.add(CompetitorProduct(competitor_id=alter.id, product_url="https://www.alter.cz/produkt/1"))
    db.commit()
    csv_file = (
        b"url,variant,product_ean,product_name\n"
        b"https://www.alter.cz/produkt/716,,8594165541536,\n"      # assigned by EAN
        b"https://www.fraus.cz/p/9,,,Neexistuje\n"                 # warning: product not found
        b"https://www.alter.cz/produkt/1,,,\n"                     # already tracked
        b"https://www.alter.cz/produkt/716,,,\n"                   # duplicate within the file
        b"https://www.didaktis.sk/p/1,,,\n"                        # no such competitor
        b"not a url,,,\n"
    )
    batch = upload(client, csv_file, kind="offers").json()
    assert statuses(batch) == [(2, "ok"), (3, "warning"), (4, "duplicate"), (5, "duplicate"), (6, "error"),
                               (7, "error")]
    assert batch["rows"][0]["data"]["product_name"] == "Hravá prvouka 1"
    result = client.post(f"/api/imports/{batch['id']}/commit").json()
    assert result["imported"] == 2
    offers = {o["product_url"]: o for o in client.get("/api/offers").json()["items"]}
    assert offers["https://www.alter.cz/produkt/716"]["product"]["name"] == "Hravá prvouka 1"
    assert offers["https://www.alter.cz/produkt/716"]["matching_method"] == "manual"
    assert offers["https://www.fraus.cz/p/9"]["product"] is None

    only_fraus = upload(client, b"url\nhttps://www.alter.cz/produkt/2\n", kind="offers", competitor_id=fraus.id).json()
    assert statuses(only_fraus) == [(2, "error")]


def test_import_permissions(client):
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert upload(client, b"name,price\nA,1\n").status_code == 403


# ---------- category page helper ----------

def test_discover_links_on_alter_homepage():
    html = (FIXTURES / "alter_cz" / "home.html").read_text("utf-8")
    links = discover_links(html, "https://www.alter.cz/", "alter.cz", tracked={"https://www.alter.cz/produkt/1111"})
    urls = [c["url"] for c in links]
    assert urls and all("/produkt/" in u for u in urls)  # image galleries and cart dialogs are left out
    tracked = [c for c in links if c["url"] == "https://www.alter.cz/produkt/1111"]
    assert not tracked or tracked[0]["tracked"] is True
    assert len(urls) == len(set(urls))


def test_discover_job_end_to_end(client, db, monkeypatch):
    monkeypatch.setattr("app.worker.processor.time.sleep", lambda s: None)
    admin(client)
    competitor = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz",
                            scrape_config=ScrapeConfig(min_interval_seconds=1))
    db.add(competitor)
    db.commit()
    page = '<a href="/produkt/1">Kniha 1</a><a href="/produkt/2"><img alt="Kniha 2"></a><a href="/kosik/">x</a>' \
           '<a href="https://www.fraus.cz/produkt/3">cudzí</a>'
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=page, headers={"content-type": "text/html"})
                                    if r.url.path == "/novinky" else httpx.Response(404))
    worker = Worker(Settings(scheduler_in_worker=False),
                    fetcher=Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"], transport=transport))
    assert client.post(f"/api/competitors/{competitor.id}/discover",
                       json={"url": "https://www.fraus.cz/x"}).status_code == 422
    job = client.post(f"/api/competitors/{competitor.id}/discover", json={"url": "https://www.alter.cz/novinky"}).json()
    while worker.run_once():
        pass
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded"
    assert [(c["url"], c["text"]) for c in done["result"]["candidates"]] == [
        ("https://www.alter.cz/produkt/1", "Kniha 1"), ("https://www.alter.cz/produkt/2", "Kniha 2")]
    assert queue.PRIORITY["discover"] < queue.PRIORITY["scheduled"]


def test_discover_uses_card_title_for_generic_link_text():
    html = '<div class="card"><h3>Matematika 2</h3><p>…</p><a href="/produkt/5">Více informací</a></div>'
    [link] = discover_links(html, "https://www.alter.cz/", "alter.cz", set())
    assert link["text"].startswith("Matematika 2")


def test_discover_card_text_for_links_without_text():
    html = '<div><span>Hravá písanka 1</span><a href="/produkt/7"><img src="x.png"></a></div>'
    [link] = discover_links(html, "https://www.alter.cz/", "alter.cz", set())
    assert link["text"] == "Hravá písanka 1"
