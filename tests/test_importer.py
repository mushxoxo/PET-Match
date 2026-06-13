"""Tests for the Excel import pipeline.

Builds into a tmp_path SQLite DB against the REAL workbook (read-only) and
asserts product counts, blank-row skipping, extra_json round-trips, and
resolved/external link spot-checks.
"""

import json
from pathlib import Path

import pytest

from numobel import db
from numobel.importer import run_import

EXCEL_PATH = str(
    Path(__file__).resolve().parent.parent
    / "my_excel"
    / "NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("import") / "numobel.db")
    summary = run_import.build(db_path=db_path, excel_path=EXCEL_PATH)
    conn = db.connect(db_path)
    yield summary, conn
    conn.close()


def _brand_product_count(conn, code):
    return conn.execute(
        "SELECT COUNT(*) c FROM products p JOIN brands b ON b.id=p.brand_id "
        "WHERE b.code=?",
        (code,),
    ).fetchone()["c"]


def test_total_products_positive(built):
    summary, conn = built
    total = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    assert total > 0
    assert summary["total_products"] == total


def test_plausible_brand_counts(built):
    _, conn = built
    # Counts verified against the actual workbook data extents.
    assert _brand_product_count(conn, "UTAB") == pytest.approx(92, abs=5)
    assert _brand_product_count(conn, "BA") == pytest.approx(69, abs=5)
    assert _brand_product_count(conn, "AT") == pytest.approx(30, abs=3)
    assert _brand_product_count(conn, "SD") == pytest.approx(41, abs=3)
    assert _brand_product_count(conn, "MMT") == pytest.approx(15, abs=2)


def test_blank_padding_rows_skipped(built):
    _, conn = built
    # SAP sheet is padded to ~1000 rows; real data is well under that.
    sap = _brand_product_count(conn, "SAP")
    assert 0 < sap < 1000


def test_extra_json_round_trips(built):
    _, conn = built
    # Bajaj row 0 (BA01) has a numeric col3 stored in extra_json.
    row = conn.execute(
        "SELECT extra_json FROM products p JOIN brands b ON b.id=p.brand_id "
        "WHERE b.code='BA' AND p.sku='BA01'"
    ).fetchone()
    assert row is not None
    extra = json.loads(row["extra_json"])
    assert "col3" in extra


def _links_for(conn, brand_code, *, shade_no=None, color_name=None):
    sql = (
        "SELECT cl.normalized, cl.status, cl.to_product_id "
        "FROM color_links cl "
        "JOIN products p ON p.id = cl.from_product_id "
        "JOIN brands b ON b.id = p.brand_id WHERE b.code = ?"
    )
    args = [brand_code]
    if shade_no is not None:
        sql += " AND p.shade_no = ?"
        args.append(shade_no)
    if color_name is not None:
        sql += " AND p.color_name = ?"
        args.append(color_name)
    return conn.execute(sql, args).fetchall()


def test_at_shade5_pcp27(built):
    _, conn = built
    norms = {r["normalized"] for r in _links_for(conn, "AT", shade_no="5")}
    assert "PCP27" in norms


def test_utab_light_grey_pcp45(built):
    _, conn = built
    norms = {r["normalized"] for r in _links_for(conn, "UTAB", color_name="Light Grey")}
    assert "PCP45" in norms


def test_mmt01_links_to_numobel(built):
    _, conn = built
    row = conn.execute(
        """SELECT cl.normalized, cl.status, tb.code AS to_brand
           FROM color_links cl
           JOIN products p ON p.id = cl.from_product_id
           JOIN brands b ON b.id = p.brand_id
           LEFT JOIN products tp ON tp.id = cl.to_product_id
           LEFT JOIN brands tb ON tb.id = tp.brand_id
           WHERE b.code = 'MMT' AND p.sku = 'MMT01'
             AND cl.normalized = 'NW01133'"""
    ).fetchone()
    assert row is not None
    assert row["status"] == "resolved"
    assert row["to_brand"] == "NUMOBEL"


def _link_status(conn, brand_code, normalized):
    """Return the status of a from-brand's link with a given normalized ref."""
    row = conn.execute(
        "SELECT cl.status FROM color_links cl "
        "JOIN products p ON p.id = cl.from_product_id "
        "JOIN brands b ON b.id = p.brand_id "
        "WHERE b.code = ? AND cl.normalized = ? LIMIT 1",
        (brand_code, normalized),
    ).fetchone()
    return row["status"] if row else None


def test_sku_number_normalizes_padding_and_prefix():
    # Pure-logic check of the matcher key.
    assert run_import._sku_number("AT05") == "5"
    assert run_import._sku_number("AT5") == "5"
    assert run_import._sku_number("BOL06") == "6"
    assert run_import._sku_number("B6") == "6"
    assert run_import._sku_number("UT19") == "19"
    assert run_import._sku_number("UTAB19") == "19"
    assert run_import._sku_number("SAP130") == "130"
    assert run_import._sku_number(None) is None
    assert run_import._sku_number("ABC") is None


def test_zero_padded_ref_resolves(built):
    # SAP references AT shades as 'AT01'..'AT05'; AT SKUs are 'AT1'..'AT5'.
    _, conn = built
    assert _link_status(conn, "SAP", "AT05") == "resolved"


def test_prefix_variant_refs_resolve(built):
    # EA references UTAB as 'UT19' (stored 'UTAB19') and Bollard as 'BOL06'
    # (stored 'B6'); both should resolve via the shade-number fallback.
    _, conn = built
    assert _link_status(conn, "EA", "UT19") == "resolved"
    assert _link_status(conn, "EA", "BOL06") == "resolved"


def test_unresolved_links_minimized(built):
    # Before the matcher fix there were 24 unresolved links, nearly all of them
    # format mismatches. The fallback should clear the vast majority.
    _, conn = built
    unresolved = conn.execute(
        "SELECT COUNT(*) c FROM color_links WHERE status='unresolved'"
    ).fetchone()["c"]
    assert unresolved <= 6


def test_external_and_resolved_links_exist(built):
    summary, conn = built
    ext = conn.execute(
        "SELECT COUNT(*) c FROM color_links WHERE status='external'"
    ).fetchone()["c"]
    res = conn.execute(
        "SELECT COUNT(*) c FROM color_links WHERE status='resolved'"
    ).fetchone()["c"]
    assert ext >= 1
    assert res >= 1
    assert summary["links"]["external"] == ext
    assert summary["links"]["resolved"] == res
