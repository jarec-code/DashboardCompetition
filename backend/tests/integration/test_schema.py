import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import BACKEND_DIR
from app.db import engine

EXPECTED_TABLES = {
    "users", "competitors", "scrape_configs", "products", "product_prices", "competitor_products",
    "current_prices", "price_history", "scrape_jobs", "scrape_runs", "app_settings",
}


def test_migration_downgrade_and_upgrade():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.downgrade(cfg, "base")
    assert not EXPECTED_TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def _offer(conn) -> tuple[int, int]:
    competitor_id = conn.execute(text(
        "INSERT INTO competitors (name, base_url, domain, competitor_type, preferred_source, status) "
        "VALUES ('Alter', 'https://www.alter.cz/', 'alter.cz', 'direct', 'html', 'active') RETURNING id")).scalar()
    offer_id = conn.execute(text(
        "INSERT INTO competitor_products (competitor_id, product_url, match_status, status) "
        "VALUES (:c, 'https://www.alter.cz/produkt/1111', 'unmatched', 'active') RETURNING id"),
        {"c": competitor_id}).scalar()
    return competitor_id, offer_id


def _job(conn, competitor_id, offer_id, status):
    conn.execute(text(
        "INSERT INTO scrape_jobs (competitor_id, competitor_product_id, kind, priority, status, attempt_count) "
        "VALUES (:c, :o, 'scheduled', 100, :s, 0)"), {"c": competitor_id, "o": offer_id, "s": status})


def test_only_one_open_job_per_offer():
    with engine.begin() as conn:
        competitor_id, offer_id = _offer(conn)
        _job(conn, competitor_id, offer_id, "succeeded")
        _job(conn, competitor_id, offer_id, "failed")
        _job(conn, competitor_id, offer_id, "pending")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _job(conn, competitor_id, offer_id, "running")


def test_check_constraints_reject_unknown_values():
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO competitors (name, base_url, domain, competitor_type, preferred_source, status) "
                          "VALUES ('X', 'https://x.cz', 'x.cz', 'direct', 'html', 'deleted')"))


def test_price_history_unique_per_offer_and_time():
    with engine.begin() as conn:
        _, offer_id = _offer(conn)
        insert = text("INSERT INTO price_history (competitor_product_id, price, currency, availability, vat_mode, "
                      "observed_at, change_types) VALUES (:o, 44.00, 'CZK', 'in_stock', 'unknown', "
                      "'2026-10-18T10:00:00Z', ARRAY['initial'])")
        conn.execute(insert, {"o": offer_id})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"o": offer_id})
