"""Small reusable building blocks for the clay UI.

``Card`` (shadowed frame), ``make_chip`` (pill label), ``swatch_pixmap``
(deterministic generated color placeholder), and ``ViewToggle`` (segmented
List/Gallery control). Each is independently testable.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
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


def _paint_icon(draw, color: QColor, size: int = 18) -> QIcon:
    """Build a QIcon by running ``draw(painter, color, size)`` on a pixmap."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        draw(painter, color, size)
    finally:
        painter.end()
    return QIcon(pix)


def _draw_list(painter: QPainter, color: QColor, size: int) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(color))
    bar_h = max(2, size // 7)
    gap = (size - 3 * bar_h) / 4
    for i in range(3):
        y = gap + i * (bar_h + gap)
        painter.drawRoundedRect(QRectF(1, y, size - 2, bar_h), 1.5, 1.5)


def _draw_grid(painter: QPainter, color: QColor, size: int) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(color))
    cell = (size - 3) / 2
    for cx in (1, 2 + cell):
        for cy in (1, 2 + cell):
            painter.drawRoundedRect(QRectF(cx, cy, cell, cell), 2, 2)


class ViewToggle(QWidget):
    """Single icon button that flips between list and gallery. Emits ``changed``.

    Public API preserved from the old segmented control: ``changed(mode)``,
    ``current()``, ``set_current(mode)`` with ``mode`` in ``{"list","gallery"}``.
    The button shows the *current* mode's icon; its tooltip names the target.
    """

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = "list"

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._btn = QToolButton()
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setIconSize(QSize(18, 18))
        self._btn.clicked.connect(self._on_click)
        row.addWidget(self._btn)
        self._refresh_icon()

    def _on_click(self) -> None:
        self.set_current("gallery" if self._mode == "list" else "list")

    def current(self) -> str:
        return self._mode

    def set_current(self, mode: str) -> None:
        self._mode = "gallery" if mode == "gallery" else "list"
        self._refresh_icon()
        self.changed.emit(self._mode)

    def _refresh_icon(self) -> None:
        color = QColor(theme.current_palette().text)
        if self._mode == "gallery":
            self._btn.setIcon(_paint_icon(_draw_grid, color))
            self._btn.setToolTip("Switch to list view")
        else:
            self._btn.setIcon(_paint_icon(_draw_list, color))
            self._btn.setToolTip("Switch to gallery view")
