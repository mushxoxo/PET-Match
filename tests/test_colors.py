"""Tests for the name->swatch-color resolver (headless, no QApplication needed)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402

from numobel import db  # noqa: E402
from numobel.ui import colors  # noqa: E402


def test_resolve_matches_known_color_word():
    assert colors.resolve_name_color("Blizzard Grey").name() == "#9a9a9a"


def test_resolve_prefers_last_matching_word():
    # "Ocean Blue": both match; the head color word (last) wins -> blue.
    assert colors.resolve_name_color("Ocean Blue").name() == "#2e6fb0"


def test_resolve_returns_none_for_unknown_and_empty():
    assert colors.resolve_name_color("Zorblax 9000") is None
    assert colors.resolve_name_color("") is None
    assert colors.resolve_name_color(None) is None


def test_resolve_is_deterministic():
    a = colors.resolve_name_color("Walnut")
    b = colors.resolve_name_color("Walnut")
    assert a.name() == b.name()


def _conn_with_family():
    conn = db.connect(":memory:")
    db.create_schema(conn)
    conn.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    conn.execute("INSERT INTO color_groups(id, created_at) VALUES (1,'2026-01-01')")
    # Two named members (red, blue) + one unnamed product, all in group 1.
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name, color_group_id) "
        "VALUES (10, 1, 'A1', 'Pure Red', 1)"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name, color_group_id) "
        "VALUES (20, 1, 'A2', 'Deep Blue', 1)"
    )
    conn.execute(
        "INSERT INTO products(id, brand_id, sku, color_name, color_group_id) "
        "VALUES (30, 1, 'A3', NULL, 1)"
    )
    conn.commit()
    return conn


def test_family_average_of_resolvable_members():
    conn = _conn_with_family()
    red = QColor("#c0392b")
    blue = QColor("#2e6fb0")
    expected = QColor(
        (red.red() + blue.red()) // 2,
        (red.green() + blue.green()) // 2,
        (red.blue() + blue.blue()) // 2,
    )
    got = colors.family_average_color(conn, 30)
    assert got.name() == expected.name()


def test_swatch_color_name_then_family_then_grey():
    conn = _conn_with_family()
    # Named product -> its own color.
    named = conn.execute("SELECT * FROM products WHERE id=10").fetchone()
    assert colors.swatch_color(conn, named).name() == "#c0392b"
    # Unnamed product in a family -> family average (not grey).
    unnamed = conn.execute("SELECT * FROM products WHERE id=30").fetchone()
    red = QColor("#c0392b")
    blue = QColor("#2e6fb0")
    expected_avg = QColor(
        (red.red() + blue.red()) // 2,
        (red.green() + blue.green()) // 2,
        (red.blue() + blue.blue()) // 2,
    ).name()
    assert colors.swatch_color(conn, unnamed).name() == expected_avg
    # Unnamed, no family -> grey.
    conn.execute("INSERT INTO products(id, brand_id, sku) VALUES (40,1,'A4')")
    conn.commit()
    lone = conn.execute("SELECT * FROM products WHERE id=40").fetchone()
    assert colors.swatch_color(conn, lone).name() == colors.NEUTRAL_GREY.name()
