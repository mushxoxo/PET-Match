"""Token-driven light/dark theme for the NUMOBEL desktop GUI.

A single :class:`Palette` of named role colors drives one templated Qt Style
Sheet via :func:`build_qss`. The module stays presentational and free of any
database import: callers read/write the persisted ``'theme'`` setting.

Public API (preserved): ``apply_theme``, ``next_theme``, ``qss_for``,
``THEMES``, ``DEFAULT_THEME``. New: ``Palette``, ``LIGHT``, ``DARK``,
``build_qss``, ``current_palette``, ``add_soft_shadow``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Supported theme names, in toggle order.
THEMES: tuple[str, str] = ("light", "dark")

#: Theme used when no preference is known or a name is unrecognised.
DEFAULT_THEME: str = "light"


@dataclass(frozen=True)
class Palette:
    """Named color roles for one theme. All colors are ``#rrggbb`` strings."""

    name: str
    bg: str             # app background (tinted)
    surface: str        # raised card surface
    surface_raised: str # the brightest surface (hero image card, dialogs)
    text: str           # primary text
    text_muted: str     # secondary text / labels
    border: str         # hairline borders
    accent: str         # primary accent (terracotta)
    accent_hover: str
    accent_pressed: str
    accent_soft: str    # low-saturation accent fill for selection
    selection_bg: str   # list/table selection fill
    selection_fg: str   # list/table selection text
    shadow: str         # drop-shadow color (#rrggbb)
    shadow_alpha: int   # drop-shadow alpha 0-255


# Accent token (terracotta). Swapping the project accent = editing these four
# values; sage (#7c9473/...) or blue (#5b7fb3/...) are trivial alternates.
LIGHT = Palette(
    name="light",
    bg="#ede6dd",
    surface="#f8f4ef",
    surface_raised="#ffffff",
    text="#3a322c",
    text_muted="#8a7c70",
    border="#ded4c8",
    accent="#c8714e",
    accent_hover="#d17e5b",
    accent_pressed="#a95c3c",
    accent_soft="#f0dbd0",
    selection_bg="#f0dbd0",
    selection_fg="#3a322c",
    shadow="#4a3b2e",
    shadow_alpha=45,
)

DARK = Palette(
    name="dark",
    bg="#1f1b18",
    surface="#2a2521",
    surface_raised="#322c27",
    text="#ece5dd",
    text_muted="#a89c8f",
    border="#3a332d",
    accent="#d9825e",
    accent_hover="#e28e6a",
    accent_pressed="#be6e4c",
    accent_soft="#3d2e26",
    selection_bg="#3d2e26",
    selection_fg="#ece5dd",
    shadow="#000000",
    shadow_alpha=120,
)

#: The palette most recently applied; read by delegates at paint time.
_ACTIVE: Palette = LIGHT


def _normalize(name: str) -> str:
    """Return ``name`` if it is a known theme, else :data:`DEFAULT_THEME`."""
    candidate = (name or "").strip().lower()
    return candidate if candidate in THEMES else DEFAULT_THEME


def _palette_for(name: str) -> Palette:
    return DARK if _normalize(name) == "dark" else LIGHT


def current_palette() -> Palette:
    """Return the palette of the theme currently applied."""
    return _ACTIVE


