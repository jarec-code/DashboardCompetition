from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db import SessionLocal
from app.models import (
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    PriceHistory,
    Product,
    ScrapeConfig,
    ScrapeJob,
    ScrapeRun,
)
from app.scraping.fetcher import Fetcher
from app.scraping.parsers.base import Extracted
from app.services import pricing
from app.worker import queue
from app.worker.worker import Worker
from tests.conftest import login, make_user

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"
ALTER_1111 = (FIXTURES / "alter_cz" / "produkt_1111.html").read_text(encoding="utf-8")


class FakeSite:
    """In-memory web: URL -> response. Counts requests."""

    def __init__(self):
        self.pages: dict[str, httpx.Response | str] = {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        page = self.pages.get(str(request.url))
        if page is None:
            return httpx.Response(404)
        if isinstance(page, str):
            return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
        return page


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr("app.worker.processor.time.sleep", lambda s: None)  # politeness pauses not needed here
    return FakeSite()


@pytest.fixture
def worker(site):
    fetcher = Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"], transport=httpx.MockTransport(site.handler))
    return Worker(Settings(scheduler_in_worker=False), fetcher=fetcher)


def setup_offer(db, parser="alter_cz", status="active", url="https://www.alter.cz/produkt/1111", **offer_kw):
    competitor = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz", status=status,
                            scrape_config=ScrapeConfig(parser_type=parser, min_interval_seconds=1))
    db.add(competitor)
    db.flush()
    offer = CompetitorProduct(competitor_id=competitor.id, product_url=url, **offer_kw)
    db.add(offer)
    db.commit()
    return competitor, offer


def history(db, offer_id):
    return list(db.scalars(select(PriceHistory).where(PriceHistory.competitor_product_id == offer_id)
                           .order_by(PriceHistory.observed_at)))


# ---------- record_observation rules ----------

def extracted(price, availability="in_stock", currency="CZK", original=None):
    return Extracted(price=Decimal(price), currency=currency, availability=availability,
                     original_price=Decimal(original) if original else None, title="Naše vlast")


def test_history_only_on_real_changes(db):
    competitor, offer = setup_offer(db)
    t0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
    steps = [("44.00", "in_stock", None, ["initial"]), ("44.00", "in_stock", None, None),
             ("39.00", "in_stock", None, ["price_down"]), ("39.00", "out_of_stock", None, ["availability"]),
             ("45.00", "in_stock", "49.00", ["price_up", "availability", "original_price"])]
    for i, (price, availability, original, expected) in enumerate(steps):
        outcome = pricing.record_success(db, offer, extracted(price, availability, original=original),
                                         t0 + timedelta(hours=i))
        db.commit()
        assert outcome.change_types == (expected or [])
    rows = history(db, offer.id)
    assert [(str(r.price), r.change_types) for r in rows] == [
        ("44.00", ["initial"]), ("39.00", ["price_down"]), ("39.00", ["availability"]),
        ("45.00", ["price_up", "availability", "original_price"])]
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    assert current.last_checked_at == t0 + timedelta(hours=4) and current.price == Decimal("45.00")


def test_failure_keeps_last_valid_price(db):
    competitor, offer = setup_offer(db)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    pricing.record_success(db, offer, extracted("44.00"), t0)
    pricing.record_failure(db, offer, competitor, "Stránka neexistuje (HTTP 404).", t0 + timedelta(minutes=30))
    db.commit()
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    assert (current.price, current.last_error, current.is_stale) == (Decimal("44.00"), "Stránka neexistuje (HTTP 404).",
                                                                     False)
    assert current.last_success_at == t0 and len(history(db, offer.id)) == 1
    # an old last success makes the price stale (2x the 24 h default interval)
    pricing.record_failure(db, offer, competitor, "timeout", t0 + timedelta(hours=49))
    db.commit()
    db.refresh(current)
    assert current.is_stale and current.price == Decimal("44.00")


# ---------- worker end to end ----------

def run_all(worker) -> int:
    count = 0
    while worker.run_once():
        count += 1
    return count


