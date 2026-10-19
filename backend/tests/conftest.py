import os

# Must be set before the app (settings, engine) is imported.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://localhost/competition_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.pop("RAILWAY_ENVIRONMENT", None)
os.environ["EMBEDDED_WORKER"] = "false"  # tests process jobs explicitly

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.cli import migrate  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password, login_limiter  # noqa: E402

PASSWORD = "spravne-heslo-123"


def truncate_all() -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session", autouse=True)
def database():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate()
    truncate_all()  # migrations seed data (default alert rules); every test starts from empty tables
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    truncate_all()
    login_limiter._failures.clear()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_user(email: str, role: str = "admin", active: bool = True) -> User:
    with SessionLocal() as s:
        user = User(email=email, password_hash=hash_password(PASSWORD), role=role, is_active=active)
        s.add(user)
        s.commit()
        return user


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Logs in and returns the CSRF token; also sets it as a default header."""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    return token
