"""Application entry point for the NUMOBEL desktop GUI."""

from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from numobel import db


def main() -> int:
    """Create the QApplication, open the DB, and show the main window.

    If the database file is missing, show a message box telling the user to
    run the importer first, then exit gracefully.
    """
    app = QApplication(sys.argv)

    if not os.path.isfile(db.DEFAULT_DB_PATH):
        QMessageBox.critical(
            None,
            "Database not found",
            "No catalog database was found at:\n"
            f"{db.DEFAULT_DB_PATH}\n\n"
            "Please import the catalog first by running:\n"
            "    python -m numobel.importer.run_import",
        )
        return 1

    conn = db.connect()

    # Imported lazily so a missing DB exits before importing the UI stack.
    from numobel.ui.main_window import MainWindow

    window = MainWindow(conn)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