def test_manual_check_end_to_end(client, db, site, worker):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    competitor, offer = setup_offer(db)
    db.add(Product(name="Naše vlast – pracovní listy", ean="8594165541796"))
    db.commit()
    site.pages["https://www.alter.cz/produkt/1111"] = ALTER_1111

    job = client.post(f"/api/offers/{offer.id}/check").json()
    assert job["status"] == "pending" and job["kind"] == "manual"
    assert client.post(f"/api/offers/{offer.id}/check").json()["id"] == job["id"]  # no duplicate job
    assert run_all(worker) == 1

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded", done
    assert done["result"]["extracted"]["price"] == "44.00" and done["result"]["change_types"] == ["initial"]
    assert done["result"]["parser"] == "alter_cz@1"
    listed = client.get(f"/api/competitors/{competitor.id}/offers").json()["items"][0]
    assert (listed["current"]["price"], listed["current"]["availability"]) == ("44.00", "in_stock")
    assert listed["product"]["name"] == "Naše vlast – pracovní listy"  # matched by EAN found on the page
    assert listed["matching_method"] == "identifier"
    runs = client.get(f"/api/competitors/{competitor.id}/runs").json()
    assert [(r["status"], r["success_count"], r["price_change_count"]) for r in runs] == [("succeeded", 1, 1)]
    assert [h["change_types"] for h in client.get(f"/api/offers/{offer.id}/history").json()] == [["initial"]]

    # same page again: no new history row
    client.post(f"/api/offers/{offer.id}/check")
    run_all(worker)
    assert len(client.get(f"/api/offers/{offer.id}/history").json()) == 1
    assert site.requests.count("https://www.alter.cz/robots.txt") == 1  # robots.txt cached


def test_failed_check_records_error(client, db, site, worker):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    _, offer = setup_offer(db)
    site.pages["https://www.alter.cz/produkt/1111"] = "<html><h1>Nová stránka</h1></html>"
    job = client.post(f"/api/offers/{offer.id}/check").json()
    run_all(worker)
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert (done["status"], done["error_kind"]) == ("failed", "permanent")
    assert "zmeniť štruktúru" in done["last_error"]
    current = client.get("/api/offers").json()["items"][0]["current"]
    assert current["price"] is None and "zmeniť štruktúru" in current["last_error"]


def test_test_check_saves_no_price(client, db, site, worker):
    make_user("admin@example.com")
    login(client, "admin@example.com")
    competitor, _ = setup_offer(db)
    site.pages["https://www.alter.cz/produkt/716"] = (FIXTURES / "alter_cz" / "produkt_716.html").read_text("utf-8")
    assert client.post(f"/api/competitors/{competitor.id}/test",
                       json={"url": "https://www.fraus.cz/x"}).status_code == 422
    job = client.post(f"/api/competitors/{competitor.id}/test", json={"url": "https://www.alter.cz/produkt/716"}).json()
    run_all(worker)
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded"
    assert done["result"]["extracted"]["price"] == "65.00"
    assert done["result"]["extracted"]["identifiers"] == {"ean": "8594165541536"}
    assert db.scalar(select(func.count(CurrentPrice.id))) == 0 and db.scalar(select(func.count(ScrapeRun.id))) == 0
    config = client.get(f"/api/competitors/{competitor.id}").json()["scrape_config"]
    assert config["last_test_status"] == "succeeded" and config["last_test_at"]

    job = client.post(f"/api/competitors/{competitor.id}/test",
                      json={"url": "https://www.alter.cz/produkt/716", "variant": "elektronická"}).json()
    run_all(worker)
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed" and "Dostupné varianty: tištěná" in done["last_error"]
    assert client.get(f"/api/competitors/{competitor.id}").json()["scrape_config"]["last_test_status"] == "failed"


