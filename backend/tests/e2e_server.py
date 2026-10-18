"""Server for browser (Playwright) tests: fresh e2e database, two users, built frontend, uvicorn,
embedded worker (scheduler tick every 2 s, initially disabled), and a local fake shop for scraping tests.
Only *.e2e.test hosts can be fetched, so no test ever loads a real website.

  python -m tests.e2e_server     (run from backend/; uses E2E_DATABASE_URL)

The fake shop answers for hosts ending in ".e2e.test" (see SCRAPER_TEST_ORIGIN): it serves saved
alter.cz pages from tests/fixtures/html/alter_cz, and GET /__set_price?id=1111&value=39,00 changes
the price it serves, so a test can observe a price change.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("E2E_PORT", "8010"))
SHOP_PORT = PORT + 1
os.environ["DATABASE_URL"] = os.environ.get("E2E_DATABASE_URL", "postgresql+psycopg://localhost/competition_e2e")
os.environ.pop("RAILWAY_ENVIRONMENT", None)
os.environ["EMBEDDED_WORKER"] = "true"
os.environ["WORKER_POLL_SECONDS"] = "0.3"
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "2"
os.environ["SCRAPER_TEST_ORIGIN"] = f"http://127.0.0.1:{SHOP_PORT}"

import uvicorn  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.cli import migrate  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import scheduling  # noqa: E402

USERS = [("admin@e2e.test", "admin"), ("viewer@e2e.test", "viewer")]
PASSWORD = "e2e-heslo-12345"
PAGES = Path(__file__).parent / "fixtures" / "html" / "alter_cz"
PRICE_OVERRIDES: dict[str, str] = {}


class FakeShop(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/robots.txt":
            return self._send(200, "User-agent: *\nDisallow: /kosik/\n", "text/plain")
        if url.path == "/__set_price":
            params = parse_qs(url.query)
            PRICE_OVERRIDES[params["id"][0]] = params["value"][0]
            return self._send(200, "ok", "text/plain")
        if url.path.startswith("/produkt/"):
            product_id = url.path.rsplit("/", 1)[-1]
            page = PAGES / f"produkt_{product_id}.html"
            if page.is_file():
                html = page.read_text(encoding="utf-8")
                if product_id in PRICE_OVERRIDES:  # the first price block holds the printed variant's price
                    html = html.replace('<div class="vatPricePD">44,00',
                                        f'<div class="vatPricePD">{PRICE_OVERRIDES[product_id]}', 1)
                return self._send(200, html)
        return self._send(404, "<h1>Stránka nebyla nalezena</h1>")


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate()
    with SessionLocal() as db:
        db.add_all(User(email=e, password_hash=hash_password(PASSWORD), role=r) for e, r in USERS)
        # Automatic checks start disabled so tests do not interfere; the automation test enables them.
        scheduling.save_settings(db, scheduling.SchedulingSettings(scheduler_enabled=False))
        db.commit()
    shop = ThreadingHTTPServer(("127.0.0.1", SHOP_PORT), FakeShop)
    threading.Thread(target=shop.serve_forever, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
