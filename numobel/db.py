"""SQLite database layer for NUMOBEL.

Uses only the stdlib ``sqlite3`` module. Provides connection management,
schema creation, catalog reset, and small settings helpers.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def base_dir() -> Path:
    """Return the directory that holds ``numobel.db`` and ``images/``.

    When running from source this is the repo root (parent of the ``numobel``
    package). When frozen by PyInstaller, ``__file__`` lives inside a temporary
    unpack dir, so we anchor to the executable's own directory instead — that
    way the database and photos sit next to the ``.exe`` and persist across
    runs.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def images_dir() -> Path:
    """Return the directory where attached photos are stored."""
    return base_dir() / "images"


# Where ``numobel.db`` lives (source layout: repo root).
DEFAULT_DB_PATH = str(base_dir() / "numobel.db")


def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection, enable Row factory and foreign keys, and return it."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL + busy_timeout let the UI thread and the sync worker thread safely
    # touch the same file: WAL permits concurrent readers alongside a single
    # writer, and a 5s busy_timeout makes a contended write wait-and-retry
    # instead of failing immediately with "database is locked". Both are
    # idempotent and safe for ``:memory:`` (journal_mode just stays "memory").
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
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

        CREATE TABLE IF NOT EXISTS color_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT,
            created_at TEXT
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
            source_row INTEGER,
            color_group_id INTEGER REFERENCES color_groups(id)
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
            details TEXT,
            uuid TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    # The color_group_id index can only exist once the column does. Fresh DBs
    # have it from the products CREATE above; legacy DBs gain it in migrate().
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if "color_group_id" in cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_color_group "
            "ON products(color_group_id)"
        )
    # Unique index on the audit uuid serves as the cross-device merge key.
    # SQLite treats NULLs as distinct, so this coexists with not-yet-backfilled
    # NULL rows. Fresh DBs have the column from the CREATE above; legacy DBs
    # (table already exists without uuid) gain both column and index in migrate().
    acols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "uuid" in acols:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uidx_audit_log_uuid "
            "ON audit_log(uuid)"
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
    conn.execute("DELETE FROM color_groups")
    conn.execute("DELETE FROM brands")
    conn.execute("DELETE FROM prices")
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema (idempotent).

    Adds the ``color_groups`` table and ``products.color_group_id`` column when
    missing, adds and backfills the ``audit_log.uuid`` column (the cross-device
    merge key) resumably, then folds any ``resolved`` color_links into color
    groups so that "similar colors" behave as transitive equivalence classes
    rather than a sparse set of one-hop edges. Safe to call on every startup.
    """
    create_schema(conn)  # ensures color_groups table + indexes exist

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if "color_group_id" not in cols:
        conn.execute(
            "ALTER TABLE products "
            "ADD COLUMN color_group_id INTEGER REFERENCES color_groups(id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_color_group "
            "ON products(color_group_id)"
        )

    acols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "uuid" not in acols:
        conn.execute("ALTER TABLE audit_log ADD COLUMN uuid TEXT")
    # Backfill + unique index run UNCONDITIONALLY: the column is now guaranteed
    # to exist (pre-existing or just added). ``ALTER TABLE ADD COLUMN``
    # auto-commits in SQLite, so a crash between the ADD and the backfill would
    # otherwise leave rows NULL forever (the guard above would skip them on the
    # next run). The ``WHERE uuid IS NULL`` set-based UPDATE is idempotent and
    # resumable — a no-op once every row is filled. ``lower(hex(randomblob(16)))``
    # yields a 32-char lowercase hex id matching ``uuid.uuid4().hex``.
    conn.execute(
        "UPDATE audit_log SET uuid = lower(hex(randomblob(16))) WHERE uuid IS NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uidx_audit_log_uuid "
        "ON audit_log(uuid)"
    )

    _fold_resolved_links_into_groups(conn)
    conn.commit()


def _fold_resolved_links_into_groups(conn: sqlite3.Connection) -> None:
    """Convert ``resolved`` color_links into color groups, then drop them.

    Computes the connected components of the resolved-link graph (treated as
    undirected) and assigns every product in a component a shared
    ``color_group_id``. Reuses an existing group id when any member already has
    one, merging groups as needed so the operation is idempotent and safe to
    re-run after a re-import. Unresolved/external links are left untouched.
    """
    resolved = conn.execute(
        "SELECT from_product_id, to_product_id FROM color_links "
        "WHERE status = 'resolved' AND to_product_id IS NOT NULL"
    ).fetchall()
    if not resolved:
        return

    # Union-find over the products touched by resolved links.
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for link in resolved:
        a, b = link["from_product_id"], link["to_product_id"]
        if a != b:
            union(a, b)

    components: dict[int, list[int]] = {}
    for node in list(parent):
        components.setdefault(find(node), []).append(node)

    now = datetime.now().isoformat(timespec="seconds")
    for members in components.values():
        if len(members) < 2:
            continue
        placeholders = ",".join("?" * len(members))
        existing = [
            row["color_group_id"]
            for row in conn.execute(
                f"SELECT DISTINCT color_group_id FROM products "
                f"WHERE id IN ({placeholders}) AND color_group_id IS NOT NULL",
                members,
            )
        ]
        if existing:
            gid = existing[0]
        else:
            gid = int(
                conn.execute(
                    "INSERT INTO color_groups(created_at) VALUES(?)", (now,)
                ).lastrowid
            )
        conn.execute(
            f"UPDATE products SET color_group_id = ? WHERE id IN ({placeholders})",
            [gid, *members],
        )
        # Merge any other pre-existing groups for these products into gid.
        for old in existing[1:]:
            if old != gid:
                conn.execute(
                    "UPDATE products SET color_group_id = ? "
                    "WHERE color_group_id = ?",
                    (gid, old),
                )
                conn.execute("DELETE FROM color_groups WHERE id = ?", (old,))

    conn.execute(
        "DELETE FROM color_links "
        "WHERE status = 'resolved' AND to_product_id IS NOT NULL"
    )


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
