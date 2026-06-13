"""Gallery card lays swatch/name/chip out without overlap (headless)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402

from numobel.ui.delegates import ProductGalleryDelegate  # noqa: E402


def test_gallery_layout_has_no_overlap():
    card = QRect(0, 0, 148, 180)
    rects = ProductGalleryDelegate._layout(card)
    assert rects["swatch"].bottom() <= rects["name"].top()
    assert rects["name"].bottom() <= rects["chip"].top()
    assert rects["chip"].bottom() <= card.bottom()
    assert rects["swatch"].left() >= card.left()
    assert rects["swatch"].right() <= card.right()
