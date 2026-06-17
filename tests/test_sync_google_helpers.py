"""Offline unit tests for the pure GoogleBackend helpers + auth config.

These deliberately exercise ONLY the module-level helpers (chunking / parsing /
round-trips), which import no ``google`` library — importing this test module
must never trigger a ``googleapiclient`` import.
"""

from __future__ import annotations

import sqlite3

import pytest

from numobel import db
from numobel.sync import google_backend as gb
from numobel.sync import serialize


def _sample_db() -> sqlite3.Connection:
    """A small in-memory catalog exercising every snapshot table."""
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
             NULL, '2026-01-01T00:00:00');
        INSERT INTO prices
            (id, seller, mrp, mrp_sft, dp, dp_sft, profit, discount,
             cust_price, cust_price_sft) VALUES
            (300, 'ACME', 100.0, 10.0, 80.0, 8.0, 20.0, 0.2, 90.0, 9.0);
        """
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# data blob round-trip
# --------------------------------------------------------------------------- #
def test_data_blob_roundtrips_real_dump():
    conn = _sample_db()
    data = serialize.dump_rows(conn)

    rows = gb.encode_data_blob(data)
    assert rows[0] == ["seq", "chunk"]  # header first
    assert gb.decode_data_blob(rows) == data


def test_data_blob_chunks_large_payload_losslessly():
    # A payload comfortably larger than _CHUNK forces multiple chunk rows.
    big = {"x": ["y" * 5000 for _ in range(50)]}  # ~250k chars of JSON
    rows = gb.encode_data_blob(big)

    chunk_rows = rows[1:]
    assert len(chunk_rows) > 1  # genuinely split across cells
    # Every chunk stays under the cell cap.
    assert all(len(str(r[1])) <= gb._CHUNK for r in chunk_rows)
    # seq values are 0..n-1 in order.
    assert [r[0] for r in chunk_rows] == list(range(len(chunk_rows)))
    # Lossless rejoin (even when rows arrive shuffled).
    assert gb.decode_data_blob(rows) == big
    shuffled = [rows[0], *reversed(chunk_rows)]
    assert gb.decode_data_blob(shuffled) == big


def test_decode_empty_or_missing_returns_empty():
    assert gb.decode_data_blob([]) == {}
    assert gb.decode_data_blob([["seq", "chunk"]]) == {}
    assert gb.decode_data_blob(gb.encode_data_blob({})) == {}


# --------------------------------------------------------------------------- #
# meta round-trip
# --------------------------------------------------------------------------- #
def test_meta_roundtrip():
    meta = {"revision": "7", "device": "abc", "schema": "3"}
    rows = gb.meta_to_rows(meta)
    assert rows[0] == ["key", "value"]
    assert gb.rows_to_meta(rows) == meta


def test_meta_empty():
    assert gb.rows_to_meta([]) == {}
    assert gb.rows_to_meta(gb.meta_to_rows({})) == {}


def test_meta_coerces_values_to_str():
    rows = gb.meta_to_rows({"revision": 7})
    assert rows[1] == ["revision", "7"]


# --------------------------------------------------------------------------- #
# photo map round-trip
# --------------------------------------------------------------------------- #
def test_photo_map_roundtrip_coerces_product_id_int():
    entries = [
        {"product_id": 100, "drive_file_id": "f1", "filename": "100_a.png",
         "checksum": "aaa"},
        {"product_id": 101, "drive_file_id": "f2", "filename": "101_b.png",
         "checksum": "bbb"},
    ]
    rows = gb.photo_map_to_rows(entries)
    assert rows[0] == gb._PHOTO_MAP_COLUMNS

    back = gb.rows_to_photo_map(rows)
    assert back == entries
    assert all(isinstance(e["product_id"], int) for e in back)


def test_photo_map_coerces_str_product_id_from_sheet():
    # Sheets returns everything as strings; product_id must come back as int.
    rows = [
        gb._PHOTO_MAP_COLUMNS,
        ["100", "f1", "100_a.png", "aaa"],
    ]
    back = gb.rows_to_photo_map(rows)
    assert back == [
        {"product_id": 100, "drive_file_id": "f1", "filename": "100_a.png",
         "checksum": "aaa"}
    ]


def test_photo_map_empty():
    assert gb.rows_to_photo_map([]) == []
    assert gb.rows_to_photo_map(gb.photo_map_to_rows([])) == []


# --------------------------------------------------------------------------- #
# readable per-table rows
# --------------------------------------------------------------------------- #
def test_readable_table_rows_header_then_data():
    conn = _sample_db()
    data = serialize.dump_rows(conn)
    rows = gb.readable_table_rows(data, "brands")

    assert rows[0] == data["brands"]["columns"]
    assert rows[1:] == [list(r) for r in data["brands"]["rows"]]


def test_readable_table_rows_missing_table():
    assert gb.readable_table_rows({}, "brands") == []


# --------------------------------------------------------------------------- #
# spreadsheet id extraction
# --------------------------------------------------------------------------- #
def test_extract_spreadsheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/1AbC_-xyz0123456789/edit#gid=0"
    assert gb.extract_spreadsheet_id(url) == "1AbC_-xyz0123456789"


def test_extract_spreadsheet_id_from_url_with_query_string():
    url = "https://docs.google.com/spreadsheets/d/1AbC_-xyz0123456789/edit?usp=sharing"
    assert gb.extract_spreadsheet_id(url) == "1AbC_-xyz0123456789"


def test_extract_spreadsheet_id_passes_through_bare_id():
    bare = "1AbCdEfGhIjKlMnOpQrStUv"  # 23 chars, id-looking
    assert len(bare) >= 20
    assert gb.extract_spreadsheet_id(bare) == bare


def test_extract_spreadsheet_id_returns_stripped_garbage():
    assert gb.extract_spreadsheet_id("  nope  ") == "nope"


# --------------------------------------------------------------------------- #
# adopt classification
# --------------------------------------------------------------------------- #
def test_classify_adopt_numobel():
    assert gb.classify_adopt(["_data", "_meta", "products"]) == "numobel"


def test_classify_adopt_empty():
    assert gb.classify_adopt([]) == "empty"
    assert gb.classify_adopt(["Sheet1"]) == "empty"


def test_classify_adopt_foreign():
    assert gb.classify_adopt(["Budget", "Notes"]) == "foreign"


# --------------------------------------------------------------------------- #
# offline guarantee: importing this module never imported googleapiclient
# --------------------------------------------------------------------------- #
def test_no_google_import_triggered():
    import sys

    assert "googleapiclient" not in sys.modules
