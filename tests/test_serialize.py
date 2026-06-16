"""Tests for the shared catalog serialization module.

Covers the dump/restore round-trip (byte-for-byte across every snapshot table,
including NULLs, FK columns, JSON text, REAL columns, and 0/1 booleans) plus the
type-coercion path used by the future Google Sheets sync, where every cell
arrives as a string and the NULL-vs-empty distinction is lost.
"""

from __future__ import annotations

import sqlite3

from numobel import db
from numobel.sync import serialize


def _sample_db() -> sqlite3.Connection:
    """An in-memory catalog exercising every snapshot table and FK/NULL column."""
    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.executescript(
        """
        INSERT INTO brands(id, code, name, has_sheet) VALUES
            (1, 'AT', 'Acoustic Tech', 1),
            (2, 'NU', 'Numobel', 0);

        INSERT INTO color_groups(id, note, created_at) VALUES
            (10, 'reds', '2026-01-01T00:00:00');

        INSERT INTO products
            (id, brand_id, sku, shade_no, color_name, thickness, self_label,
             category, extra_json, image_path, source_sheet, source_row,
             color_group_id) VALUES
            (100, 1, 'AT05', '5', 'Crimson', 1.5, 'lbl', 'cat',
             '{"k": "v"}', NULL, 'AT', 3, 10),
            (101, 2, 'NU05', '5', 'Scarlet', NULL, NULL, NULL,
             NULL, NULL, '(synthesized)', NULL, 10);

        INSERT INTO color_links
            (id, from_product_id, to_product_id, to_brand_code, raw_ref,
             normalized, status, source, note, created_at) VALUES
            (200, 100, NULL, 'PCP', 'PCP12', 'PCP12', 'external', 'import',
             NULL, '2026-01-01T00:00:00'),
            (201, 100, NULL, 'AT', 'AT99', 'AT99', 'unresolved', 'user',
             'manual', '2026-01-02T00:00:00');

        INSERT INTO prices
            (id, seller, mrp, mrp_sft, dp, dp_sft, profit, discount,
             cust_price, cust_price_sft) VALUES
            (300, 'ACME', 100.0, 10.0, 80.0, 8.0, 20.0, 0.2, 90.0, 9.0);
        """
    )
    conn.commit()
    return conn


