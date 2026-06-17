"""Tests for the snapshot export / restore round-trip.

Covers: lossless table round-trip (including foreign-key columns), embedded
photo round-trip, format auto-detection, and the import_workbook dispatcher.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from numobel import db
from numobel.exporter.run_export import export
from numobel.importer import snapshot

EXCEL_PATH = str(
    Path(__file__).resolve().parent.parent
    / "my_excel"
    / "NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx"
)


def _sample_db() -> sqlite3.Connection:
    """An in-memory catalog exercising every exported table and FK column."""
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

        INSERT INTO audit_log(id, ts, action, entity, entity_id, details) VALUES
            (400, '2026-01-01T00:00:00', 'create', 'product', 100, '{"k": "v"}'),
            (401, '2026-01-02T00:00:00', 'update', 'price', 300, NULL);
        """
    )
    conn.commit()
    return conn


def _dump(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """All rows of ``table`` as plain tuples, ordered by primary key."""
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_roundtrip_preserves_all_tables(tmp_path):
    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    summary = export(excel_path=out, conn=src)

    assert summary["total_products"] == 2
    assert summary["prices"] == 1
    assert summary["links"] == 2
    assert summary["audit_log"] == 2

    dest = db.connect(":memory:")
    snapshot.restore(out, dest)

    # EXPORT_RESTORE_ORDER includes audit_log, so this also asserts the audit
    # log round-trips verbatim alongside the catalog tables.
    for table in snapshot.EXPORT_RESTORE_ORDER:
        assert _dump(src, table) == _dump(dest, table), f"mismatch in {table}"

    # FK columns survive verbatim (color_group_id ties the two products).
    rows = dest.execute(
        "SELECT id, color_group_id FROM products ORDER BY id"
    ).fetchall()
    assert [(r["id"], r["color_group_id"]) for r in rows] == [(100, 10), (101, 10)]


def test_restore_summary_shape(tmp_path):
    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    export(excel_path=out, conn=src)

    dest = db.connect(":memory:")
    summary = snapshot.restore(out, dest)
    assert summary["total_products"] == 2
    assert summary["prices"] == 1
    assert summary["links"] == {"resolved": 0, "unresolved": 1, "external": 1}
    assert summary["audit_log"] == 2


def test_import_replaces_local_audit_log(tmp_path):
    """Restoring a snapshot replaces local audit history (no append/duplicate)."""
    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    export(excel_path=out, conn=src)

    dest = db.connect(":memory:")
    db.create_schema(dest)
    # Pre-existing local history that must NOT survive the import.
    dest.execute(
        "INSERT INTO audit_log(id, ts, action, entity, entity_id, details) "
        "VALUES (999, '2025-01-01T00:00:00', 'create', 'product', 1, NULL)"
    )
    dest.commit()

    snapshot.restore(out, dest)

    assert _dump(src, "audit_log") == _dump(dest, "audit_log")
    ids = [r[0] for r in dest.execute("SELECT id FROM audit_log ORDER BY id")]
    assert ids == [400, 401]  # the stale local row (999) is gone, no dupes


def test_import_old_snapshot_without_audit_preserves_local(tmp_path):
    """An older snapshot lacking an audit_log sheet leaves local history intact."""
    from openpyxl import load_workbook

    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    export(excel_path=out, conn=src)

    # Simulate a pre-feature snapshot by dropping the audit_log sheet.
    wb = load_workbook(out)
    del wb["audit_log"]
    wb.save(out)

    dest = db.connect(":memory:")
    db.create_schema(dest)
    dest.execute(
        "INSERT INTO audit_log(id, ts, action, entity, entity_id, details) "
        "VALUES (999, '2025-01-01T00:00:00', 'create', 'product', 1, NULL)"
    )
    dest.commit()

    summary = snapshot.restore(out, dest)

    ids = [r[0] for r in dest.execute("SELECT id FROM audit_log ORDER BY id")]
    assert ids == [999]  # local history untouched
    assert summary["audit_log"] == 0


def test_photo_embedding_roundtrip(tmp_path, monkeypatch):
    # Isolate base_dir/images_dir so we never touch the real images/ folder.
    src_root = tmp_path / "src"
    (src_root / "images").mkdir(parents=True)
    photo_bytes = b"\x89PNG\r\n\x1a\n-not-a-real-png-but-bytes-round-trip"
    (src_root / "images" / "100_pic.png").write_bytes(photo_bytes)

    monkeypatch.setattr(db, "base_dir", lambda: src_root)
    monkeypatch.setattr(db, "images_dir", lambda: src_root / "images")

    src = _sample_db()
    src.execute(
        "UPDATE products SET image_path = 'images/100_pic.png' WHERE id = 100"
    )
    # Product 101 references a photo we will NOT embed (file missing).
    src.execute(
        "UPDATE products SET image_path = 'images/missing.png' WHERE id = 101"
    )
    src.commit()

    out = str(tmp_path / "snap.xlsx")
    summary = export(excel_path=out, conn=src)
    assert summary["images"] == 1

    # Restore into a fresh, separate images directory.
    dest_root = tmp_path / "dest"
    monkeypatch.setattr(db, "images_dir", lambda: dest_root / "images")
    dest = db.connect(":memory:")
    snapshot.restore(out, dest)

    restored = dest_root / "images" / "100_pic.png"
    assert restored.read_bytes() == photo_bytes

    paths = dict(dest.execute("SELECT id, image_path FROM products").fetchall())
    assert paths[100] == "images/100_pic.png"
    assert paths[101] is None  # broken reference cleared


def test_is_snapshot_detection(tmp_path):
    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    export(excel_path=out, conn=src)

    assert snapshot.is_snapshot(out) is True
    assert snapshot.is_snapshot(EXCEL_PATH) is False


def test_import_workbook_routes_snapshot(tmp_path):
    src = _sample_db()
    out = str(tmp_path / "snap.xlsx")
    export(excel_path=out, conn=src)

    dest = db.connect(":memory:")
    summary = snapshot.import_workbook(excel_path=out, conn=dest)
    assert summary["total_products"] == 2
    assert _dump(src, "products") == _dump(dest, "products")


def test_import_workbook_routes_master_workbook(tmp_path):
    db_path = str(tmp_path / "numobel.db")
    conn = db.connect(db_path)
    summary = snapshot.import_workbook(excel_path=EXCEL_PATH, conn=conn)
    # build() returns per-sheet counts; a real catalog has many products.
    assert summary["total_products"] > 0
    assert "products_by_sheet" in summary
