"""Top-level window: a sidebar nav rail + a stack of pages."""

from __future__ import annotations

import sqlite3

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from numobel import db
from numobel.ui import theme
from numobel.ui.audit_tab import AuditTab
from numobel.ui.catalog_tab import CatalogTab
from numobel.ui.price_tab import PriceTab
from numobel.ui.sidebar import Sidebar

_PAGES = [("▦", "Catalog"), ("₹", "Prices"), ("◷", "Audit")]
_CATALOG, _PRICES, _AUDIT = 0, 1, 2


class MainWindow(QMainWindow):
    """Sidebar shell hosting the catalog, prices, and audit pages."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn

        self.setWindowTitle("NUMOBEL Colored PET Catalog")
        self.resize(1180, 740)

        self._build_menu()

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = Sidebar(_PAGES)
        self.sidebar.page_changed.connect(self._on_page_changed)
        self.sidebar.add_requested.connect(self._on_add_product)
        self.sidebar.theme_toggle_requested.connect(self._toggle_theme)
        row.addWidget(self.sidebar)

        self.catalog_tab = CatalogTab(conn)
        self.price_tab = PriceTab(conn)
        self.audit_tab = AuditTab(conn)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.catalog_tab)
        self.stack.addWidget(self.price_tab)
        self.stack.addWidget(self.audit_tab)
        row.addWidget(self.stack, 1)

        self.setCentralWidget(central)

    # ------------------------------------------------------------------ #
    # Menu / shortcuts
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
    # Page switching
    # ------------------------------------------------------------------ #
    def _on_page_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == _AUDIT:
            self.audit_tab.refresh()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _on_add_product(self) -> None:
        self._show_catalog()
        self.catalog_tab.add_product()

    def _on_add_brand(self) -> None:
        self.catalog_tab.add_brand()

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        current = db.get_setting(self._conn, "theme", theme.DEFAULT_THEME)
        new = theme.next_theme(current)
        theme.apply_theme(app, new)
        db.set_setting(self._conn, "theme", new)
        # Delegates re-poll current_palette() on repaint.
        self.stack.update()
        self.catalog_tab.list_view.viewport().update()
        self.catalog_tab.gallery_view.viewport().update()

    def _show_catalog(self) -> None:
        self.sidebar.set_current_index(_CATALOG)
        self.stack.setCurrentIndex(_CATALOG)

    # ------------------------------------------------------------------ #
    # Public navigation API
    # ------------------------------------------------------------------ #
    def load_product(self, product_id: int) -> None:
        self._show_catalog()
        self.catalog_tab.load_product(product_id)
