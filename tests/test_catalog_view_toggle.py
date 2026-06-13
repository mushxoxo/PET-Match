"""The catalog List/Gallery toggle shares model + selection (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel import db  # noqa: E402
from numobel.ui.catalog_tab import CatalogTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.create_schema(c)
    c.execute("INSERT INTO brands(id, code, name, has_sheet) VALUES (1,'AT','Acme',1)")
    for pid, sku, color in [(10, "AT5", "Aqua"), (20, "AT6", "Rose"), (30, "AT7", "Mint")]:
        c.execute(
            "INSERT INTO products(id, brand_id, sku, color_name) VALUES (?,1,?,?)",
            (pid, sku, color),
        )
    c.commit()
    yield c
    c.close()


def test_both_views_share_model_and_selection(app, conn):
    tab = CatalogTab(conn)
    # Same backing model and selection model for both views.
    assert tab.list_view.model() is tab.gallery_view.model()
    assert tab.list_view.selectionModel() is tab.gallery_view.selectionModel()


def test_toggle_preserves_selection(app, conn):
    tab = CatalogTab(conn)
    assert tab.results_model.rowCount() == 3
    # Select the second row in list mode.
    index = tab.results_model.index(1, 0)
    from PySide6.QtCore import QItemSelectionModel

    tab.list_view.selectionModel().select(
        index,
        QItemSelectionModel.ClearAndSelect,
    )
    selected_before = tab.list_view.selectionModel().selectedIndexes()[0].row()

    tab.set_view_mode("gallery")

    selected_after = tab.gallery_view.selectionModel().selectedIndexes()[0].row()
    assert selected_after == selected_before == 1
    assert tab.stack.currentWidget() is tab.gallery_view