def test_paused_and_archived_competitors(db, site, worker):
    competitor, offer = setup_offer(db, status="paused")
    site.pages["https://www.alter.cz/produkt/1111"] = ALTER_1111
    scheduled = queue.enqueue(db, competitor.id, "scheduled", offer_id=offer.id)
    db.commit()
    run_all(worker)
    db.refresh(scheduled)
    assert scheduled.status == "cancelled" and "pozastavený" in scheduled.last_error
    assert site.requests == []  # nothing was fetched

    manual = queue.enqueue(db, competitor.id, "manual", offer_id=offer.id)  # a user may still check by hand
    db.commit()
    run_all(worker)
    db.refresh(manual)
    assert manual.status == "succeeded"

    competitor.status = "archived"
    job = queue.enqueue(db, competitor.id, "manual", offer_id=offer.id)
    db.commit()
    run_all(worker)
    db.refresh(job)
    assert job.status == "cancelled"


def test_temporary_error_and_429(client, db, site, worker):
    _, offer = setup_offer(db)
    site.pages["https://www.alter.cz/produkt/1111"] = httpx.Response(429, headers={"retry-after": "600"})
    job = queue.enqueue(db, offer.competitor_id, "manual", offer_id=offer.id)
    db.commit()
    run_all(worker)
    db.refresh(job)
    assert (job.status, job.error_kind) == ("failed", "temporary")
    # the domain is paused for the Retry-After period: the next job does not even request the page
    requests_before = len(site.requests)
    job2 = queue.enqueue(db, offer.competitor_id, "manual", offer_id=offer.id)
    db.commit()
    run_all(worker)
    db.refresh(job2)
    assert job2.status == "failed" and "pauzu" in job2.last_error and len(site.requests) == requests_before


# ---------- queue mechanics ----------

def test_claim_skips_locked_jobs(db):
    competitor, offer = setup_offer(db)
    _, other = setup_offer(db, url="https://www.alter.cz/produkt/2")
    queue.enqueue(db, competitor.id, "scheduled", offer_id=offer.id)
    queue.enqueue(db, other.competitor_id, "scheduled", offer_id=other.id)
    db.commit()
    with SessionLocal() as a, SessionLocal() as b:
        first = queue.claim(a, limit=1)
        second = queue.claim(b, limit=5)
        assert len(first) == 1 and len(second) == 1 and first[0].id != second[0].id
        assert queue.claim(b, limit=5) == []


def test_manual_request_promotes_open_scheduled_job(db):
    competitor, offer = setup_offer(db)
    scheduled = queue.enqueue(db, competitor.id, "scheduled", offer_id=offer.id,
                              scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    manual = queue.enqueue(db, competitor.id, "manual", offer_id=offer.id)
    db.commit()
    assert manual.id == scheduled.id and (manual.kind, manual.priority) == ("manual", 20)
    assert manual.scheduled_at <= datetime.now(UTC)


def test_abandoned_jobs_are_recovered_after_restart(db, site, worker):
    competitor, offer = setup_offer(db)
    site.pages["https://www.alter.cz/produkt/1111"] = ALTER_1111
    job = queue.enqueue(db, competitor.id, "scheduled", offer_id=offer.id)
    db.commit()
    queue.claim(db)                      # a worker took the job ...
    db.refresh(job)
    job.locked_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()                          # ... and crashed long ago
    assert queue.recover_abandoned(db, lease_seconds=600) == 1
    db.refresh(job)
    assert job.status == "pending"
    run_all(worker)
    db.refresh(job)
    assert job.status == "succeeded" and job.attempt_count == 2

    job3 = queue.enqueue(db, competitor.id, "scheduled", offer_id=offer.id)
    db.commit()
    job3.status, job3.attempt_count, job3.locked_at = "running", 3, datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    queue.recover_abandoned(db, lease_seconds=600)
    db.refresh(job3)
    assert job3.status == "failed"  # no endless retries


def test_jobs_api_permissions(client, db):
    competitor, offer = setup_offer(db)
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.post(f"/api/offers/{offer.id}/check").status_code == 403
    test_url = f"/api/competitors/{competitor.id}/test"
    assert client.post(test_url, json={"url": "https://www.alter.cz/x"}).status_code == 403
    assert {p["name"] for p in client.get("/api/parsers").json()} >= {"auto", "css", "xpath", "alter_cz"}
    db.add(ScrapeJob(competitor_id=competitor.id, kind="test", priority=10, status="pending", attempt_count=0))
    db.commit()
    assert client.get("/api/jobs/1").status_code == 200
