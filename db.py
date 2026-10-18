import os
import sqlite3

from flask import g

# On Railway, attach a volume: its mount path is exposed as RAILWAY_VOLUME_MOUNT_PATH,
# so data survives redeploys. Without a volume the container's disk is wiped on each deploy.
DB_PATH = os.environ.get("COMPETITION_DB") or os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.dirname(os.path.abspath(__file__)), "competition.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS our_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sku TEXT,
    price REAL NOT NULL,
    currency TEXT DEFAULT 'EUR'
);

CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    urls TEXT NOT NULL,                -- one URL per line
    item_selector TEXT,                -- optional CSS selector of a product card
    name_selector TEXT,                -- optional CSS selector of name inside card
    price_selector TEXT,               -- optional CSS selector of price inside card
    last_scraped TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS competitor_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price REAL,
    currency TEXT,
    url TEXT,
    scraped_at TEXT
);

-- Manual overrides of the automatic matching.
-- our_product_id NULL means "never match this competitor product".
CREATE TABLE IF NOT EXISTS links (
    competitor_product_id INTEGER PRIMARY KEY REFERENCES competitor_products(id) ON DELETE CASCADE,
    our_product_id INTEGER REFERENCES our_products(id) ON DELETE CASCADE
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.close()
