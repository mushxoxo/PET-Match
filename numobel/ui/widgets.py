"""Small reusable building blocks for the clay UI.

``Card`` (shadowed frame), ``make_chip`` (pill label), ``swatch_pixmap``
(deterministic generated color placeholder), and ``ViewToggle`` (single-button
List/Gallery toggle). Each is independently testable.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
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


def _chevron_icon(color: QColor, *, down: bool, size: int = 12) -> QIcon:
    """A small painted chevron (down = expanded, right = collapsed)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        m = size / 2
        if down:
            pts = [QPointF(3, m - 1), QPointF(m, m + 2), QPointF(size - 3, m - 1)]
        else:
            pts = [QPointF(m - 1, 3), QPointF(m + 2, m), QPointF(m - 1, size - 3)]
        painter.drawPolyline(QPolygonF(pts))
    finally:
        painter.end()
    return QIcon(pix)


class CollapsibleSection(QWidget):
    """A titled section whose body can be collapsed.

    Header is a painted-chevron + title button; clicking toggles the body. Use
    :meth:`add_widget` / :meth:`add_layout` to fill the body. Emits
    ``toggled(expanded)``.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
    ):
        super().__init__(parent)
        self._expanded = expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QToolButton()
        self._header.setText(title)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setProperty("class", "SectionHeader")
        self._header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._header.setIconSize(QSize(12, 12))
        self._header.clicked.connect(self._on_click)
        outer.addWidget(self._header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 8, 0, 0)
        outer.addWidget(self._body)

        self._apply()

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._body_layout.addLayout(layout)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, value: bool) -> None:
        value = bool(value)
        if value == self._expanded:
            return
        self._expanded = value
        self._apply()
        self.toggled.emit(self._expanded)

    def _on_click(self) -> None:
        self.set_expanded(not self._expanded)

    def _apply(self) -> None:
        self._body.setVisible(self._expanded)
        color = QColor(theme.current_palette().text)
        self._header.setIcon(_chevron_icon(color, down=self._expanded))
