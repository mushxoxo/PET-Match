"""Catalog rows carry a resolved swatch color (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db  # noqa: E402
from numobel.ui import colors  # noqa: E402
from numobel.ui.catalog_tab import CatalogTab  # noqa: E402
from numobel.ui.delegates import COLOR_ROLE, NAME_ROLE  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.create_schema(c)
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    c.execute("INSERT INTO products(id, brand_id, sku, color_name) VALUES (10,1,'AT5','Ocean Blue')")
    c.commit()
    yield c
    c.close()


def test_row_color_role_matches_resolver(app, conn):
    tab = CatalogTab(conn)
    item = tab.results_model.item(0, 0)
    assert item is not None, "catalog model should auto-populate on init"
    assert item.data(NAME_ROLE) == "Ocean Blue"
    assert item.data(COLOR_ROLE) == colors.resolve_name_color("Ocean Blue").name()