def _dump(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """All rows of ``table`` as plain tuples, ordered by primary key."""
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_dump_table_shape():
    src = _sample_db()
    data = serialize.dump_table(src, "products")

    assert set(data) == {"columns", "rows"}
    # Columns match the schema declaration order.
    schema_cols = [r[1] for r in src.execute("PRAGMA table_info(products)")]
    assert data["columns"] == schema_cols
    # Rows are plain lists in column order.
    assert all(isinstance(r, list) for r in data["rows"])
    assert len(data["rows"]) == 2


def test_dump_rows_covers_all_snapshot_tables():
    src = _sample_db()
    data = serialize.dump_rows(src)
    assert tuple(data) == serialize.SNAPSHOT_TABLES
    assert serialize.RESTORE_ORDER == serialize.SNAPSHOT_TABLES


def test_roundtrip_preserves_all_tables():
    src = _sample_db()
    data = serialize.dump_rows(src)

    dest = db.connect(":memory:")
    db.create_schema(dest)
    db.reset_catalog(dest)
    counts = serialize.restore_rows(dest, data)
    dest.commit()

    assert counts == {
        "brands": 2,
        "color_groups": 1,
        "products": 2,
        "color_links": 2,
        "prices": 1,
    }

    for table in serialize.RESTORE_ORDER:
        assert _dump(src, table) == _dump(dest, table), f"mismatch in {table}"

    # FK column survives verbatim.
    rows = dest.execute(
        "SELECT id, color_group_id FROM products ORDER BY id"
    ).fetchall()
    assert [(r["id"], r["color_group_id"]) for r in rows] == [(100, 10), (101, 10)]

    # extra_json round-trips byte-for-byte, thickness stays REAL, has_sheet 0/1.
    p = dict(src.execute("SELECT * FROM products WHERE id=100").fetchone())
    q = dict(dest.execute("SELECT * FROM products WHERE id=100").fetchone())
    assert p["extra_json"] == q["extra_json"] == '{"k": "v"}'
    assert q["thickness"] == 1.5 and isinstance(q["thickness"], float)
    has = dict(dest.execute("SELECT id, has_sheet FROM brands").fetchall())
    assert has == {1: 1, 2: 0}


def test_restore_table_returns_count():
    dest = db.connect(":memory:")
    db.create_schema(dest)
    n = serialize.restore_table(
        dest, "brands", ["id", "code", "name", "has_sheet"],
        [[1, "AT", "Acoustic Tech", 1], [2, "NU", "Numobel", 0]],
    )
    dest.commit()
    assert n == 2


def test_restore_table_coerces_stringified_inputs():
    """Simulate the Google Sheets path: every cell arrives as text."""
    dest = db.connect(":memory:")
    db.create_schema(dest)

    # brand needed for the products FK.
    serialize.restore_table(
        dest, "brands", ["id", "code", "has_sheet"], [["1", "AT", "1"]]
    )

    cols = [
        "id", "brand_id", "sku", "color_name", "thickness", "extra_json",
        "source_row", "color_group_id",
    ]
    # thickness="1.5" -> float; source_row="" -> NULL; extra_json JSON passes
    # through unchanged; id/brand_id ints parse from text.
    rows = [["100", "1", "AT05", "Crimson", "1.5", '{"k": "v"}', "", ""]]
    serialize.restore_table(dest, "products", cols, rows)
    dest.commit()

    r = dict(dest.execute("SELECT * FROM products WHERE id=100").fetchone())
    assert r["id"] == 100 and isinstance(r["id"], int)
    assert r["brand_id"] == 1 and isinstance(r["brand_id"], int)
    assert r["thickness"] == 1.5 and isinstance(r["thickness"], float)
    assert r["source_row"] is None  # "" -> NULL
    assert r["color_group_id"] is None  # "" -> NULL
    assert r["extra_json"] == '{"k": "v"}'  # text passes through unchanged

    # has_sheet "1" coerced to int 1, not the string "1".
    b = dict(dest.execute("SELECT has_sheet FROM brands WHERE id=1").fetchone())
    assert b["has_sheet"] == 1 and isinstance(b["has_sheet"], int)


def test_restore_table_integral_float_to_int():
    """An INTEGER column fed a float like 5.0 stores int 5, not 5.0."""
    dest = db.connect(":memory:")
    db.create_schema(dest)
    serialize.restore_table(dest, "brands", ["id", "code", "has_sheet"],
                            [[5.0, "AT", 1]])
    # The numeric string "5.0" is integral too and must narrow to int.
    serialize.restore_table(dest, "brands", ["id", "code", "has_sheet"],
                            [[6, "NU", "5.0"]])
    dest.commit()
    val = dest.execute("SELECT id FROM brands WHERE code='AT'").fetchone()[0]
    assert val == 5 and isinstance(val, int)
    has = dest.execute("SELECT has_sheet FROM brands WHERE code='NU'").fetchone()[0]
    assert has == 5 and isinstance(has, int)


def test_restore_table_non_integral_not_truncated():
    """A non-integral value into an INTEGER column must not be truncated.

    Matches the prior xlsx restore behavior where SQLite's native affinity kept
    a non-integral value as REAL instead of silently dropping the fraction.
    """
    dest = db.connect(":memory:")
    db.create_schema(dest)
    # has_sheet has INTEGER affinity but is not the rowid PK, so SQLite stores
    # a non-integral value verbatim instead of rejecting it. Feed both a Python
    # float and a numeric string carrying a fraction.
    serialize.restore_table(dest, "brands", ["id", "code", "has_sheet"],
                            [[1, "AT", 5.5]])
    serialize.restore_table(dest, "brands", ["id", "code", "has_sheet"],
                            [[2, "NU", "5.5"]])
    dest.commit()

    float_val = dest.execute("SELECT has_sheet FROM brands WHERE id=1").fetchone()[0]
    str_val = dest.execute("SELECT has_sheet FROM brands WHERE id=2").fetchone()[0]
    assert float_val == 5.5 and float_val != 5
    assert str_val == 5.5 and str_val != 5


def test_restore_rows_skips_missing_tables():
    dest = db.connect(":memory:")
    db.create_schema(dest)
    counts = serialize.restore_rows(
        dest, {"brands": {"columns": ["id", "code"], "rows": [[1, "AT"]]}}
    )
    dest.commit()
    assert counts == {"brands": 1}
