import io
from pathlib import Path

import httpx
from openpyxl import Workbook
from sqlalchemy import select

from app.config import Settings
from app.models import Competitor, CompetitorProduct, Product, ScrapeConfig
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
    assert statuses(batch) == [(2, "ok"), (3, "duplicate"), (4, "error"), (5, "error"), (6, "duplicate"),
                               (7, "error"), (8, "ok")]
    reasons = {r["row"]: r["reason"] for r in batch["rows"]}
    assert reasons[3].startswith("Produkt už existuje") and reasons[6] == "Rovnaký produkt je už v riadku 2"
    assert reasons[4] == "Chýba cena." and reasons[5].startswith("Chýba trh")
    assert reasons[7].startswith("isbn")
    assert batch["stats"] == {"ok": 2, "warning": 0, "duplicate": 2, "error": 3}
    assert db.scalar(select(Product).where(Product.name == "Kniha v eurách")) is None  # nothing saved yet

    result = client.post(f"/api/imports/{batch['id']}/commit").json()
    assert result == {"imported": 2, "duplicates": 2, "errors": 3, "skipped_at_commit": []}
    euro = client.get("/api/products", params={"q": "eurách"}).json()["items"][0]
    assert euro["prices"] == [{"market": "SK", "price": "10.00", "currency": "EUR", "vat_mode": "with_vat"}]
    assert client.post(f"/api/imports/{batch['id']}/commit").status_code == 409  # only once


def test_commit_rechecks_duplicates_and_discard(client, db):
    admin(client)
    batch = upload(client, b"name,price,currency\nNova kniha,10,EUR\n").json()
    db.add(Product(name="Nova kniha"))  # created by someone else after the preview
    db.commit()
    result = client.post(f"/api/imports/{batch['id']}/commit").json()
    assert result["imported"] == 0 and result["skipped_at_commit"][0]["reason"] == "Produkt medzitým pribudol."

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
    google = """<?xml version="1.0"?><rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>
      <item><g:id>T-1</g:id><title>Hravá matematika 2</title><g:price>114.00 CZK</g:price>
        <g:gtin>9780306406157</g:gtin><link>https://www.etaktik.cz/m2</link></item></channel></rss>"""
    batch = upload(client, google.encode(), name="google.xml").json()
    assert batch["file_format"] == "google" and statuses(batch) == [(1, "ok")]
    data = batch["rows"][0]["data"]
    assert (data["ean"], data["internal_id"], data["prices"][0]["market"]) == ("9780306406157", "T-1", "CZ")


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
