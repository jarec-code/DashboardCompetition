from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models import (
    Alert,
    AlertRule,
    Competitor,
    CompetitorProduct,
    ImportBatch,
    PriceHistory,
    ScrapeConfig,
    ScrapeJob,
    ScrapeRun,
)
from app.services import housekeeping

NOW = datetime.now(UTC)


def test_cleanup_removes_only_old_diagnostics(db):
    c = Competitor(name="Alter", base_url="https://www.alter.cz/", domain="alter.cz", scrape_config=ScrapeConfig())
    db.add(c)
    db.flush()
    o = CompetitorProduct(competitor_id=c.id, product_url="https://www.alter.cz/produkt/1")
    rule = AlertRule(rule_type="stale", params={})
    db.add_all([o, rule])
    db.flush()
    old, recent = NOW - timedelta(days=40), NOW - timedelta(days=2)
    job = dict(competitor_id=c.id, kind="manual", priority=20, attempt_count=1)
    db.add_all([
        ScrapeJob(**job, status="succeeded", finished_at=old),            # removed
        ScrapeJob(**job, status="failed", finished_at=recent),            # kept (recent)
        ScrapeJob(competitor_id=c.id, kind="test", priority=10, attempt_count=0, status="pending"),  # kept (open)
        ScrapeRun(competitor_id=c.id, status="succeeded", started_at=old),  # runs are kept
        PriceHistory(competitor_product_id=o.id, price=Decimal("1"), currency="CZK", availability="in_stock",
                     vat_mode="unknown", observed_at=old - timedelta(days=400), change_types=["initial"]),
        ImportBatch(kind="products", status="committed", file_name="a.csv", file_format="csv", rows=[], stats={},
                    created_at=old),                                       # removed
        ImportBatch(kind="products", status="preview", file_name="b.csv", file_format="csv", rows=[], stats={},
                    created_at=NOW - timedelta(days=3)),                   # abandoned preview: removed
        ImportBatch(kind="products", status="preview", file_name="c.csv", file_format="csv", rows=[], stats={},
                    created_at=NOW),                                       # kept
        Alert(rule_id=rule.id, competitor_product_id=o.id, alert_type="stale", status="resolved", message="x",
              resolved_at=NOW - timedelta(days=200)),                      # removed
        Alert(rule_id=rule.id, competitor_product_id=o.id, alert_type="stale", status="open", message="x",
              created_at=NOW - timedelta(days=300)),                       # kept (still open)
    ])
    db.commit()
    assert housekeeping.cleanup(db) == {"jobs": 1, "imports": 2, "alerts": 1}
    db.commit()
    counts = [db.scalar(select(func.count()).select_from(m))
              for m in (ScrapeJob, ScrapeRun, PriceHistory, ImportBatch, Alert)]
    assert counts == [2, 1, 1, 1, 1]
