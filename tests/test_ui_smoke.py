"""Headless smoke tests for the interactive UI wiring.

These run Qt with the offscreen platform so they need no display. They verify
the detail panel renders a transitive color family and that the create
dialogs talk to the mutation layer — guarding against role/signal wiring
regressions that unit tests on search/mutations can't catch.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db, mutations  # noqa: E402
from numobel.ui.detail_panel import DetailPanel  # noqa: E402
from numobel.ui.forms import ProductFormDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.create_schema(c)
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (2,'BA','Bajaj',1)")
    for pid, bid, sku, color in [
        (10, 1, "AT5", "Aqua"),
        (20, 2, "BA9", "Aqua"),
        (30, 2, "BA8", "Aqua"),
    ]:
        c.execute(
            "INSERT INTO products(id, brand_id, sku, color_name) VALUES (?,?,?,?)",
            (pid, bid, sku, color),
        )
    c.commit()
    yield c
    c.close()


def test_detail_panel_shows_full_family(app, conn):
    # Build a 3-member family linked only through product 10.
    mutations.add_to_family(conn, 10, 20)
    mutations.add_to_family(conn, 10, 30)

    panel = DetailPanel(conn)
    panel.set_product(20)  # 20 links directly only to 10...

    labels = [
        panel._similar_list.item(i).text()
        for i in range(panel._similar_list.count())
    ]
    # ...yet the panel shows the whole family (10 and 30), not just 10.
    assert any("AT5" in t for t in labels)
    assert any("BA8" in t for t in labels)
    assert len(labels) == 2


def test_detail_panel_remove_member_dissolves_pair(app, conn):
    mutations.add_to_family(conn, 10, 20)

    panel = DetailPanel(conn)
    panel.set_product(10)
    panel._similar_list.setCurrentRow(0)
    panel._on_remove_link()  # remove the only family member

    assert panel._similar_list.count() == 1
    assert "No similar colors" in panel._similar_list.item(0).text()
    assert (
        conn.execute(
            "SELECT color_group_id FROM products WHERE id=10"
        ).fetchone()[0]
        is None
    )


def test_product_form_dialog_creates_product(app, conn):
    dialog = ProductFormDialog(conn, preselect_brand_id=1)
    dialog._sku.setText("AT77")
    dialog._color.setText("New Teal")
    dialog._on_accept()

    new_id = dialog.created_product_id()
    assert new_id is not None
    row = conn.execute(
        "SELECT brand_id, sku, color_name FROM products WHERE id=?", (new_id,)
    ).fetchone()
    assert row["brand_id"] == 1
    assert row["sku"] == "AT77"
    assert row["color_name"] == "New Teal"
