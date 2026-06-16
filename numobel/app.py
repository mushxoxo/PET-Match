"""Application entry point for the NUMOBEL desktop GUI."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from numobel import db


def main() -> int:
    """Create the QApplication, open the DB, and show the main window.

    The database is created on demand: ``connect()`` makes the file if absent
    and ``migrate()`` (via ``create_schema()``) lays down the schema, so a
    fresh install opens to an empty catalog and the onboarding screen prompts
    the user to import a workbook.
    """
    app = QApplication(sys.argv)

    conn = db.connect()
    # Create the schema on a fresh DB and bring older databases up to the
    # current one (adds color groups and folds legacy resolved links into
    # transitive color families).
    db.migrate(conn)

    from numobel.sync import state
    from numobel.sync.service import SyncService
    from numobel.ui import theme
    from numobel.ui.main_window import MainWindow

    # Apply the persisted theme (defaults to light) before showing any UI.
    theme.apply_theme(app, db.get_setting(conn, "theme", theme.DEFAULT_THEME))

    # The service imports google lazily only when actually connecting, so
    # constructing it here keeps app import google-free.
    sync = SyncService(db.DEFAULT_DB_PATH)
    window = MainWindow(conn, sync_service=sync)
    window.show()

    # Startup sync (data-loss-safe): only when the catalog is already linked.
    # Pending local edits are pushed up (which conflict-prompts if the cloud
    # also moved) rather than silently discarded by a pull.
    if state.is_linked(conn):
        if state.is_pending(conn):
            sync.push()
        else:
            sync.pull()

    try:
        return app.exec()
    finally:
        # Belt-and-suspenders: the window's closeEvent already shuts the
        # service down, and shutdown() is idempotent.
        sync.shutdown()


if __name__ == "__main__":
    sys.exit(main())
