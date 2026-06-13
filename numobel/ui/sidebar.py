"""Always-expanded sidebar nav rail with a footer (theme toggle + add)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from numobel.ui import theme


class Sidebar(QFrame):
    """Vertical nav: one checkable pill per page + footer actions.

    ``items`` is a list of ``(glyph, label)`` tuples, one per page.
    """

    page_changed = Signal(int)
    add_requested = Signal()
    theme_toggle_requested = Signal()

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("class", "Sidebar")
        self.setFixedWidth(184)
        theme.add_soft_shadow(self, blur=22, dx=2, dy=0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.page_changed.emit)

        for idx, (glyph, label) in enumerate(items):
            btn = QPushButton(f"  {glyph}   {label}")
            btn.setCheckable(True)
            btn.setProperty("class", "SidebarItem")
            if idx == 0:
                btn.setChecked(True)
            self._group.addButton(btn, idx)
            layout.addWidget(btn)

        layout.addStretch(1)

        theme_btn = QPushButton("  ◐   Theme")
        theme_btn.setProperty("class", "SidebarItem")
        theme_btn.clicked.connect(self.theme_toggle_requested.emit)
        layout.addWidget(theme_btn)

        add_btn = QPushButton("+ Add Color")
        add_btn.setProperty("class", "AccentButton")
        add_btn.clicked.connect(self.add_requested.emit)
        layout.addWidget(add_btn)

    def set_current_index(self, index: int) -> None:
        button = self._group.button(index)
        if button is not None:
            button.setChecked(True)
