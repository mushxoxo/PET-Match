"""Light/dark theme system for the NUMOBEL desktop GUI.

This module is intentionally pure and presentational: it produces Qt Style
Sheet (QSS) strings and applies them to a ``QApplication``. It does not import
the database or touch persisted settings — callers are responsible for reading
and writing the ``'theme'`` setting via :mod:`numobel.db`. Keeping it free of
side effects makes it trivial to test headlessly.

Typical usage::

    from numobel.ui import theme

    # At startup, apply the persisted theme (falling back to the default):
    applied = theme.apply_theme(app, persisted_name)

    # For a toggle action:
    new_name = theme.next_theme(current_name)
    theme.apply_theme(app, new_name)
"""

from __future__ import annotations

from typing import Any

#: Supported theme names, in toggle order.
THEMES: tuple[str, str] = ("light", "dark")

#: Theme used when no preference is known or a name is unrecognised.
DEFAULT_THEME: str = "light"


def light_qss() -> str:
    """Return the QSS string for the light theme.

    Close to Qt's native light palette, but with a consistent blue accent for
    selection and focus so toggling to/from dark is visibly distinct.
    """
    return """
/* ---- Base ---- */
QWidget {
    background-color: #f4f5f7;
    color: #1f2329;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}

/* ---- Inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    color: #1f2329;
    border: 1px solid #c2c7d0;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #2f6fed;
}
QLineEdit:disabled, QComboBox:disabled {
    background-color: #eceef1;
    color: #9aa0a8;
}

/* ---- ComboBox ---- */
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1f2329;
    border: 1px solid #c2c7d0;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: #ffffff;
    color: #1f2329;
    border: 1px solid #c2c7d0;
    border-radius: 4px;
    padding: 5px 14px;
}
QPushButton:hover {
    background-color: #eef2fb;
    border: 1px solid #9bb6f3;
}
QPushButton:pressed {
    background-color: #d9e3fb;
    border: 1px solid #2f6fed;
}
QPushButton:default {
    border: 1px solid #2f6fed;
}
QPushButton:disabled {
    background-color: #eceef1;
    color: #9aa0a8;
    border: 1px solid #d6dae0;
}

/* ---- Tables ---- */
QTableView {
    background-color: #ffffff;
    alternate-background-color: #eef1f5;
    color: #1f2329;
    gridline-color: #dde1e7;
    border: 1px solid #c2c7d0;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}
QTableView::item:selected {
    background-color: #2f6fed;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #e7eaef;
    color: #1f2329;
    padding: 5px 6px;
    border: none;
    border-right: 1px solid #d3d8df;
    border-bottom: 1px solid #c2c7d0;
}
QTableCornerButton::section {
    background-color: #e7eaef;
    border: none;
    border-bottom: 1px solid #c2c7d0;
}

/* ---- Lists ---- */
QListWidget {
    background-color: #ffffff;
    color: #1f2329;
    border: 1px solid #c2c7d0;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #2f6fed;
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: #eef2fb;
}

/* ---- GroupBox ---- */
QGroupBox {
    border: 1px solid #c2c7d0;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #41464d;
}

/* ---- Tabs ---- */
QTabWidget::pane {
    border: 1px solid #c2c7d0;
    border-radius: 4px;
    top: -1px;
}
QTabBar::tab {
    background-color: #e7eaef;
    color: #41464d;
    border: 1px solid #c2c7d0;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1f2329;
}
QTabBar::tab:hover:!selected {
    background-color: #eef2fb;
}

/* ---- Scroll areas ---- */
QScrollArea {
    border: 1px solid #c2c7d0;
    border-radius: 4px;
    background-color: #ffffff;
}
QScrollBar:vertical {
    background: #eceef1;
    width: 12px;
    margin: 0;
}
QScrollBar:horizontal {
    background: #eceef1;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #c2c7d0;
    border-radius: 6px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #a7adb6;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}

/* ---- Tooltips ---- */
QToolTip {
    background-color: #2b2f36;
    color: #f4f5f7;
    border: 1px solid #2b2f36;
    padding: 4px 6px;
    border-radius: 4px;
}
"""


