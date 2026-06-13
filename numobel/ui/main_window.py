"""Main application window for the NUMOBEL catalog (Phase A, read-only)."""

from __future__ import annotations

import sqlite3
from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QAction, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from numobel import db, search
from numobel.ui import theme
from numobel.ui.audit_tab import AuditTab
from numobel.ui.detail_panel import DetailPanel
from numobel.ui.forms import BrandFormDialog, ProductFormDialog
from numobel.ui.price_tab import PriceTab

# Role storing the product id on a results-table row.
_ID_ROLE = Qt.UserRole + 1

# Scope combo labels -> search_products scope values.
_SCOPE_MAP = [("All", "all"), ("Color", "color"), ("Brand", "brand")]

# (header, numeric?)
_RESULT_COLUMNS = [
    ("Brand", False),
    ("SKU", False),
    ("Color Name", False),
    ("Shade No", False),
    ("Thickness", True),
]

_SEARCH_LIMIT = 5000


class MainWindow(QMainWindow):
    """Top-level window: a Catalog tab and a Prices tab."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn

        self.setWindowTitle("NUMOBEL Colored PET Catalog")
        self.resize(1100, 700)

        self._build_menu()

        self.tabs = QTabWidget(self)
        self.setCentralWidget(self.tabs)

        self.audit_tab = AuditTab(conn)
        self.tabs.addTab(self._build_catalog_tab(), "Catalog")
        self.tabs.addTab(PriceTab(conn), "Prices")
        self.tabs.addTab(self.audit_tab, "Audit")
        # Refresh the audit log each time its tab becomes visible.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Initial population: empty search returns all products (capped).
        self._run_search()

    # ------------------------------------------------------------------ #
    # Menu + theme
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        catalog_menu = self.menuBar().addMenu("&Catalog")
        add_product = QAction("Add &Product / Color…", self)
        add_product.setShortcut("Ctrl+N")
        add_product.triggered.connect(self._on_add_product)
        catalog_menu.addAction(add_product)
        add_brand = QAction("Add &Brand…", self)
        add_brand.triggered.connect(self._on_add_brand)
        catalog_menu.addAction(add_brand)

        view_menu = self.menuBar().addMenu("&View")
        toggle = QAction("Toggle &Dark / Light Theme", self)
        toggle.setShortcut("Ctrl+T")
        toggle.triggered.connect(self._toggle_theme)
        view_menu.addAction(toggle)

    # ------------------------------------------------------------------ #
    # Catalog creation
    # ------------------------------------------------------------------ #
    def _on_add_brand(self) -> None:
        BrandFormDialog(self._conn, self).exec()
        # A new brand may belong in the brand filter; rebuild it.
        self._reload_brand_filter()

    def _on_add_product(self) -> None:
        dialog = ProductFormDialog(self._conn, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._reload_brand_filter()
        new_id = dialog.created_product_id()
        # Refresh results and jump to the newly created product.
        self._run_search()
        if new_id is not None:
            self.load_product(int(new_id))

    def _reload_brand_filter(self) -> None:
        """Rebuild the brand filter combo, preserving the current selection."""
        current = self.brand_combo.currentData()
        self.brand_combo.blockSignals(True)
        self.brand_combo.clear()
        self.brand_combo.addItem("All brands", None)
        for brand in search.list_brands(self._conn, only_with_sheet=True):
            label = brand["name"] or brand["code"]
            self.brand_combo.addItem(f"{label} ({brand['code']})", brand["code"])
        idx = self.brand_combo.findData(current)
        if idx >= 0:
            self.brand_combo.setCurrentIndex(idx)
        self.brand_combo.blockSignals(False)

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        current = db.get_setting(self._conn, "theme", theme.DEFAULT_THEME)
        new = theme.next_theme(current)
        theme.apply_theme(app, new)
        db.set_setting(self._conn, "theme", new)

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.audit_tab:
            self.audit_tab.refresh()

    # ------------------------------------------------------------------ #
    # Catalog tab construction
    # ------------------------------------------------------------------ #
    def _build_catalog_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addLayout(self._build_search_bar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_results_table())

        self.detail_panel = DetailPanel(self._conn, on_navigate=self.load_product)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 420])
        layout.addWidget(splitter, 1)

        return tab

    def _build_search_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.search_edit = QLineEdit()
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
        self.brand_combo.addItem("All brands", None)
        for brand in search.list_brands(self._conn, only_with_sheet=True):
            label = brand["name"] or brand["code"]
            self.brand_combo.addItem(f"{label} ({brand['code']})", brand["code"])
        self.brand_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.brand_combo)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._run_search)
        bar.addWidget(self.search_button)

        return bar

    def _build_results_table(self) -> QTableView:
        self.results_view = QTableView()
        self.results_view.setSortingEnabled(True)
        self.results_view.setSelectionBehavior(QTableView.SelectRows)
        self.results_view.setSelectionMode(QTableView.SingleSelection)
        self.results_view.setEditTriggers(QTableView.NoEditTriggers)
        self.results_view.setAlternatingRowColors(True)
        self.results_view.verticalHeader().setVisible(False)

        self.results_model = QStandardItemModel()
        self.results_model.setHorizontalHeaderLabels(
            [c[0] for c in _RESULT_COLUMNS]
        )
        self.results_view.setModel(self.results_model)

        header = self.results_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # Color Name

        sel = self.results_view.selectionModel()
        sel.selectionChanged.connect(self._on_selection_changed)

        return self.results_view

    # ------------------------------------------------------------------ #
    # Search + results
    # ------------------------------------------------------------------ #
    def _run_search(self) -> None:
        query = self.search_edit.text()
        scope = self.scope_combo.currentData() or "all"
        brand_code = self.brand_combo.currentData()

        rows = search.search_products(
            self._conn,
            query,
            scope=scope,
            brand_code=brand_code,
            limit=_SEARCH_LIMIT,
        )
        self._populate_results(rows)

    def _populate_results(self, rows) -> None:
        was_sorting = self.results_view.isSortingEnabled()
        self.results_view.setSortingEnabled(False)
        self.results_model.setRowCount(0)

        for row in rows:
            brand = row["brand_code"] or row["brand_name"] or ""
            sku = row["sku"] or ""
            color = row["color_name"] or ""
            shade = row["shade_no"] or ""
            thickness = row["thickness"]
            thick_text = "" if thickness is None else f"{thickness:g}"

            cells = [brand, sku, color, shade, thick_text]
            items: list[QStandardItem] = []
            for idx, ((_, numeric), text) in enumerate(
                zip(_RESULT_COLUMNS, cells)
            ):
                item = QStandardItem(text)
                item.setEditable(False)
                if numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if idx == 0:
                    item.setData(int(row["id"]), _ID_ROLE)
                items.append(item)
            self.results_model.appendRow(items)

        self.results_view.setSortingEnabled(was_sorting)

    def _on_selection_changed(self, *_args) -> None:
        product_id = self._selected_product_id()
        self.detail_panel.set_product(product_id)

    def _selected_product_id(self) -> Optional[int]:
        indexes = self.results_view.selectionModel().selectedRows(0)
        if not indexes:
            return None
        return self.results_model.itemFromIndex(indexes[0]).data(_ID_ROLE)

    # ------------------------------------------------------------------ #
    # Public navigation API
    # ------------------------------------------------------------------ #
    def load_product(self, product_id: int) -> None:
        """Navigate to ``product_id``: select it in the table if present,
        and always refresh the detail panel."""
        # Try to select the matching row.
        for r in range(self.results_model.rowCount()):
            item = self.results_model.item(r, 0)
            if item is not None and item.data(_ID_ROLE) == product_id:
                index = self.results_model.index(r, 0)
                self.results_view.selectionModel().select(
                    index,
                    QItemSelectionModel.ClearAndSelect
                    | QItemSelectionModel.Rows,
                )
                self.results_view.scrollTo(index)
                break

        # Always refresh the detail panel (row may not be in current results).
        self.detail_panel.set_product(product_id)
