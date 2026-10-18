"""Server for browser (Playwright) tests: fresh e2e database, two users, built frontend, uvicorn.

  python -m tests.e2e_server     (run from backend/; uses E2E_DATABASE_URL)
"""
import os

os.environ["DATABASE_URL"] = os.environ.get("E2E_DATABASE_URL", "postgresql+psycopg://localhost/competition_e2e")
os.environ.pop("RAILWAY_ENVIRONMENT", None)

import uvicorn  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.cli import migrate  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

USERS = [("admin@e2e.test", "admin"), ("viewer@e2e.test", "viewer")]
PASSWORD = "e2e-heslo-12345"
PORT = int(os.environ.get("E2E_PORT", "8010"))


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate()
    with SessionLocal() as db:
        db.add_all(User(email=e, password_hash=hash_password(PASSWORD), role=r) for e, r in USERS)
        db.commit()
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
