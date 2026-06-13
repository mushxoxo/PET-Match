"""Detail panel collapsible + edit behavior (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db  # noqa: E402
from numobel.ui.detail_panel import DetailPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.create_schema(c)
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (2,'BA','Bajaj',1)")
    c.execute("INSERT INTO products(id, brand_id, sku, color_name) VALUES (10,1,'AT5','Blizzard Grey')")
    c.execute("INSERT INTO products(id, brand_id, sku, color_name) VALUES (20,1,'AT6','Ocean Blue')")
    c.commit()
    yield c
    c.close()


def test_defaults_details_collapsed_similar_expanded(app, conn):
    panel = DetailPanel(conn)
    panel.set_product(10)
    assert panel._details_section.is_expanded() is False
    assert panel._similar_section.is_expanded() is True


def test_expanded_details_sticky_across_products(app, conn):
    panel = DetailPanel(conn)
    panel.set_product(10)
    panel._details_section.set_expanded(True)
    panel.set_product(20)
    assert panel._details_section.is_expanded() is True
