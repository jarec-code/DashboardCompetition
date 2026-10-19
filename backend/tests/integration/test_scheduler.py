from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from app.config import Settings
from app.db import SessionLocal
from app.models import (
    Competitor,
    CompetitorProduct,
    CurrentPrice,
    Product,
    ScrapeConfig,
    ScrapeJob,
    ScrapeRun,
)
from app.scraping.fetcher import Fetcher
from app.services import scheduling
from app.worker import queue, scheduler
from app.worker.worker import Worker
from tests.conftest import login, make_user

ALTER_PAGE = (Path(__file__).parent.parent / "fixtures" / "html" / "alter_cz" / "produkt_1111.html").read_text("utf-8")
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)  # 12:00 in Bratislava


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr("app.worker.processor.time.sleep", lambda s: None)
    pages: dict[str, httpx.Response | str] = {}

    def handler(request):
        page = pages.get(str(request.url))
        if page is None:
            return httpx.Response(404)
        return page if isinstance(page, httpx.Response) else httpx.Response(
            200, text=page, headers={"content-type": "text/html; charset=utf-8"})
    pages["handler"] = handler
    return pages


@pytest.fixture
def worker(site):
    fetcher = Fetcher(Settings(), resolver=lambda h, p: ["93.184.216.34"],
                      transport=httpx.MockTransport(site["handler"]))
    return Worker(Settings(scheduler_in_worker=False), fetcher=fetcher)


def competitor(db, name="Alter", domain="alter.cz", status="active", enabled=True, frequency=None, retries=2):
    c = Competitor(name=name, base_url=f"https://www.{domain}/", domain=domain, status=status,
                   default_frequency_hours=frequency,
                   scrape_config=ScrapeConfig(parser_type="alter_cz", min_interval_seconds=5, enabled=enabled,
                                              max_retries=retries))
    db.add(c)
    db.flush()
    return c


def offer(db, comp, path, **kw):
    o = CompetitorProduct(competitor_id=comp.id, product_url=f"https://www.{comp.domain}/produkt/{path}", **kw)
    db.add(o)
    db.flush()
    return o


def jobs(db):
    db.expire_all()  # read the state written by the worker's own session
    return list(db.scalars(select(ScrapeJob).order_by(ScrapeJob.scheduled_at, ScrapeJob.id)))


def run_all(worker, db):
    """Process every job that is due now (scheduled_at in the future is made due)."""
    while True:
        db.execute(text("UPDATE scrape_jobs SET scheduled_at = now() WHERE status = 'pending'"))
        db.commit()
        if not worker.run_once():
            return


# ---------- selection and planning ----------

def test_tick_plans_only_due_active_offers(db):
    alter = competitor(db)
    paused = competitor(db, "Paused", "paused.cz", status="paused")
    disabled = competitor(db, "Disabled", "disabled.cz", enabled=False)
    strategic = Product(name="A", priority="strategic")
    stopped = Product(name="B", tracking_status="paused")
    db.add_all([strategic, stopped])
    db.flush()
    due_new = offer(db, alter, 1)
    due_old = offer(db, alter, 2, next_check_at=NOW - timedelta(hours=1), product_id=strategic.id)
    offer(db, alter, 3, next_check_at=NOW + timedelta(hours=1))      # not due yet
    offer(db, alter, 4, status="paused")
    offer(db, alter, 5, product_id=stopped.id)                       # product tracking paused
    offer(db, paused, 6)
    offer(db, disabled, 7)
    with_open_job = offer(db, alter, 8)
    db.commit()
    queue.enqueue(db, alter.id, "manual", offer_id=with_open_job.id)
    db.commit()

    result = scheduler.tick(db, now=NOW)
    assert (result.status, result.jobs, result.runs) == ("planned", 2, 1)
    planned = [j for j in jobs(db) if j.kind == "scheduled"]
    assert {j.competitor_product_id for j in planned} == {due_new.id, due_old.id}
    assert sorted((j.scheduled_at - NOW).total_seconds() for j in planned) == [0, 5]  # spaced by min interval
    run = db.scalars(select(ScrapeRun)).one()
    assert (run.trigger, run.total_count, run.status) == ("scheduled", 2, "running")
    db.refresh(due_new)
    db.refresh(due_old)
    assert timedelta(hours=22.7) < due_new.next_check_at - NOW < timedelta(hours=25.3)   # 24 h +-5 %
    assert timedelta(hours=7.5) < due_old.next_check_at - NOW < timedelta(hours=8.5)     # strategic 8 h

    assert scheduler.tick(db, now=NOW).jobs == 0  # nothing new: offers moved forward / have open jobs


def test_interval_order(db):
    product = Product(name="A", priority="stable")
    db.add(product)
    alter = competitor(db, frequency=48)
    other = competitor(db, "Other", "other.cz")
    db.flush()
    a = offer(db, alter, 1, product_id=product.id)
    b = offer(db, other, 1, product_id=product.id)
    c = offer(db, other, 2, product_id=product.id, custom_frequency_hours=6)
    d = offer(db, other, 3)
    assert scheduling.interval_hours(db, a, alter) == 48   # competitor frequency is the site's limit
    assert scheduling.interval_hours(db, b, other) == 120  # product priority "stable"
    assert scheduling.interval_hours(db, c, other) == 6    # offer's own frequency wins
    assert scheduling.interval_hours(db, d, other) == 24   # default


