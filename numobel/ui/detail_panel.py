"""Product detail panel (interactive, Phase B).

Adds photo attach/remove and a similar-color mapping editor on top of the
read-only Phase A panel. All writes go through :mod:`numobel.mutations`.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from numobel import db, mutations, search

# Role storing the navigable product id on a similar-color list item.
_NAV_ROLE = Qt.UserRole + 1
# Role storing the link id on a similar-color list item.
_LINK_ROLE = Qt.UserRole + 2
# Role storing the link status on a similar-color list item.
_STATUS_ROLE = Qt.UserRole + 3

# Role storing the product id on a picker-dialog result item.
_PICK_ROLE = Qt.UserRole + 1

_THUMB_MAX = 220

# Base dir holding ``numobel.db`` and ``images/`` — frozen-aware (see db.base_dir).
_REPO_ROOT = db.base_dir()
_IMAGES_DIR = db.images_dir()

_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif)"


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
        self._image_label.mouseDoubleClickEvent = self._on_image_double_click
        self._layout.addWidget(self._image_label)

        # --- Photo buttons ---
        self._photo_buttons = QWidget()
        photo_row = QHBoxLayout(self._photo_buttons)
        photo_row.setContentsMargins(0, 0, 0, 0)
        self._add_photo_btn = QPushButton("Add Photo…")
        self._add_photo_btn.clicked.connect(self._on_add_photo)
        self._remove_photo_btn = QPushButton("Remove Photo")
        self._remove_photo_btn.clicked.connect(self._on_remove_photo)
        photo_row.addWidget(self._add_photo_btn)
        photo_row.addWidget(self._remove_photo_btn)
        photo_row.addStretch(1)
        self._layout.addWidget(self._photo_buttons)

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
        self._similar_list.itemSelectionChanged.connect(
            self._update_mapping_buttons
        )
        sim_layout.addWidget(self._similar_list)

        # --- Mapping editor buttons ---
        map_row = QHBoxLayout()
        self._add_link_btn = QPushButton("Add Similar Color…")
        self._add_link_btn.clicked.connect(self._on_add_link)
        self._remove_link_btn = QPushButton("Remove")
        self._remove_link_btn.clicked.connect(self._on_remove_link)
        self._resolve_link_btn = QPushButton("Resolve…")
        self._resolve_link_btn.clicked.connect(self._on_resolve_link)
        map_row.addWidget(self._add_link_btn)
        map_row.addWidget(self._remove_link_btn)
        map_row.addWidget(self._resolve_link_btn)
        map_row.addStretch(1)
        sim_layout.addLayout(map_row)

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
        self._update_photo_buttons(product)
        self._update_mapping_buttons()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _set_content_visible(self, visible: bool) -> None:
        self._empty_label.setVisible(not visible)
        self._image_label.setVisible(visible)
        self._photo_buttons.setVisible(visible)
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

    @staticmethod
    def _resolve_image_path(path: Optional[str]) -> Optional[str]:
        """Resolve a stored image path to an absolute filesystem path.

        Relative paths are resolved against the repo root so the DB stays
        portable; absolute paths are returned unchanged (backward compat).
        """
        if not path:
            return None
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str(_REPO_ROOT / p)

    def _populate_image(self, product: sqlite3.Row) -> None:
        resolved = self._resolve_image_path(product["image_path"])
        if resolved and os.path.isfile(resolved):
            pix = QPixmap(resolved)
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
            item.setData(_LINK_ROLE, link["link_id"])
            item.setData(_STATUS_ROLE, status)

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

    # ------------------------------------------------------------------ #
    # Photo actions
    # ------------------------------------------------------------------ #
    def _update_photo_buttons(self, product: sqlite3.Row) -> None:
        has_image = bool(product["image_path"])
        self._remove_photo_btn.setEnabled(has_image)

    def _on_image_double_click(self, event) -> None:
        """Open the full image in the OS viewer (best-effort, optional)."""
        if self._product_id is None:
            return
        product = search.get_product(self._conn, self._product_id)
        if product is None:
            return
        resolved = self._resolve_image_path(product["image_path"])
        if resolved and os.path.isfile(resolved):
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl

                QDesktopServices.openUrl(QUrl.fromLocalFile(resolved))
            except Exception:
                pass

    def _on_add_photo(self) -> None:
        if self._product_id is None:
            return
        src, _ = QFileDialog.getOpenFileName(
            self, "Choose Photo", "", _IMAGE_FILTER
        )
        if not src:
            return

        basename = os.path.basename(src)
        dest_name = f"{self._product_id}_{basename}"
        _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        dest = _IMAGES_DIR / dest_name
        try:
            shutil.copyfile(src, dest)
        except OSError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return

        # Store the path relative to repo root so the DB stays portable.
        rel_path = os.path.join("images", dest_name)
        try:
            mutations.set_product_image(self._conn, self._product_id, rel_path)
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return
        self.set_product(self._product_id)

    def _on_remove_photo(self) -> None:
        if self._product_id is None:
            return
        try:
            mutations.set_product_image(self._conn, self._product_id, None)
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return
        self.set_product(self._product_id)

    # ------------------------------------------------------------------ #
    # Mapping editor actions
    # ------------------------------------------------------------------ #
    def _update_mapping_buttons(self) -> None:
        has_product = self._product_id is not None
        self._add_link_btn.setEnabled(has_product)

        item = self._similar_list.currentItem()
        link_id = None if item is None else item.data(_LINK_ROLE)
        status = None if item is None else item.data(_STATUS_ROLE)
        has_link = has_product and link_id is not None

        self._remove_link_btn.setEnabled(has_link)
        self._resolve_link_btn.setEnabled(
            has_link and status in ("unresolved", "external")
        )

    def _on_add_link(self) -> None:
        if self._product_id is None:
            return
        chosen = self._pick_product("Add Similar Color")
        if chosen is None:
            return
        try:
            mutations.add_link(self._conn, self._product_id, chosen)
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return
        self.set_product(self._product_id)

    def _on_remove_link(self) -> None:
        if self._product_id is None:
            return
        item = self._similar_list.currentItem()
        if item is None:
            return
        link_id = item.data(_LINK_ROLE)
        if link_id is None:
            return
        try:
            mutations.remove_link(self._conn, int(link_id))
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return
        self.set_product(self._product_id)

    def _on_resolve_link(self) -> None:
        if self._product_id is None:
            return
        item = self._similar_list.currentItem()
        if item is None:
            return
        link_id = item.data(_LINK_ROLE)
        if link_id is None:
            return
        chosen = self._pick_product("Resolve Similar Color")
        if chosen is None:
            return
        try:
            mutations.resolve_link(self._conn, int(link_id), chosen)
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot do that", str(err))
            return
        self.set_product(self._product_id)

    def _pick_product(self, title: str) -> Optional[int]:
        """Open the product-picker dialog and return a chosen product id."""
        dialog = _ProductPickerDialog(
            self._conn, exclude_id=self._product_id, title=title, parent=self
        )
        if dialog.exec() == QDialog.Accepted:
            return dialog.chosen_id()
        return None


class _ProductPickerDialog(QDialog):
    """Modal search-and-pick dialog for choosing a product to link."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        exclude_id: Optional[int],
        title: str = "Choose Product",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._conn = conn
        self._exclude_id = exclude_id
        self._chosen_id: Optional[int] = None

        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 460)

        layout = QVBoxLayout(self)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search products…")
        self._search_box.textChanged.connect(self._refresh_results)
        layout.addWidget(self._search_box)

        self._results = QListWidget()
        self._results.itemDoubleClicked.connect(self._on_result_double)
        self._results.itemSelectionChanged.connect(self._update_ok)
        layout.addWidget(self._results)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._refresh_results("")
        self._update_ok()

    def chosen_id(self) -> Optional[int]:
        return self._chosen_id

    def _refresh_results(self, text: str) -> None:
        self._results.clear()
        rows = search.search_products(self._conn, text or "", limit=200)
        for row in rows:
            if self._exclude_id is not None and row["id"] == self._exclude_id:
                continue
            parts = [
                row["brand_code"] or "",
                row["sku"] or "",
                row["color_name"] or "",
            ]
            label = " ".join(p for p in parts if p).strip() or "(unnamed)"
            item = QListWidgetItem(label)
            item.setData(_PICK_ROLE, row["id"])
            self._results.addItem(item)
        self._update_ok()

    def _update_ok(self) -> None:
        ok_button = self._buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setEnabled(self._results.currentItem() is not None)

    def _on_result_double(self, item: QListWidgetItem) -> None:
        self._chosen_id = int(item.data(_PICK_ROLE))
        self.accept()

    def _on_accept(self) -> None:
        item = self._results.currentItem()
        if item is None:
            return
        self._chosen_id = int(item.data(_PICK_ROLE))
        self.accept()