def dark_qss() -> str:
    """Return the QSS string for the dark theme.

    Charcoal (~#2b2b2b) surfaces with light text and a clear blue accent for
    selection and focus, tuned for comfortable contrast.
    """
    return """
/* ---- Base ---- */
QWidget {
    background-color: #2b2b2b;
    color: #e6e6e6;
    selection-background-color: #3d7eff;
    selection-color: #ffffff;
}

/* ---- Inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #353535;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #3d7eff;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #3d7eff;
}
QLineEdit:disabled, QComboBox:disabled {
    background-color: #2f2f2f;
    color: #6f6f6f;
}

/* ---- ComboBox ---- */
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background-color: #353535;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    selection-background-color: #3d7eff;
    selection-color: #ffffff;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: #3a3a3a;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 5px 14px;
}
QPushButton:hover {
    background-color: #454545;
    border: 1px solid #5d7fc4;
}
QPushButton:pressed {
    background-color: #2f4d80;
    border: 1px solid #3d7eff;
}
QPushButton:default {
    border: 1px solid #3d7eff;
}
QPushButton:disabled {
    background-color: #313131;
    color: #6f6f6f;
    border: 1px solid #3d3d3d;
}

/* ---- Tables ---- */
QTableView {
    background-color: #303030;
    alternate-background-color: #353535;
    color: #e6e6e6;
    gridline-color: #424242;
    border: 1px solid #4a4a4a;
    selection-background-color: #3d7eff;
    selection-color: #ffffff;
}
QTableView::item:selected {
    background-color: #3d7eff;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #3a3a3a;
    color: #e6e6e6;
    padding: 5px 6px;
    border: none;
    border-right: 1px solid #2b2b2b;
    border-bottom: 1px solid #4a4a4a;
}
QTableCornerButton::section {
    background-color: #3a3a3a;
    border: none;
    border-bottom: 1px solid #4a4a4a;
}

/* ---- Lists ---- */
QListWidget {
    background-color: #303030;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #3d7eff;
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: #3c3c3c;
}

/* ---- GroupBox ---- */
QGroupBox {
    border: 1px solid #4a4a4a;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #b8b8b8;
}

/* ---- Tabs ---- */
QTabWidget::pane {
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    top: -1px;
}
QTabBar::tab {
    background-color: #333333;
    color: #b8b8b8;
    border: 1px solid #4a4a4a;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #2b2b2b;
    color: #e6e6e6;
}
QTabBar::tab:hover:!selected {
    background-color: #3c3c3c;
}

/* ---- Scroll areas ---- */
QScrollArea {
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    background-color: #303030;
}
QScrollBar:vertical {
    background: #2f2f2f;
    width: 12px;
    margin: 0;
}
QScrollBar:horizontal {
    background: #2f2f2f;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #4a4a4a;
    border-radius: 6px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #5c5c5c;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}

/* ---- Tooltips ---- */
QToolTip {
    background-color: #1d1d1d;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    padding: 4px 6px;
    border-radius: 4px;
}
"""


def _normalize(name: str) -> str:
    """Return ``name`` if it is a known theme, else :data:`DEFAULT_THEME`."""
    candidate = (name or "").strip().lower()
    return candidate if candidate in THEMES else DEFAULT_THEME


def qss_for(name: str) -> str:
    """Return the QSS string for theme ``name``.

    Unknown names fall back to the light theme.
    """
    return dark_qss() if _normalize(name) == "dark" else light_qss()


def apply_theme(app: Any, name: str) -> str:
    """Apply theme ``name`` to ``app`` and return the normalized name applied.

    ``app`` is a ``QApplication`` (typed loosely to keep this module free of a
    hard import). The returned name is normalized to a known theme so callers
    can persist exactly what was applied.
    """
    applied = _normalize(name)
    app.setStyleSheet(qss_for(applied))
    return applied


def next_theme(name: str) -> str:
    """Return the opposite theme of ``name`` (light <-> dark).

    Unknown names normalize to the default first, so the result is always a
    valid theme name.
    """
    return "dark" if _normalize(name) == "light" else "light"
