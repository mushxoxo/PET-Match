"""Tests for the color-group schema migration (numobel.db.migrate).

The migration folds the old per-edge ``resolved`` color_links into transitive
color groups, so that every member of a similarity family shares one
``color_group_id`` and unresolved/external references are left intact.
"""

from __future__ import annotations

import sqlite3

from numobel import db


def _legacy_db() -> sqlite3.Connection:
    """A connection whose products table predates the color_group_id column.

    Built by hand (no color_groups table, no color_group_id) so the test
    exercises the ALTER-TABLE path that real numobel.db files take.
    """
    conn = db.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL, name TEXT, has_sheet INTEGER DEFAULT 0
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL REFERENCES brands(id),
            sku TEXT, shade_no TEXT, color_name TEXT, thickness REAL,
            self_label TEXT, category TEXT, extra_json TEXT, image_path TEXT,
            source_sheet TEXT, source_row INTEGER
        );
        CREATE TABLE color_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_product_id INTEGER NOT NULL, to_product_id INTEGER,
            to_brand_code TEXT, raw_ref TEXT, normalized TEXT,
            status TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'import',
            note TEXT, created_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO brands(id, code) VALUES (1, 'A')")
    # Five products: 10-11-12 form a chain; 13 links only to 12; 99 is alone.
    for pid in (10, 11, 12, 13, 99):
        conn.execute(
            "INSERT INTO products(id, brand_id, sku) VALUES (?, 1, ?)",
            (pid, f"A{pid}"),
        )
    # Resolved chain 10->11->12 and a separate edge 13->12, in BOTH directions
    # for some pairs (the duplicate-link defect the import left behind).
    edges = [(10, 11), (11, 10), (11, 12), (13, 12)]
    for a, b in edges:
        conn.execute(
            "INSERT INTO color_links(from_product_id, to_product_id, status) "
            "VALUES (?, ?, 'resolved')",
            (a, b),
        )
    # An unresolved + an external reference that must survive migration.
    conn.execute(
        "INSERT INTO color_links(from_product_id, raw_ref, status) "
        "VALUES (10, 'XYZ9', 'unresolved')"
    )
    conn.execute(
        "INSERT INTO color_links(from_product_id, to_brand_code, raw_ref, status) "
        "VALUES (10, 'PCP', 'PCP27', 'external')"
    )
    conn.commit()
    return conn


def _group(conn, pid):
    return conn.execute(
        "SELECT color_group_id FROM products WHERE id=?", (pid,)
    ).fetchone()[0]


def test_migration_groups_transitive_family():
    conn = _legacy_db()
    db.migrate(conn)

    # 10, 11, 12, 13 are all transitively similar -> one shared group.
    groups = {_group(conn, pid) for pid in (10, 11, 12, 13)}
    assert len(groups) == 1
    assert None not in groups

    # The lonely product keeps no group.
    assert _group(conn, 99) is None


def test_migration_drops_resolved_links_keeps_pending():
    conn = _legacy_db()
    db.migrate(conn)

    statuses = [
        r["status"] for r in conn.execute("SELECT status FROM color_links")
    ]
    assert "resolved" not in statuses
    assert sorted(statuses) == ["external", "unresolved"]


def test_migration_is_idempotent():
    conn = _legacy_db()
    db.migrate(conn)
    group_before = _group(conn, 10)
    n_groups_before = conn.execute(
        "SELECT COUNT(*) FROM color_groups"
    ).fetchone()[0]

    db.migrate(conn)  # second run must change nothing

    assert _group(conn, 10) == group_before
    assert (
        conn.execute("SELECT COUNT(*) FROM color_groups").fetchone()[0]
        == n_groups_before
    )
