"""SQLite database layer for NUMOBEL.

Uses only the stdlib ``sqlite3`` module. Provides connection management,
schema creation, catalog reset, and small settings helpers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Repo root is the parent of the ``numobel`` package directory.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "numobel.db")


def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection, enable Row factory and foreign keys, and return it."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes IF NOT EXISTS."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT,
            has_sheet INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL REFERENCES brands(id),
            sku TEXT,
            shade_no TEXT,
            color_name TEXT,
            thickness REAL,
            self_label TEXT,
            category TEXT,
            extra_json TEXT,
            image_path TEXT,
            source_sheet TEXT,
            source_row INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_products_brand_id
            ON products(brand_id);
        CREATE INDEX IF NOT EXISTS idx_products_color_name
            ON products(color_name);
        CREATE UNIQUE INDEX IF NOT EXISTS uidx_products_brand_sku
            ON products(brand_id, sku);

        CREATE TABLE IF NOT EXISTS color_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_product_id INTEGER NOT NULL REFERENCES products(id),
            to_product_id INTEGER REFERENCES products(id),
            to_brand_code TEXT,
            raw_ref TEXT,
            normalized TEXT,
            status TEXT NOT NULL CHECK(status IN ('resolved','unresolved','external')),
            source TEXT NOT NULL DEFAULT 'import' CHECK(source IN ('import','user')),
            note TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_color_links_from
            ON color_links(from_product_id);
        CREATE INDEX IF NOT EXISTS idx_color_links_to
            ON color_links(to_product_id);

        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller TEXT,
            mrp REAL,
            mrp_sft REAL,
            dp REAL,
            dp_sft REAL,
            profit REAL,
            discount REAL,
            cust_price REAL,
            cust_price_sft REAL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            action TEXT,
            entity TEXT,
            entity_id INTEGER,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def reset_catalog(conn: sqlite3.Connection) -> None:
    """Delete catalog data to support re-import.

    SIMPLE (MVP) version: deletes from color_links, products, brands, and
    prices in FK-safe order. This discards user-authored color_links too.

    NOTE: Full user-data-preserving reimport (keeping color_links with
    source='user', audit_log, settings, prices) is post-MVP. Preserving
    user color_links across a reimport is complex because they reference
    products that get deleted; deferred until after MVP.
    """
    conn.execute("DELETE FROM color_links")
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM brands")
    conn.execute("DELETE FROM prices")
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    """Return the value for ``key`` from settings, or ``default`` if absent."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    """Insert or update a settings key/value pair."""
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
