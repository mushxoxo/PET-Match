"""Custom item delegates for the clay catalog and detail panel.

These paint rounded thumbnails, chips, and soft shadows directly with
``QPainter`` so hundreds of rows/cards cost nothing extra (no per-widget
graphics effects). All colors are pulled live from ``theme.current_palette()``
so a theme toggle is picked up on the next repaint.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from numobel.ui import theme, widgets

# Roles shared with catalog_tab's results model.
ID_ROLE = Qt.UserRole + 1
NAME_ROLE = Qt.UserRole + 2
BRAND_ROLE = Qt.UserRole + 3
SUB_ROLE = Qt.UserRole + 4   # "shade · thickness" muted line
SEED_ROLE = Qt.UserRole + 5  # swatch seed (color name or sku)
COLOR_ROLE = Qt.UserRole + 6  # resolved swatch color (#rrggbb), optional

# Roles used by the similar-color list (mirrors detail_panel's status role).
SIM_STATUS_ROLE = Qt.UserRole + 3
SIM_KIND_ROLE = Qt.UserRole + 4


def _chip(painter: QPainter, rect: QRect, text: str, pal: theme.Palette) -> int:
    """Paint a small pill chip; return its right edge x."""
    if not text:
        return rect.left()
    metrics = painter.fontMetrics()
    pad = 8
    width = metrics.horizontalAdvance(text) + pad * 2
    chip_rect = QRect(rect.left(), rect.top(), width, rect.height())
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(pal.accent_soft))
    painter.drawRoundedRect(QRectF(chip_rect), 8, 8)
    painter.setPen(QPen(QColor(pal.text)))
    painter.drawText(chip_rect, Qt.AlignCenter, text)
    return chip_rect.right()


class ProductListDelegate(QStyledItemDelegate):
    """A catalog row: swatch + bold name + brand chip + muted sub-line."""

    _HEIGHT = 64
    _THUMB = 44

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self._HEIGHT)

    def paint(self, painter, option, index) -> None:  # noqa: N802
        pal = theme.current_palette()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(6, 4, -6, -4)
        if option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal.selection_bg))
            painter.drawRoundedRect(QRectF(rect), 12, 12)

        # Thumbnail / swatch.
        seed = index.data(SEED_ROLE) or index.data(NAME_ROLE) or ""
        color_hex = index.data(COLOR_ROLE)
        color = QColor(color_hex) if color_hex else None
        thumb = widgets.swatch_pixmap(str(seed), self._THUMB, pal, color=color)
        ty = rect.top() + (rect.height() - self._THUMB) // 2
        painter.drawPixmap(rect.left() + 8, ty, thumb)

        text_left = rect.left() + 8 + self._THUMB + 12
        name = str(index.data(NAME_ROLE) or "")
        sub = str(index.data(SUB_ROLE) or "")
        brand = str(index.data(BRAND_ROLE) or "")

        # Name (bold).
        name_font = QFont(option.font)
        name_font.setBold(True)
        name_font.setPixelSize(14)
        painter.setFont(name_font)
        painter.setPen(QPen(QColor(pal.text)))
        name_rect = QRect(text_left, rect.top() + 8, rect.right() - text_left - 8, 20)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, name)

        # Brand chip + muted sub-line.
        chip_font = QFont(option.font)
        chip_font.setPixelSize(11)
        painter.setFont(chip_font)
        sub_rect = QRect(text_left, rect.bottom() - 26, rect.right() - text_left - 8, 18)
        chip_end = _chip(painter, sub_rect, brand, pal)
        if sub:
            painter.setPen(QPen(QColor(pal.text_muted)))
            muted_rect = QRect(chip_end + 8, sub_rect.top(), rect.right() - chip_end - 16, 18)
            painter.drawText(muted_rect, Qt.AlignLeft | Qt.AlignVCenter, sub)

        painter.restore()


class ProductGalleryDelegate(QStyledItemDelegate):
    """A catalog card: swatch on top, name + brand chip below, soft shadow."""

    _W = 168
    _H = 188
    _PAD = 10

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self._W, self._H)

    def paint(self, painter, option, index) -> None:  # noqa: N802
        pal = theme.current_palette()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        card = option.rect.adjusted(self._PAD, self._PAD, -self._PAD, -self._PAD)

        # Painted soft shadow (offset, low alpha).
        shadow = QColor(pal.shadow)
        shadow.setAlpha(min(70, pal.shadow_alpha + 25))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(QRectF(card.translated(0, 4)), 16, 16)

        # Card body.
        selected = bool(option.state & QStyle.State_Selected)
        painter.setBrush(QColor(pal.surface_raised))
        painter.setPen(QPen(QColor(pal.accent if selected else pal.border),
                             2 if selected else 1))
        painter.drawRoundedRect(QRectF(card), 16, 16)

        # Swatch.
        thumb = card.width() - 24
        seed = index.data(SEED_ROLE) or index.data(NAME_ROLE) or ""
        color_hex = index.data(COLOR_ROLE)
        color = QColor(color_hex) if color_hex else None
        pix = widgets.swatch_pixmap(str(seed), thumb, pal, color=color)
        painter.drawPixmap(card.left() + 12, card.top() + 12, pix)

        # Name.
        name = str(index.data(NAME_ROLE) or "")
        brand = str(index.data(BRAND_ROLE) or "")
        name_font = QFont(option.font)
        name_font.setBold(True)
        name_font.setPixelSize(13)
        painter.setFont(name_font)
        painter.setPen(QPen(QColor(pal.text)))
        ny = card.top() + 12 + thumb + 6
        name_rect = QRect(card.left() + 12, ny, card.width() - 24, 18)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, name)

        # Brand chip.
        chip_font = QFont(option.font)
        chip_font.setPixelSize(11)
        painter.setFont(chip_font)
        chip_rect = QRect(card.left() + 12, card.bottom() - 22, card.width() - 24, 18)
        _chip(painter, chip_rect, brand, pal)

        painter.restore()


class SimilarColorDelegate(QStyledItemDelegate):
    """Mini-row for the similar-colors list: swatch + label + status tint.

    Uses the item's existing display text (unchanged) so the smoke tests that
    assert on item text keep passing.
    """

    _HEIGHT = 40
    _THUMB = 26

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self._HEIGHT)

    def paint(self, painter, option, index) -> None:  # noqa: N802
        pal = theme.current_palette()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(4, 3, -4, -3)
        if option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal.selection_bg))
            painter.drawRoundedRect(QRectF(rect), 10, 10)

        text = str(index.data(Qt.DisplayRole) or "")
        kind = index.data(SIM_KIND_ROLE)

        if kind == "member":
            seed = text.split()[-1] if text else text
            color_hex = index.data(COLOR_ROLE)
            color = QColor(color_hex) if color_hex else None
            thumb = widgets.swatch_pixmap(str(seed), self._THUMB, pal, color=color)
            ty = rect.top() + (rect.height() - self._THUMB) // 2
            painter.drawPixmap(rect.left() + 6, ty, thumb)
            text_left = rect.left() + 6 + self._THUMB + 10
            painter.setPen(QPen(QColor(pal.text)))
        else:
            text_left = rect.left() + 10
            painter.setPen(QPen(QColor(pal.text_muted)))

        painter.setFont(option.font)
        painter.drawText(
            QRect(text_left, rect.top(), rect.right() - text_left - 6, rect.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            text,
        )
        painter.restore()