def test_parallel_schedulers_do_not_plan_twice(db):
    alter = competitor(db)
    offer(db, alter, 1)
    db.commit()
    with SessionLocal() as other:
        other.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": scheduler.LOCK_ID})  # another tick runs
        assert scheduler.tick(db, now=NOW).status == "locked"
        other.rollback()
    assert scheduler.tick(db, now=NOW).jobs == 1


def test_window_and_disabled(db):
    alter = competitor(db)
    offer(db, alter, 1)
    db.commit()
    scheduling.save_settings(db, scheduling.SchedulingSettings(window_start_hour=1, window_end_hour=5))
    db.commit()
    assert scheduler.tick(db, now=NOW).status == "outside_window"                     # 12:00 local
    assert scheduler.tick(db, now=datetime(2026, 10, 20, 1, 30, tzinfo=UTC)).status == "planned"  # 03:30 local

    s = scheduling.SchedulingSettings(window_start_hour=22, window_end_hour=5)          # over midnight
    assert scheduling.in_window(s, datetime(2026, 10, 20, 21, 30, tzinfo=UTC))           # 23:30 local
    assert not scheduling.in_window(s, NOW)
    scheduling.save_settings(db, scheduling.SchedulingSettings(scheduler_enabled=False))
    db.commit()
    assert scheduler.tick(db, now=NOW).status == "disabled"
    assert scheduling.get_heartbeat(db, "scheduler") == NOW


# ---------- runs, retries, failures ----------

def test_run_summary_partial(db, site, worker):
    alter = competitor(db)
    offer(db, alter, 1111)
    missing = offer(db, alter, 9999)
    db.commit()
    site["https://www.alter.cz/produkt/1111"] = ALTER_PAGE
    scheduler.tick(db)
    run_all(worker, db)
    run = db.scalars(select(ScrapeRun)).one()
    assert (run.status, run.success_count, run.failure_count, run.price_change_count) == ("partial", 1, 1, 1)
    assert "HTTP 404" in run.error_summary and run.finished_at is not None
    assert db.scalar(select(CurrentPrice.consecutive_failures).where(
        CurrentPrice.competitor_product_id == missing.id)) == 1


def test_temporary_errors_retry_with_backoff_then_fail(db, site, worker):
    alter = competitor(db, retries=2)
    o = offer(db, alter, 1111)
    db.commit()
    site["https://www.alter.cz/produkt/1111"] = httpx.Response(503)
    scheduler.tick(db)
    job = jobs(db)[0]

    started = datetime.now(UTC)
    worker.run_once()
    db.refresh(job)
    assert (job.status, job.attempt_count, job.error_kind) == ("pending", 1, "temporary")
    assert timedelta(seconds=45) < job.scheduled_at - started < timedelta(seconds=75)  # ~60 s +-20 %
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == o.id))
    assert current.consecutive_failures == 0 and "HTTP 503" in current.last_error   # not a failed check yet

    for expected_attempt, expected_status in ((2, "pending"), (3, "failed")):
        db.execute(text("UPDATE scrape_jobs SET scheduled_at = now()"))
        db.commit()
        worker.run_once()
        db.refresh(job)
        assert (job.attempt_count, job.status) == (expected_attempt, expected_status)
    db.refresh(current)
    assert current.consecutive_failures == 1
    assert db.scalars(select(ScrapeRun)).one().status == "failed"

    # success resets the counter
    site["https://www.alter.cz/produkt/1111"] = ALTER_PAGE
    queue.enqueue(db, alter.id, "manual", offer_id=o.id)
    db.commit()
    run_all(worker, db)
    db.refresh(current)
    assert current.consecutive_failures == 0 and current.last_error is None


def test_retry_after_is_respected_and_permanent_errors_do_not_retry(db, site, worker):
    alter = competitor(db, retries=3)
    offer(db, alter, 1)
    offer(db, alter, 2)
    db.commit()
    site["https://www.alter.cz/produkt/1"] = httpx.Response(429, headers={"retry-after": "900"})
    site["https://www.alter.cz/produkt/2"] = httpx.Response(410)
    scheduler.tick(db)
    worker.run_once()
    first = jobs(db)
    limited = [j for j in first if j.competitor_product_id and j.attempt_count == 1][0]
    assert limited.scheduled_at - datetime.now(UTC) > timedelta(seconds=850)
    worker.processor._blocked_until.clear()
    db.execute(text("UPDATE scrape_jobs SET scheduled_at = now() WHERE id <> :id"), {"id": limited.id})
    db.commit()
    worker.run_once()
    gone = [j for j in jobs(db) if j.id != limited.id][0]
    assert (gone.status, gone.attempt_count, gone.error_kind) == ("failed", 1, "permanent")


