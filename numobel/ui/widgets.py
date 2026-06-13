"""Small reusable building blocks for the clay UI.

``Card`` (shadowed frame), ``make_chip`` (pill label), ``swatch_pixmap``
(deterministic generated color placeholder), and ``ViewToggle`` (segmented
List/Gallery control). Each is independently testable.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from numobel.ui import theme

# Cache generated swatches by (seed, size); pastel color is theme-independent.
_swatch_cache: dict[tuple[str, int, int], QPixmap] = {}


class Card(QFrame):
    """A rounded surface frame, optionally with a soft drop shadow."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        raised: bool = False,
        shadow: bool = True,
    ):
        super().__init__(parent)
        self.setProperty("class", "CardRaised" if raised else "Card")
        if shadow:
            theme.add_soft_shadow(self)


def make_chip(text: str) -> QLabel:
    """Return a pill-styled label (used for brand / shade / thickness chips)."""
    label = QLabel(text)
    label.setProperty("class", "Chip")
    return label


def _swatch_color(seed: str) -> QColor:
    """Map ``seed`` deterministically to a pleasant pastel ``QColor``."""
    digest = hashlib.md5((seed or "").encode("utf-8")).hexdigest()
    hue = int(digest[:2], 16) * 360 // 256
    color = QColor()
    color.setHsv(hue, 90, 205)
    return color


def _initials(seed: str) -> str:
    parts = [w for w in (seed or "").split() if w]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def swatch_pixmap(
    seed: str,
    size: int = 64,
    palette: theme.Palette | None = None,
    color: QColor | None = None,
) -> QPixmap:
    """Return a deterministic rounded swatch for ``seed``.

    When ``color`` is given it is used as the fill; otherwise the fill is the
    deterministic hash color of ``seed``. Results are cached by
    ``(seed, size, fill)``.
    """
    size = max(1, int(size))
    fill = color if color is not None else _swatch_color(seed)
    key = (seed or "", size, fill.rgba())
    cached = _swatch_cache.get(key)
    if cached is not None:
        return cached

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill))
        radius = size * 0.28
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

        initials = _initials(seed)
        if initials:
            painter.setPen(QColor(60, 50, 44, 150))
            font = QFont()
            font.setPixelSize(max(10, int(size * 0.34)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pix.rect(), Qt.AlignCenter, initials)
    finally:
        painter.end()

    _swatch_cache[key] = pix
    return pix


class ViewToggle(QWidget):
    """Segmented List/Gallery control. Emits ``changed(mode)``."""

    changed = Signal(str)  # "list" or "gallery"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._list_btn = self._segment("List")
        self._gallery_btn = self._segment("Gallery")
        self._list_btn.setChecked(True)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self._list_btn, 0)
        self._group.addButton(self._gallery_btn, 1)
        self._group.idClicked.connect(self._on_clicked)

        row.addWidget(self._list_btn)
        row.addWidget(self._gallery_btn)

    def _segment(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setProperty("class", "Segment")
        return btn

    def _on_clicked(self, idx: int) -> None:
        self.changed.emit("gallery" if idx == 1 else "list")

    def current(self) -> str:
        return "gallery" if self._gallery_btn.isChecked() else "list"

    def set_current(self, mode: str) -> None:
        gallery = mode == "gallery"
        (self._gallery_btn if gallery else self._list_btn).setChecked(True)
        self.changed.emit("gallery" if gallery else "list")
