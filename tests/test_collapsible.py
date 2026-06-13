"""CollapsibleSection toggle behavior (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from numobel.ui import widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_collapsed_by_default_hides_body(app):
    section = widgets.CollapsibleSection("Details", expanded=False)
    assert section.is_expanded() is False
    assert section._body.isHidden()


def test_toggle_shows_body_and_emits(app):
    section = widgets.CollapsibleSection("Details", expanded=False)
    seen = []
    section.toggled.connect(seen.append)
    section.set_expanded(True)
    assert section.is_expanded() is True
    assert not section._body.isHidden()
    assert seen == [True]


def test_add_widget_lands_in_body(app):
    section = widgets.CollapsibleSection("Similar", expanded=True)
    label = QLabel("hi")
    section.add_widget(label)
    assert label.parent() is section._body
