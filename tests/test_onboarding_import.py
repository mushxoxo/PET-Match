"""Headless tests for the first-run onboarding + in-app import flow.

Runs Qt offscreen (no display). Verifies that an empty database opens to the
onboarding screen and that importing the real workbook through the window's
handler populates the catalog and switches to the app shell — exercising
build-on-live-connection + migrate + tab refresh without a native dialog.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from numobel import db  # noqa: E402
from numobel.ui import main_window as mw  # noqa: E402
from numobel.ui.main_window import _ONBOARDING, _SHELL, MainWindow  # noqa: E402

EXCEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "my_excel"
    / "NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx"
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def empty_conn():
    c = db.connect(":memory:")
    db.migrate(c)  # schema only, zero products — a fresh install
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    """Stop the import handler's modal dialogs from blocking headless runs."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )


def test_empty_db_opens_onboarding(app, empty_conn):
    window = MainWindow(empty_conn)
    try:
        assert window._outer.currentIndex() == _ONBOARDING
    finally:
        window.deleteLater()


@pytest.mark.skipif(not EXCEL_PATH.is_file(), reason="real workbook not present")
def test_import_populates_and_switches_to_shell(app, empty_conn):
    window = MainWindow(empty_conn)
    try:
        assert window._outer.currentIndex() == _ONBOARDING

        window._import_catalog(path=str(EXCEL_PATH))

        assert window._outer.currentIndex() == _SHELL
        assert window._has_catalog()
        assert window.catalog_tab.results_model.rowCount() > 0
    finally:
        window.deleteLater()


@pytest.mark.skipif(not EXCEL_PATH.is_file(), reason="real workbook not present")
def test_populated_db_opens_shell(app, empty_conn):
    from numobel.importer.run_import import build

    build(excel_path=str(EXCEL_PATH), conn=empty_conn)
    db.migrate(empty_conn)

    window = MainWindow(empty_conn)
    try:
        assert window._outer.currentIndex() == _SHELL
    finally:
        window.deleteLater()


def test_failed_import_leaves_data_untouched(app, empty_conn, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *a, **k: captured.update(shown=True)),
    )
    # Point build() at a non-existent workbook so it raises.
    window = MainWindow(empty_conn)
    try:
        window._import_catalog(path=str(EXCEL_PATH.parent / "does_not_exist.xlsx"))
        assert captured.get("shown") is True
        assert window._outer.currentIndex() == _ONBOARDING
        assert not window._has_catalog()
    finally:
        window.deleteLater()