def build_qss(p: Palette) -> str:
    """Build the full Qt Style Sheet for palette ``p``.

    Component variants are targeted by a dynamic ``class`` property (e.g.
    ``widget.setProperty("class", "Card")``) so a single template covers both
    themes.
    """
    return f"""
/* ---- Base ---- */
QWidget {{
    background-color: {p.bg};
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: #ffffff;
}}
QMainWindow, QDialog {{ background-color: {p.bg}; }}
QToolTip {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 4px 8px;
    border-radius: 8px;
}}
QLabel {{ background-color: transparent; }}

/* ---- Section headers (collapsible) ---- */
QToolButton[class="SectionHeader"] {{
    background-color: transparent;
    border: none;
    padding: 2px 0;
    font-weight: 600;
    color: {p.text};
}}
QToolButton[class="SectionHeader"]:hover {{ color: {p.accent}; }}

/* ---- Cards ---- */
QFrame[class="Card"] {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 18px;
}}
QFrame[class="CardRaised"] {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: 18px;
}}

/* ---- Inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 12px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: #ffffff;
}}
QLineEdit[class="SearchField"] {{
    border-radius: 14px;
    padding: 9px 14px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background-color: {p.bg};
    color: {p.text_muted};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 10px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 12px;
    padding: 7px 16px;
}}
QPushButton:hover {{ border: 1px solid {p.accent}; }}
QPushButton:pressed {{ background-color: {p.accent_soft}; }}
QPushButton:disabled {{ color: {p.text_muted}; border: 1px solid {p.border}; }}
QPushButton[class="AccentButton"] {{
    background-color: {p.accent};
    color: #ffffff;
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[class="AccentButton"]:hover {{ background-color: {p.accent_hover}; }}
QPushButton[class="AccentButton"]:pressed {{ background-color: {p.accent_pressed}; }}
QPushButton[class="Pill"] {{ border-radius: 14px; padding: 6px 14px; }}

/* ---- Chips / pills (labels) ---- */
QLabel[class="Chip"] {{
    background-color: {p.accent_soft};
    color: {p.text};
    border-radius: 9px;
    padding: 2px 9px;
}}

/* ---- Sidebar ---- */
QFrame[class="Sidebar"] {{
    background-color: {p.surface};
    border: none;
    border-right: 1px solid {p.border};
}}
QPushButton[class="SidebarItem"] {{
    background-color: transparent;
    border: none;
    border-radius: 12px;
    padding: 10px 14px;
    text-align: left;
    color: {p.text_muted};
}}
QPushButton[class="SidebarItem"]:hover {{ background-color: {p.accent_soft}; color: {p.text}; }}
QPushButton[class="SidebarItem"]:checked {{
    background-color: {p.accent_soft};
    color: {p.text};
    font-weight: 600;
}}

/* ---- Tables ---- */
QTableView {{
    background-color: {p.surface};
    alternate-background-color: {p.bg};
    color: {p.text};
    gridline-color: {p.border};
    border: 1px solid {p.border};
    border-radius: 14px;
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_fg};
}}
QTableView::item:selected {{ background-color: {p.selection_bg}; color: {p.selection_fg}; }}
QHeaderView::section {{
    background-color: {p.surface};
    color: {p.text_muted};
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid {p.border};
}}
QTableCornerButton::section {{ background-color: {p.surface}; border: none; }}

/* ---- Lists ---- */
QListView, QListWidget {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 14px;
    outline: none;
}}
QListView::item, QListWidget::item {{ border-radius: 10px; }}
QListView::item:selected, QListWidget::item:selected {{
    background-color: {p.selection_bg};
    color: {p.selection_fg};
}}
QListView::item:hover, QListWidget::item:hover {{ background-color: {p.accent_soft}; }}

/* ---- Scrollbars ---- */
QScrollArea {{ border: none; background-color: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.border};
    border-radius: 6px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- Menus ---- */
QMenuBar {{ background-color: {p.bg}; color: {p.text}; }}
QMenuBar::item:selected {{ background-color: {p.accent_soft}; border-radius: 6px; }}
QMenu {{ background-color: {p.surface_raised}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 10px; }}
QMenu::item:selected {{ background-color: {p.accent_soft}; }}

/* ---- Splitter ---- */
QSplitter::handle {{ background-color: transparent; }}
"""


def qss_for(name: str) -> str:
    """Return the QSS string for theme ``name`` (unknown -> light)."""
    return build_qss(_palette_for(name))


def apply_theme(app: Any, name: str) -> str:
    """Apply theme ``name`` to ``app``; return the normalized name applied.

    Also records the active palette so :func:`current_palette` and custom
    delegates read live tokens at paint time.
    """
    global _ACTIVE
    applied = _normalize(name)
    _ACTIVE = _palette_for(applied)
    app.setStyleSheet(qss_for(applied))
    return applied


def next_theme(name: str) -> str:
    """Return the opposite theme of ``name`` (light <-> dark)."""
    return "dark" if _normalize(name) == "light" else "light"


def add_soft_shadow(
    widget: Any,
    palette: Palette | None = None,
    *,
    blur: int = 28,
    dx: int = 0,
    dy: int = 8,
) -> Any:
    """Attach a soft :class:`QGraphicsDropShadowEffect` to ``widget``.

    A widget holds only one graphics effect, so apply this only to large
    containers (cards, sidebar, dialogs) — never to many small items. Returns
    the effect (best-effort; returns ``None`` if Qt rejects it).
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    pal = palette or current_palette()
    try:
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setXOffset(dx)
        effect.setYOffset(dy)
        color = QColor(pal.shadow)
        color.setAlpha(pal.shadow_alpha)
        effect.setColor(color)
        widget.setGraphicsEffect(effect)
        return effect
    except Exception:
        return None
