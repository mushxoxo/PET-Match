"""Headless tests for the LinkSpreadsheetDialog (Qt offscreen)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel.ui.link_spreadsheet_dialog import LinkSpreadsheetDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_default_choice_is_create_new(app):
    dlg = LinkSpreadsheetDialog()
    assert dlg.selected_choice() == ""


def test_set_spreadsheets_populates_list(app):
    dlg = LinkSpreadsheetDialog()
    dlg.set_spreadsheets(
        [
            {"id": "a", "name": "Alpha", "modifiedTime": "2024"},
            {"id": "b", "name": "Beta", "modifiedTime": ""},
        ]
    )
    assert dlg._list.count() == 2
    dlg._list.setCurrentRow(0)
    assert dlg.selected_choice() == "a"


def test_paste_field_wins(app):
    dlg = LinkSpreadsheetDialog()
    dlg.set_spreadsheets(
        [{"id": "a", "name": "Alpha", "modifiedTime": "2024"}]
    )
    dlg._list.setCurrentRow(0)
    dlg._paste.setText("pasted-id")
    assert dlg.selected_choice() == "pasted-id"


def test_set_spreadsheets_empty_shows_placeholder_row(app):
    dlg = LinkSpreadsheetDialog()
    dlg.set_spreadsheets([])
    # No selectable real rows -> still create-new.
    assert dlg.selected_choice() == ""
