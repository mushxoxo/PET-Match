"""Tests for reusable clay widgets (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from numobel.ui import widgets  # noqa: E402
from numobel.ui.widgets import Card  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_swatch_color_is_deterministic():
    a = widgets._swatch_color("Aqua")
    b = widgets._swatch_color("Aqua")
    c = widgets._swatch_color("Crimson")
    assert a.rgb() == b.rgb()
    assert a.rgb() != c.rgb()


def test_swatch_pixmap_size_and_non_null(app):
    pix = widgets.swatch_pixmap("Aqua", 72)
    assert not pix.isNull()
    assert pix.width() == 72 and pix.height() == 72


def test_view_toggle_reports_and_sets_mode(app):
    toggle = widgets.ViewToggle()
    seen = []
    toggle.changed.connect(seen.append)
    assert toggle.current() == "list"
    toggle.set_current("gallery")
    assert toggle.current() == "gallery"
    assert seen and seen[-1] == "gallery"


def test_card_property(app):
    assert Card().property("class") == "Card"
    assert Card(raised=True).property("class") == "CardRaised"


def test_make_chip_property(app):
    chip = widgets.make_chip("Terracotta")
    assert chip.property("class") == "Chip"
    assert chip.text() == "Terracotta"


def test_swatch_pixmap_uses_explicit_color(app):
    from PySide6.QtGui import QColor

    # Empty seed -> no initials drawn, so the center pixel is the pure fill.
    pix = widgets.swatch_pixmap("", 40, color=QColor("#ff0000"))
    center = pix.toImage().pixelColor(20, 20)
    assert (center.red(), center.green(), center.blue()) == (255, 0, 0)


def test_view_toggle_single_button_flips_mode(app):
    toggle = widgets.ViewToggle()
    assert toggle.current() == "list"
    toggle._btn.click()
    assert toggle.current() == "gallery"
    toggle._btn.click()
    assert toggle.current() == "list"
    assert not toggle._btn.icon().isNull()
