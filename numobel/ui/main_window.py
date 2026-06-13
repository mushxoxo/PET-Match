"""Top-level window: a sidebar nav rail + a stack of pages."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from numobel import db
from numobel.exporter.run_export import export
from numobel.importer.snapshot import import_workbook
from numobel.ui import theme
from numobel.ui.audit_tab import AuditTab
from numobel.ui.catalog_tab import CatalogTab
from numobel.ui.onboarding import OnboardingWidget
from numobel.ui.price_tab import PriceTab
from numobel.ui.sidebar import Sidebar

_PAGES = [("▦", "Catalog"), ("₹", "Prices"), ("◷", "Audit")]
_CATALOG, _PRICES, _AUDIT = 0, 1, 2

# Outer stack pages: onboarding empty state vs. the populated app shell.
_ONBOARDING, _SHELL = 0, 1


class MainWindow(QMainWindow):
    """Sidebar shell hosting the catalog, prices, and audit pages.

    On a fresh database (no products yet) the window shows an onboarding screen
    that prompts the user to import a workbook; once a catalog exists it shows
    the normal sidebar + pages shell.
    """

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn

        self.setWindowTitle("NUMOBEL Colored PET Catalog")
        self.resize(1180, 740)

        self._build_menu()

        self._outer = QStackedWidget()
        self.onboarding = OnboardingWidget()
        self.onboarding.file_selected.connect(self._import_catalog)
        self._outer.addWidget(self.onboarding)        # index 0
        self._outer.addWidget(self._build_shell())    # index 1
        self.setCentralWidget(self._outer)

        self._outer.setCurrentIndex(
            _SHELL if self._has_catalog() else _ONBOARDING
        )

    def _build_shell(self) -> QWidget:
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = Sidebar(_PAGES)
        self.sidebar.page_changed.connect(self._on_page_changed)
        self.sidebar.add_requested.connect(self._on_add_product)
        self.sidebar.theme_toggle_requested.connect(self._toggle_theme)
        row.addWidget(self.sidebar)

        self.catalog_tab = CatalogTab(self._conn)
        self.price_tab = PriceTab(self._conn)
        self.audit_tab = AuditTab(self._conn)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.catalog_tab)
        self.stack.addWidget(self.price_tab)
        self.stack.addWidget(self.audit_tab)
        row.addWidget(self.stack, 1)

        return shell

    def _has_catalog(self) -> bool:
        """True when the catalog already holds at least one product."""
        return (
            self._conn.execute("SELECT 1 FROM products LIMIT 1").fetchone()
            is not None
        )

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

        catalog_menu.addSeparator()
        import_action = QAction("&Import / Replace catalog…", self)
        import_action.triggered.connect(lambda: self._import_catalog())
        catalog_menu.addAction(import_action)
        export_action = QAction("&Export catalog…", self)
        export_action.triggered.connect(self._export_catalog)
        catalog_menu.addAction(export_action)

        view_menu = self.menuBar().addMenu("&View")
        toggle = QAction("Toggle &Dark / Light Theme", self)
        toggle.setShortcut("Ctrl+T")
        toggle.triggered.connect(self._toggle_theme)
        view_menu.addAction(toggle)

    # ------------------------------------------------------------------ #
    # Import
    # ------------------------------------------------------------------ #
    def _import_catalog(self, path: str | None = None) -> None:
        """Import a workbook into the live DB, replacing any existing catalog.

        ``path`` is supplied by the onboarding screen's file picker; the menu
        action passes none, so we open our own dialog. Re-importing wipes the
        current catalog (``reset_catalog``), so confirm first when data exists.
        """
        if path is None:
            from PySide6.QtWidgets import QFileDialog

            path, _ = QFileDialog.getOpenFileName(
                self, "Import catalog workbook", "", "Excel workbook (*.xlsx)"
            )
            if not path:
                return

        if self._has_catalog():
            answer = QMessageBox.question(
                self,
                "Replace catalog?",
                "Importing will replace the entire catalog — all products, "
                "links, brands and prices, including manual edits.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Auto-detects snapshot vs. original master workbook and routes
            # accordingly; both replace the existing catalog.
            summary = import_workbook(excel_path=path, conn=self._conn)
            # build() leaves resolved links inspectable; migrate() folds them
            # into transitive color groups on the same live connection.
            db.migrate(self._conn)
        except Exception as exc:  # noqa: BLE001 — surface any import failure
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self,
                "Import failed",
                "The workbook could not be imported. Existing data is "
                f"unchanged.\n\n{type(exc).__name__}: {exc}",
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.catalog_tab.refresh()
        self.price_tab.refresh()
        self.audit_tab.refresh()
        self._outer.setCurrentIndex(_SHELL)
        self._show_catalog()

        links = summary["links"]
        QMessageBox.information(
            self,
            "Import complete",
            f"Imported {summary['total_products']} products and "
            f"{summary['prices']} price rows.\n\n"
            f"Links — resolved: {links['resolved']}, "
            f"unresolved: {links['unresolved']}, external: {links['external']}.",
        )

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export_catalog(self) -> None:
        """Write the whole catalog to a single shareable ``.xlsx`` snapshot.

        The snapshot embeds every table plus attached photos, so a colleague can
        import it (via the same Import action, which auto-detects the format) and
        continue working from the exact current state.
        """
        from PySide6.QtWidgets import QFileDialog

        if not self._has_catalog():
            QMessageBox.information(
                self,
                "Nothing to export",
                "The catalog is empty. Import or add products first.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export catalog", "numobel-catalog.xlsx", "Excel workbook (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            summary = export(excel_path=path, conn=self._conn)
        except Exception as exc:  # noqa: BLE001 — surface any export failure
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self,
                "Export failed",
                f"The catalog could not be exported.\n\n{type(exc).__name__}: {exc}",
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self,
            "Export complete",
            f"Exported {summary['total_products']} products, "
            f"{summary['prices']} price rows and {summary['images']} photos to:\n\n"
            f"{path}",
        )

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
