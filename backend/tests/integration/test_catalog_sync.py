"""Regular update of our own catalogue from the e-shop feed, run by the worker."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import Settings
from app.models import ImportBatch, Product
from app.scraping.fetcher import Fetcher
from app.services import catalog_sync
from app.worker.worker import Worker
from tests.conftest import login, make_user

FEED = (Path(__file__).parent.parent / "fixtures" / "etaktik" / "heureka.xml").read_bytes()
FEED_URL = "https://www.etaktik.cz/export/heureka.php"


def fake_shop(feed: bytes = FEED, robots: str = "User-agent: *\nAllow: /\n", requests: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == "/export/heureka.php":
            return httpx.Response(200, content=feed, headers={"content-type": "application/xml"})
        return httpx.Response(404)
    return Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"], transport=httpx.MockTransport(handle))


def run_worker(fetcher: Fetcher) -> None:
    worker = Worker(Settings(scheduler_in_worker=False), fetcher=fetcher)
    worker.run_once()


def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def test_settings_run_now_and_daily_rerun(client, db):
    admin(client)
    assert client.get("/api/settings/catalog-sync").json()["settings"]["feed_url"] is None
    assert client.post("/api/settings/catalog-sync/run").status_code == 409  # no feed yet
    bad = client.put("/api/settings/catalog-sync", json={"enabled": True, "feed_url": "", "market": "CZ",
                                                         "interval_hours": 24})
    assert bad.status_code == 422
    r = client.put("/api/settings/catalog-sync", json={"enabled": False, "feed_url": FEED_URL, "market": "CZ",
                                                       "interval_hours": 24})
    assert r.status_code == 200, r.text

    requests: list[str] = []
    run_worker(fake_shop(requests=requests))
    assert requests == []  # automatic updates are off and nothing was requested
    assert client.post("/api/settings/catalog-sync/run").status_code == 202
    run_worker(fake_shop(requests=requests))
    assert requests == ["/robots.txt", "/export/heureka.php"]  # one feed download

    state = client.get("/api/settings/catalog-sync").json()
    assert state["status"]["last_error"] is None and state["status"]["requested_at"] is None
    assert state["last_run"]["stats"]["imported"] == 8 and state["last_run"]["file_name"] == FEED_URL
    product = db.scalar(select(Product).where(Product.internal_id == "3"))
    assert (product.source_kind, product.source) == ("feed", "etaktik.cz (feed)") and product.source_synced_at

    # Daily run: only when due; unchanged rows are not kept in the stored batch.
    client.put("/api/settings/catalog-sync", json={"enabled": True, "feed_url": FEED_URL, "market": "CZ",
                                                   "interval_hours": 24})
    settings, status = catalog_sync.load_settings(db), catalog_sync.load_status(db)
    now = datetime.now(UTC)
    assert not catalog_sync.is_due(settings, status, now + timedelta(hours=23))
    assert catalog_sync.is_due(settings, status, now + timedelta(hours=25))
    newer = FEED.replace(b"<PRICE_VAT>92.00", b"<PRICE_VAT>95.00")
    assert catalog_sync.run_if_due(db, fake_shop(newer), now + timedelta(hours=25))["updated"] == 1
    db.expire_all()
    assert str(db.scalar(select(Product).where(Product.internal_id == "3")).prices[0].price) == "95.00"
    batch = db.get(ImportBatch, catalog_sync.load_status(db).last_batch_id)
    assert [r["status"] for r in batch.rows] == ["update"]
    assert len(db.scalars(select(Product)).all()) == 8  # no duplicates


def test_failures_are_recorded_and_robots_respected(client, db):
    admin(client)
    client.put("/api/settings/catalog-sync", json={"enabled": True, "feed_url": FEED_URL, "market": "CZ",
                                                   "interval_hours": 24})
    run_worker(fake_shop(robots="User-agent: *\nDisallow: /export/\n"))
    state = client.get("/api/settings/catalog-sync").json()
    assert "robots.txt" in state["status"]["last_error"] and state["status"]["last_success_at"] is None
    assert db.scalars(select(Product)).all() == []

    catalog_sync.request_run(db)
    db.commit()
    run_worker(fake_shop(feed=b"<html><body>Not a feed</body></html>"))
    assert "Neznámy XML formát" in client.get("/api/settings/catalog-sync").json()["status"]["last_error"]


def test_viewer_sees_but_cannot_change(client):
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/settings/catalog-sync").status_code == 200
    assert client.post("/api/settings/catalog-sync/run").status_code == 403