def test_recovered_job_closes_its_run(db):
    alter = competitor(db)
    offer(db, alter, 1)
    db.commit()
    scheduler.tick(db)
    job = jobs(db)[0]
    job.status, job.attempt_count, job.locked_at = "running", 3, datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    queue.recover_abandoned(db, lease_seconds=600)
    assert db.scalars(select(ScrapeRun)).one().status == "failed"


# ---------- rescheduling, API ----------

def admin(client):
    make_user("admin@example.com")
    login(client, "admin@example.com")


def test_frequency_changes_reschedule(client, db):
    admin(client)
    alter = competitor(db)
    product = Product(name="A", priority="regular")
    db.add(product)
    db.flush()
    o = offer(db, alter, 1, product_id=product.id, match_status="matched", matching_method="manual")
    checked = datetime.now(UTC) - timedelta(hours=2)
    db.add(CurrentPrice(competitor_product_id=o.id, price=10, currency="CZK", last_checked_at=checked,
                        last_success_at=checked))
    db.commit()

    def next_in_hours():
        db.expire_all()
        return round((db.get(CompetitorProduct, o.id).next_check_at - checked).total_seconds() / 3600, 1)

    client.patch(f"/api/products/{product.id}", json={"priority": "strategic"})
    assert next_in_hours() == 8
    r = client.put("/api/settings/scheduling", json={"priority_interval_hours": {"strategic": 12}})
    assert r.status_code == 200
    assert r.json()["priority_interval_hours"] == {"strategic": 12, "regular": 24, "stable": 120}
    assert next_in_hours() == 12
    client.patch(f"/api/competitors/{alter.id}", json={"default_frequency_hours": 72})
    assert next_in_hours() == 72
    client.patch(f"/api/offers/{o.id}", json={"custom_frequency_hours": 1})
    assert next_in_hours() == 2.0  # 1 h after the last check is already past: due now (~2 h after the check)


def test_scheduling_settings_validation_and_permissions(client):
    admin(client)
    assert client.get("/api/settings/scheduling").json()["scheduler_enabled"] is True
    for bad in ({"window_start_hour": 1}, {"window_start_hour": 3, "window_end_hour": 3},
                {"priority_interval_hours": {"regular": 0}}, {"batch_size": 5000}):
        assert client.put("/api/settings/scheduling", json=bad).status_code == 422, bad
    client.post("/api/auth/logout")
    make_user("viewer@example.com", role="viewer")
    login(client, "viewer@example.com")
    assert client.get("/api/settings/scheduling").status_code == 200
    assert client.put("/api/settings/scheduling", json={}).status_code == 403


def test_monitoring_and_logs(client, db, site, worker):
    admin(client)
    alter = competitor(db)
    broken = offer(db, alter, 9999)
    offer(db, alter, 1111)
    db.commit()
    site["https://www.alter.cz/produkt/1111"] = ALTER_PAGE
    for _ in range(3):  # three failing runs in a row for the missing page
        db.execute(text("UPDATE competitor_products SET next_check_at = NULL"))
        db.commit()
        scheduler.tick(db)
        run_all(worker, db)
    data = client.get("/api/monitoring").json()
    assert data["scheduler_last_tick_at"] and data["pending_jobs"] == 0 and data["due_offers"] == 0
    assert data["checks_24h"] == 3 and data["failures_24h"] == 3 and data["price_changes_24h"] == 1
    assert [(p["id"], p["consecutive_failures"]) for p in data["problem_offers"]] == [(broken.id, 3)]
    assert data["problem_competitors"] == []  # runs were partial, not failed

    runs = client.get("/api/runs", params={"competitor_id": alter.id}).json()
    assert runs["total"] == 3 and {r["status"] for r in runs["items"]} == {"partial"}
    assert runs["items"][0]["competitor_name"] == "Alter"
    failed = client.get("/api/jobs", params={"status": "failed"}).json()
    assert failed["total"] == 3 and failed["items"][0]["url"].endswith("/produkt/9999")
    assert client.get("/api/jobs", params={"kind": "scheduled"}).json()["total"] == 6


def test_competitor_problem_after_failed_runs(client, db, site, worker):
    admin(client)
    alter = competitor(db)
    offer(db, alter, 9999)
    db.commit()
    for _ in range(3):
        db.execute(text("UPDATE competitor_products SET next_check_at = NULL"))
        db.commit()
        scheduler.tick(db)
        run_all(worker, db)
    problems = client.get("/api/monitoring").json()["problem_competitors"]
    assert [(p["name"], p["failed_runs_in_row"]) for p in problems] == [("Alter", 3)]


def test_many_jobs_in_one_tick(db):
    """Regression: enqueueing more than 5 jobs on one connection (prepared statement + partial index)."""
    alter = competitor(db)
    for i in range(15):
        offer(db, alter, i)
    db.commit()
    assert scheduler.tick(db, now=NOW).jobs == 15
    assert len([j for j in jobs(db) if j.kind == "scheduled"]) == 15
