"""Reusable create dialogs for brands and products.

Both dialogs write through :mod:`numobel.mutations` (so every creation is
audited) and expose the new row id via ``created_*_id`` after the dialog is
accepted. They are shared by the catalog menu (add brand / add product) and by
the similar-color editor's "create new color while linking" flow.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from numobel import mutations, search


class BrandFormDialog(QDialog):
    """Modal dialog to create a new brand."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn
        self._brand_id: Optional[int] = None

        self.setWindowTitle("Add Brand")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._code = QLineEdit()
        self._code.setPlaceholderText("e.g. AT")
        self._name = QLineEdit()
        self._has_sheet = QCheckBox("Brand has a price sheet")
        form.addRow("Code *:", self._code)
        form.addRow("Name:", self._name)
        form.addRow("", self._has_sheet)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._code.setFocus()

    def created_brand_id(self) -> Optional[int]:
        return self._brand_id

    def _on_accept(self) -> None:
        try:
            self._brand_id = mutations.add_brand(
                self._conn,
                self._code.text(),
                self._name.text() or None,
                has_sheet=self._has_sheet.isChecked(),
            )
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot add brand", str(err))
            return
        self.accept()


class ProductFormDialog(QDialog):
    """Modal dialog to create a new product/color under a brand.

    Includes an inline "New Brand…" button so a brand-new brand can be created
    without leaving the flow.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        parent: QWidget | None = None,
        preselect_brand_id: Optional[int] = None,
    ):
        super().__init__(parent)
        self._conn = conn
        self._product_id: Optional[int] = None

        self.setWindowTitle("Add Product / Color")
        self.setModal(True)
        self.resize(400, 0)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        self._brand_combo = QComboBox()
        new_brand_btn = QPushButton("New Brand…")
        new_brand_btn.clicked.connect(self._on_new_brand)
        brand_row.addWidget(self._brand_combo, 1)
        brand_row.addWidget(new_brand_btn)
        brand_holder = QWidget()
        brand_holder.setLayout(brand_row)
        form.addRow("Brand *:", brand_holder)
        self._reload_brands(preselect_brand_id)

        self._sku = QLineEdit()
        self._color = QLineEdit()
        self._shade = QLineEdit()
        self._thickness = QDoubleSpinBox()
        self._thickness.setRange(0.0, 1000.0)
        self._thickness.setDecimals(2)
        self._thickness.setSingleStep(0.1)
        # A value of 0 means "unset" and is stored as NULL.
        self._thickness.setSpecialValueText("—")
        self._thickness.setValue(0.0)
        form.addRow("SKU:", self._sku)
        form.addRow("Color Name:", self._color)
        form.addRow("Shade No:", self._shade)
        form.addRow("Thickness:", self._thickness)
        layout.addLayout(form)

        hint = QLabel("Provide at least a SKU or a color name.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def created_product_id(self) -> Optional[int]:
        return self._product_id

    def _reload_brands(self, select_id: Optional[int] = None) -> None:
        self._brand_combo.clear()
        for brand in search.list_brands(self._conn):
            label = brand["code"]
            if brand["name"]:
                label = f"{brand['code']} — {brand['name']}"
            self._brand_combo.addItem(label, brand["id"])
        if select_id is not None:
            idx = self._brand_combo.findData(select_id)
            if idx >= 0:
                self._brand_combo.setCurrentIndex(idx)

    def _on_new_brand(self) -> None:
        dialog = BrandFormDialog(self._conn, self)
        if dialog.exec() == QDialog.Accepted:
            self._reload_brands(dialog.created_brand_id())

    def _on_accept(self) -> None:
        brand_id = self._brand_combo.currentData()
        if brand_id is None:
            QMessageBox.warning(
                self, "Cannot add product", "Create or pick a brand first."
            )
            return
        thickness = self._thickness.value()
        try:
            self._product_id = mutations.add_product(
                self._conn,
                int(brand_id),
                sku=self._sku.text() or None,
                color_name=self._color.text() or None,
                shade_no=self._shade.text() or None,
                thickness=thickness if thickness > 0 else None,
            )
        except mutations.MutationError as err:
            QMessageBox.warning(self, "Cannot add product", str(err))
            return
        self.accept()
