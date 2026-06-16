"""Top-level window: a sidebar nav rail + a stack of pages."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from numobel import db
from numobel.exporter.run_export import export
from numobel.importer.snapshot import import_workbook
from numobel.sync import state
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

    def __init__(
        self,
        conn: sqlite3.Connection,
        sync_service=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._conn = conn
        self._sync = sync_service

        self.setWindowTitle("NUMOBEL Colored PET Catalog")
        self.resize(1180, 740)

        self._build_menu()
        self._build_status_bar()

        self._outer = QStackedWidget()
        self.onboarding = OnboardingWidget()
        self.onboarding.file_selected.connect(self._import_catalog)
        self.onboarding.google_requested.connect(self._google_from_onboarding)
        self._outer.addWidget(self.onboarding)        # index 0
        self._outer.addWidget(self._build_shell())    # index 1
        self.setCentralWidget(self._outer)

        self._outer.setCurrentIndex(
            _SHELL if self._has_catalog() else _ONBOARDING
        )

        self._wire_sync_signals()

    def _build_status_bar(self) -> None:
        """Add a status bar with a permanent right-aligned sync indicator."""
        bar = QStatusBar()
        self.setStatusBar(bar)
        from numobel.sync.worker import STATUS_DISCONNECTED

        # Seed the indicator so it is never blank: with no service (or a service
        # that is not yet linked) show "Not connected" until the first real
        # statusChanged arrives.
        self._sync_status = QLabel(
            STATUS_DISCONNECTED
            if self._sync is None or not state.is_linked(self._conn)
            else ""
        )
        bar.addPermanentWidget(self._sync_status)

    def _wire_sync_signals(self) -> None:
        """Connect the sync service's public signals (no-op without a service)."""
        if self._sync is None:
            return
        self._sync.statusChanged.connect(self._on_sync_status)
        self._sync.pullFinished.connect(self._on_pull_finished)
        self._sync.conflictDetected.connect(self._on_conflict)
        self._sync.errored.connect(self._on_sync_error)
        self._sync.offlineNotice.connect(self._on_offline_notice)

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

        google_menu = self.menuBar().addMenu("&Google")
        connect_action = QAction("Connect Google…", self)
        connect_action.triggered.connect(self._connect_google)
        google_menu.addAction(connect_action)
        reload_action = QAction("Reload from Google", self)
        reload_action.triggered.connect(self._google_pull)
        google_menu.addAction(reload_action)
        sync_action = QAction("Sync now", self)
        sync_action.triggered.connect(self._google_push)
        google_menu.addAction(sync_action)
        google_menu.addSeparator()
        disconnect_action = QAction("Disconnect", self)
        disconnect_action.triggered.connect(self._google_disconnect)
        google_menu.addAction(disconnect_action)

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
    # Google sync
    # ------------------------------------------------------------------ #
    def _sync_unavailable(self) -> bool:
        """Inform the user and return True when no sync service is wired."""
        if self._sync is None:
            QMessageBox.information(
                self,
                "Google sync",
                "Google sync is not available.",
            )
            return True
        return False

    def _collect_google_credentials(self) -> tuple[str, str] | None:
        """Prompt for the OAuth client id + secret; return ``(id, secret)``.

        Returns ``None`` if the user cancels or leaves either field empty.
        Factored out so tests can monkeypatch the credential collection.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Connect Google")
        layout = QVBoxLayout(dialog)

        helper = QLabel(
            "Paste the OAuth client id and secret from a \"Desktop app\" "
            "client created in the Google Cloud Console "
            "(APIs & Services → Credentials)."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)

        form = QFormLayout()
        id_edit = QLineEdit(state.get_client_id(self._conn) or "")
        secret_edit = QLineEdit(state.get_client_secret(self._conn) or "")
        secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Client ID", id_edit)
        form.addRow("Client secret", secret_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None
        client_id = id_edit.text().strip()
        client_secret = secret_edit.text().strip()
        if not client_id or not client_secret:
            return None
        return client_id, client_secret

    def _connect_google(self) -> None:
        """Collect OAuth credentials and link the catalog to Google."""
        if self._sync_unavailable():
            return
        creds = self._collect_google_credentials()
        if creds is None:
            return
        self._sync.connect(creds[0], creds[1])

    def _google_pull(self) -> None:
        """Reload the catalog from Google (replaces the local catalog)."""
        if self._sync_unavailable():
            return
        if self._has_catalog():
            answer = QMessageBox.question(
                self,
                "Replace catalog?",
                "Reloading from Google will replace the entire catalog — all "
                "products, links, brands and prices, including manual edits.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._sync.pull()

    def _google_push(self) -> None:
        """Push local catalog edits up to Google now."""
        if self._sync_unavailable():
            return
        self._sync.push()

    def _google_disconnect(self) -> None:
        """Confirm, then clear all Google sync state."""
        if self._sync_unavailable():
            return
        answer = QMessageBox.question(
            self,
            "Disconnect Google?",
            "This unlinks the catalog from Google. Local data is kept, but "
            "edits will no longer sync.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._sync.disconnect()

    def _google_from_onboarding(self) -> None:
        """Onboarding "Load from Google…": pull if linked, else connect."""
        if self._sync is None:
            QMessageBox.information(
                self,
                "Google sync",
                "Google sync is not available.",
            )
            return
        if state.is_linked(self._conn):
            self._sync.pull()
        else:
            self._connect_google()

    # ------------------------------------------------------------------ #
    # Sync service signal handlers
    # ------------------------------------------------------------------ #
    def _on_sync_status(self, text: str) -> None:
        self._sync_status.setText(text)

    def _on_pull_finished(self, summary: dict) -> None:
        """A cloud pull replaced the catalog: refresh views (quietly).

        ``audit_log`` is excluded from the synced tables and is never restored by
        a pull, so refreshing the audit tab here would be redundant.
        """
        self.catalog_tab.refresh()
        self.price_tab.refresh()
        if self._has_catalog():
            self._outer.setCurrentIndex(_SHELL)
            self._show_catalog()
        else:
            self._outer.setCurrentIndex(_ONBOARDING)

    def _on_conflict(self, local: dict, cloud: dict) -> None:
        """The cloud sheet moved since the last sync: ask which side wins."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Sync conflict")
        box.setText(
            "The Google sheet changed since your last sync.\n\n"
            f"Your version is revision {local.get('revision')}; "
            f"the cloud is at revision {cloud.get('revision')}.\n\n"
            "Which version would you like to keep?"
        )
        keep_local = box.addButton("Keep my version", QMessageBox.AcceptRole)
        keep_cloud = box.addButton("Keep cloud version", QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep_local:
            self._sync.resolve_conflict("local")
        elif clicked is keep_cloud:
            self._sync.resolve_conflict("cloud")
        # Closing / Escape: leave the conflict pending.

    def _on_sync_error(self, kind: str, message: str) -> None:
        if kind == "auth":
            QMessageBox.warning(
                self,
                "Reconnect needed",
                "Google sync needs to reconnect. Please use "
                "Google → Connect Google… to sign in again.\n\n"
                f"{message}",
            )
        elif kind == "sheet_missing":
            QMessageBox.warning(
                self,
                "Sheet not found",
                "The linked Google Sheet could not be found — it may have been "
                "deleted or moved. Reconnect or relink to create a new one.\n\n"
                f"{message}",
            )
        else:
            QMessageBox.warning(self, "Sync error", message)

    def _on_offline_notice(self) -> None:
        QMessageBox.information(
            self,
            "Offline",
            "You appear to be offline. Your edits are saved locally and will "
            "sync to Google automatically when the connection returns.",
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

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Join the worker thread + unregister the mutation listener on close."""
        if self._sync is not None:
            self._sync.shutdown()
        super().closeEvent(event)
