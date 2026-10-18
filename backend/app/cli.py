"""Command line tasks.

  python -m app.cli migrate        apply database migrations
  python -m app.cli create-admin   create an administrator (asks for the password)
  python -m app.cli ensure-admin   create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD if none exists
  python -m app.cli predeploy      migrate + ensure-admin (run by the container before the web server starts)
"""
import argparse
import getpass
import sys

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select, text

from app.config import BACKEND_DIR, get_settings
from app.db import SessionLocal, engine
from app.models import User
from app.schemas.users import UserCreate
from app.security import hash_password

MIGRATION_LOCK_ID = 727_001  # pg advisory lock: only one process migrates at a time


def migrate() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            conn.commit()


def create_admin(email: str, password: str) -> str:
    try:
        data = UserCreate(email=email, password=password, role="admin")
    except ValidationError as exc:
        raise SystemExit(f"Neplatné údaje: {exc.errors()[0]['msg']}") from None
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == data.email)):
            raise SystemExit(f"Používateľ {data.email} už existuje.")
        db.add(User(email=data.email, password_hash=hash_password(data.password), role="admin"))
        db.commit()
    return data.email


def ensure_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == "admin").limit(1)):
            print("Administrátor už existuje.")
            return
    settings = get_settings()
    if not (settings.admin_email and settings.admin_password):
        print("Žiadny administrátor neexistuje: nastavte ADMIN_EMAIL a ADMIN_PASSWORD.", file=sys.stderr)
        return
    print(f"Vytvorený administrátor {create_admin(settings.admin_email, settings.admin_password)}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    sub.add_parser("ensure-admin")
    sub.add_parser("predeploy")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
    elif args.cmd == "create-admin":
        print(f"Vytvorený administrátor {create_admin(args.email, getpass.getpass('Heslo: '))}.")
    elif args.cmd == "ensure-admin":
        ensure_admin()
    elif args.cmd == "predeploy":
        migrate()
        ensure_admin()


if __name__ == "__main__":
    main()
