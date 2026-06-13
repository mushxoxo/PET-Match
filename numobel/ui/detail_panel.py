"""Product detail panel (read-only, Phase A)."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from numobel import search

# Role storing the navigable product id on a similar-color list item.
_NAV_ROLE = Qt.UserRole + 1

_THUMB_MAX = 220


class DetailPanel(QWidget):
    """Shows full detail for a single product, plus its similar colors."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        on_navigate: Optional[Callable[[int], None]] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._conn = conn
        self._on_navigate = on_navigate
        self._product_id: Optional[int] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)

        # --- Empty-state label ---
        self._empty_label = QLabel("Select a product to view details.")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: gray;")
        self._layout.addWidget(self._empty_label)

        # --- Image ---
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumHeight(_THUMB_MAX)
        self._image_label.setFrameShape(QFrame.StyledPanel)
        self._layout.addWidget(self._image_label)

        # --- Title (brand + color) ---
        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self._layout.addWidget(self._title_label)

        # --- Field form ---
        self._fields_box = QGroupBox("Details")
        self._form = QFormLayout(self._fields_box)
        self._form.setLabelAlignment(Qt.AlignRight)
        self._layout.addWidget(self._fields_box)

        # --- Similar colors ---
        self._similar_box = QGroupBox("Similar Colors")
        sim_layout = QVBoxLayout(self._similar_box)
        self._similar_hint = QLabel(
            "Double-click a resolved color to jump to it."
        )
        self._similar_hint.setStyleSheet("color: gray; font-size: 11px;")
        sim_layout.addWidget(self._similar_hint)
        self._similar_list = QListWidget()
        self._similar_list.itemDoubleClicked.connect(self._on_similar_double)
        sim_layout.addWidget(self._similar_list)
        self._layout.addWidget(self._similar_box)

        self._layout.addStretch(1)

        self._set_content_visible(False)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def set_product(self, product_id: Optional[int]) -> None:
        """Populate the panel for ``product_id`` (or clear if None/missing)."""
        if product_id is None:
            self._show_empty()
            return

        product = search.get_product(self._conn, product_id)
        if product is None:
            self._show_empty()
            return

        self._product_id = product_id
        self._set_content_visible(True)

        self._populate_title(product)
        self._populate_fields(product)
        self._populate_image(product)
        self._populate_similar(product_id)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _set_content_visible(self, visible: bool) -> None:
        self._empty_label.setVisible(not visible)
        self._image_label.setVisible(visible)
        self._title_label.setVisible(visible)
        self._fields_box.setVisible(visible)
        self._similar_box.setVisible(visible)

    def _show_empty(self) -> None:
        self._product_id = None
        self._set_content_visible(False)

    def _populate_title(self, product: sqlite3.Row) -> None:
        brand = (product["brand_code"] or "").strip()
        name = (product["brand_name"] or "").strip()
        color = (product["color_name"] or "(unnamed color)").strip()
        brand_str = brand
        if name:
            brand_str = f"{brand} — {name}" if brand else name
        self._title_label.setText(f"{color}\n{brand_str}".strip())

    def _populate_fields(self, product: sqlite3.Row) -> None:
        # Clear existing rows.
        while self._form.rowCount():
            self._form.removeRow(0)

        def add(label: str, value) -> None:
            text = "" if value is None else str(value).strip()
            if text == "":
                text = "—"
            val_label = QLabel(text)
            val_label.setWordWrap(True)
            val_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._form.addRow(f"{label}:", val_label)

        add("Brand", self._brand_text(product))
        add("SKU", product["sku"])
        add("Color Name", product["color_name"])
        add("Shade No", product["shade_no"])
        add(
            "Thickness",
            "" if product["thickness"] is None else f"{product['thickness']:g}",
        )
        add("Category", product["category"])
        add("Self Label", product["self_label"])

        # Extra JSON -> key: value lines (guarded).
        extra = product["extra_json"]
        for key, value in self._parse_extra(extra).items():
            add(str(key), value)

    def _brand_text(self, product: sqlite3.Row) -> str:
        code = (product["brand_code"] or "").strip()
        name = (product["brand_name"] or "").strip()
        if code and name:
            return f"{code} ({name})"
        return code or name

    @staticmethod
    def _parse_extra(extra) -> dict:
        if not extra:
            return {}
        try:
            data = json.loads(extra)
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _populate_image(self, product: sqlite3.Row) -> None:
        path = product["image_path"]
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    _THUMB_MAX,
                    _THUMB_MAX,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self._image_label.setPixmap(scaled)
                self._image_label.setText("")
                return
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText("No photo")
        self._image_label.setStyleSheet("color: gray;")

    def _populate_similar(self, product_id: int) -> None:
        self._similar_list.clear()
        links = search.get_similar_colors(self._conn, product_id)
        if not links:
            placeholder = QListWidgetItem("No similar colors recorded.")
            placeholder.setFlags(Qt.NoItemFlags)
            self._similar_list.addItem(placeholder)
            return

        for link in links:
            status = link["status"]
            label = link["other_label"] or link["raw_ref"] or "(unknown)"
            text = f"{label}  ({status})"
            item = QListWidgetItem(text)

            other_id = link["other_product_id"]
            if status == "resolved" and other_id is not None:
                item.setData(_NAV_ROLE, other_id)
                item.setToolTip("Double-click to open this color")
            elif status == "external":
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                item.setForeground(Qt.gray)
            else:  # unresolved
                item.setForeground(Qt.darkYellow)

            self._similar_list.addItem(item)

    def _on_similar_double(self, item: QListWidgetItem) -> None:
        other_id = item.data(_NAV_ROLE)
        if other_id is not None and self._on_navigate is not None:
            self._on_navigate(int(other_id))
