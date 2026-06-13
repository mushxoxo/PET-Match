"""Token-system tests for the redesigned theme module (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from numobel.ui import theme  # noqa: E402

_REQUIRED_ROLES = [
    "bg",
    "surface",
    "surface_raised",
    "text",
    "text_muted",
    "border",
    "accent",
    "accent_hover",
    "accent_pressed",
    "accent_soft",
    "selection_bg",
    "selection_fg",
    "shadow",
    "shadow_alpha",
]


def test_palettes_define_every_role():
    for pal in (theme.LIGHT, theme.DARK):
        for role in _REQUIRED_ROLES:
            value = getattr(pal, role)
            assert value not in (None, ""), f"{pal.name}.{role} missing"


def test_build_qss_contains_selectors_and_accent():
    qss = theme.build_qss(theme.LIGHT)
    assert isinstance(qss, str) and qss.strip()
    for selector in ("QWidget", "QPushButton", "QTableView", "QListView"):
        assert selector in qss
    assert theme.LIGHT.accent in qss


def test_qss_for_uses_palettes_and_public_api_preserved():
    assert theme.qss_for("dark") == theme.build_qss(theme.DARK)
    assert theme.qss_for("light") == theme.build_qss(theme.LIGHT)
    assert theme.THEMES == ("light", "dark")
    assert theme.DEFAULT_THEME == "light"
    assert theme.next_theme("light") == "dark"
    assert theme.next_theme("dark") == "light"


def test_apply_theme_sets_current_palette():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert theme.apply_theme(app, "dark") == "dark"
    assert theme.current_palette() is theme.DARK
    assert theme.apply_theme(app, "light") == "light"
    assert theme.current_palette() is theme.LIGHT


def test_add_soft_shadow_attaches_effect():
    from PySide6.QtWidgets import QApplication, QFrame

    QApplication.instance() or QApplication([])
    frame = QFrame()
    effect = theme.add_soft_shadow(frame, theme.LIGHT)
    assert frame.graphicsEffect() is effect


def test_qlabel_is_transparent_in_qss():
    assert "QLabel { background-color: transparent; }" in theme.build_qss(theme.LIGHT)


def test_section_header_styled_in_qss():
    assert 'QToolButton[class="SectionHeader"]' in theme.build_qss(theme.DARK)
