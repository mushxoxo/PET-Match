"""Catalog screen: search/sort/filter toolbar, List/Gallery views, detail.

Extracted from the old ``main_window`` so the window can become a thin shell.
Both the list and gallery ``QListView``s share one ``QStandardItemModel`` and
one selection model, so the view toggle never loses the current selection.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from numobel import search
from numobel.ui import colors, delegates
from numobel.ui.delegates import (
    BRAND_ROLE,
    COLOR_ROLE,
    ID_ROLE,
    NAME_ROLE,
    SEED_ROLE,
    SUB_ROLE,
)
from numobel.ui.detail_panel import DetailPanel
from numobel.ui.forms import BrandFormDialog, ProductFormDialog
from numobel.ui.widgets import Card, ViewToggle

_SCOPE_MAP = [("All", "all"), ("Color", "color"), ("Brand", "brand")]
# Labels matching the if/elif dispatch in _sorted().
_SORT_OPTIONS = ["Color", "Brand", "Thickness"]
_SEARCH_LIMIT = 5000


class CatalogTab(QWidget):
    """The catalog page: toolbar + List/Gallery stack + detail panel."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_views())

        self.detail_panel = DetailPanel(self._conn, on_navigate=self.load_product)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 440])
        layout.addWidget(splitter, 1)

        self._run_search()

    # ------------------------------------------------------------------ #
    # Toolbar
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> QWidget:
        card = Card()
        bar = QHBoxLayout(card)
        bar.setContentsMargins(14, 12, 14, 12)
        bar.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setProperty("class", "SearchField")
        self.search_edit.setPlaceholderText("Search by color or brand…")
        self.search_edit.returnPressed.connect(self._run_search)
        self.search_edit.textChanged.connect(self._run_search)
        bar.addWidget(self.search_edit, 1)

        self.scope_combo = QComboBox()
        for label, value in _SCOPE_MAP:
            self.scope_combo.addItem(label, value)
        self.scope_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.scope_combo)

        self.brand_combo = QComboBox()
        self._fill_brand_combo()
        self.brand_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.brand_combo)

        self.sort_combo = QComboBox()
        for label in _SORT_OPTIONS:
            self.sort_combo.addItem(label)
        self.sort_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.sort_combo)

        self.view_toggle = ViewToggle()
        self.view_toggle.changed.connect(self.set_view_mode)
        bar.addWidget(self.view_toggle)

        return card

    def _fill_brand_combo(self) -> None:
        self.brand_combo.addItem("All brands", None)
        for brand in search.list_brands(self._conn, only_with_sheet=True):
            label = brand["name"] or brand["code"]
            self.brand_combo.addItem(f"{label} ({brand['code']})", brand["code"])

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #
    def _build_views(self) -> QWidget:
        self.results_model = QStandardItemModel()

        self.list_view = QListView()
        self.list_view.setModel(self.results_model)
        self.list_view.setItemDelegate(delegates.ProductListDelegate(self.list_view))
        self.list_view.setSelectionMode(QListView.SingleSelection)
        self.list_view.setUniformItemSizes(True)

        self.gallery_view = QListView()
        self.gallery_view.setModel(self.results_model)
        self.gallery_view.setItemDelegate(
            delegates.ProductGalleryDelegate(self.gallery_view)
        )
        self.gallery_view.setViewMode(QListView.IconMode)
        self.gallery_view.setResizeMode(QListView.Adjust)
        self.gallery_view.setWrapping(True)
        self.gallery_view.setUniformItemSizes(True)
        self.gallery_view.setSelectionMode(QListView.SingleSelection)
        self.gallery_view.setMovement(QListView.Static)

        # Share ONE selection model, owned by this widget so teardown order
        # between the two views can't free it twice.
        self._sel_model = QItemSelectionModel(self.results_model, self)
        self.list_view.setSelectionModel(self._sel_model)
        self.gallery_view.setSelectionModel(self._sel_model)
        self._sel_model.selectionChanged.connect(self._on_selection_changed)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_view)     # index 0
        self.stack.addWidget(self.gallery_view)  # index 1
        return self.stack

    def set_view_mode(self, mode: str) -> None:
        self.stack.setCurrentWidget(
            self.gallery_view if mode == "gallery" else self.list_view
        )

    # ------------------------------------------------------------------ #
    # Search / results
    # ------------------------------------------------------------------ #
    def _run_search(self) -> None:
        query = self.search_edit.text()
        scope = self.scope_combo.currentData() or "all"
        brand_code = self.brand_combo.currentData()
        rows = search.search_products(
            self._conn, query, scope=scope, brand_code=brand_code, limit=_SEARCH_LIMIT
        )
        self._populate_results(self._sorted(rows))

    def _sorted(self, rows):
        key = self.sort_combo.currentText()
        if key == "Brand":
            return sorted(rows, key=lambda r: (r["brand_code"] or "").lower())
        if key == "Thickness":
            return sorted(
                rows,
                key=lambda r: (r["thickness"] is None, r["thickness"] or 0.0),
            )
        return sorted(rows, key=lambda r: (r["color_name"] or "").lower())

    def _populate_results(self, rows) -> None:
        self.results_model.setRowCount(0)
        for row in rows:
            brand = row["brand_code"] or row["brand_name"] or ""
            color = row["color_name"] or "(unnamed)"
            shade = row["shade_no"] or ""
            thickness = row["thickness"]
            thick = "" if thickness is None else f"{thickness:g} mm"
            sub = " · ".join(p for p in (shade, thick) if p)

            item = QStandardItem()
            item.setEditable(False)
            item.setData(int(row["id"]), ID_ROLE)
            item.setData(color, NAME_ROLE)
            item.setData(brand, BRAND_ROLE)
            item.setData(sub, SUB_ROLE)
            item.setData(row["color_name"] or row["sku"] or "", SEED_ROLE)
            item.setData(colors.swatch_color(self._conn, row).name(), COLOR_ROLE)
            self.results_model.appendRow(item)

    def _on_selection_changed(self, *_args) -> None:
        self.detail_panel.set_product(self._selected_product_id())

    def _selected_product_id(self) -> Optional[int]:
        indexes = self.list_view.selectionModel().selectedIndexes()
        if not indexes:
            return None
        return self.results_model.itemFromIndex(indexes[0]).data(ID_ROLE)

    # ------------------------------------------------------------------ #
    # Public navigation + creation API (used by the window shell/menu)
    # ------------------------------------------------------------------ #
    def load_product(self, product_id: int) -> None:
        for r in range(self.results_model.rowCount()):
            item = self.results_model.item(r, 0)
            if item is not None and item.data(ID_ROLE) == product_id:
                index = self.results_model.index(r, 0)
                self.list_view.selectionModel().select(
                    index, QItemSelectionModel.ClearAndSelect
                )
                self.list_view.scrollTo(index)
                self.gallery_view.scrollTo(index)
                break
        self.detail_panel.set_product(product_id)

    def reload_brand_filter(self) -> None:
        current = self.brand_combo.currentData()
        self.brand_combo.blockSignals(True)
        self.brand_combo.clear()
        self._fill_brand_combo()
        idx = self.brand_combo.findData(current)
        if idx >= 0:
            self.brand_combo.setCurrentIndex(idx)
        self.brand_combo.blockSignals(False)

    def add_product(self) -> None:
        dialog = ProductFormDialog(self._conn, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.reload_brand_filter()
        new_id = dialog.created_product_id()
        self._run_search()
        if new_id is not None:
            self.load_product(int(new_id))

    def add_brand(self) -> None:
        BrandFormDialog(self._conn, self).exec()
        self.reload_brand_filter()
