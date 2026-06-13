"""Prices tab widget (read-only)."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTableView, QVBoxLayout, QWidget

from numobel import search

# Role used to carry a numeric sort key for right-aligned numeric columns.
_SORT_ROLE = Qt.UserRole + 1


class _NumericSortProxy(QSortFilterProxyModel):
    """Sorts on the numeric sort role when present, else falls back to text."""

    def lessThan(self, left, right):  # noqa: N802 (Qt naming)
        lv = left.data(_SORT_ROLE)
        rv = right.data(_SORT_ROLE)
        if lv is not None and rv is not None:
            return lv < rv
        return super().lessThan(left, right)

# (header label, price-row column key, numeric?)
_COLUMNS: list[tuple[str, str, bool]] = [
    ("Seller", "seller", False),
    ("MRP", "mrp", True),
    ("MRP/sft", "mrp_sft", True),
    ("DP", "dp", True),
    ("DP/sft", "dp_sft", True),
    ("Profit", "profit", True),
    ("Discount", "discount", True),
    ("Cust Price", "cust_price", True),
    ("Cust Price/sft", "cust_price_sft", True),
]


def _looks_fractional(values: list) -> bool:
    """True if every non-null discount value is <= 1 (i.e. a fraction)."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return False
    return all(0 <= v <= 1 for v in nums)


def _format_number(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        # Trim trailing zeros but keep a sensible representation.
        return f"{value:g}"
    return str(value)


class PriceTab(QWidget):
    """Read-only, sortable table of all price rows."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.table = QTableView(self)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])

        self.proxy = _NumericSortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self.proxy)

        self._populate()
        self.table.resizeColumnsToContents()

    def _populate(self) -> None:
        rows = search.get_prices(self._conn)
        discounts = [r["discount"] for r in rows]
        discount_is_pct = _looks_fractional(discounts)

        self.model.setRowCount(0)
        for row in rows:
            items: list[QStandardItem] = []
            for header, key, numeric in _COLUMNS:
                value = row[key]
                if key == "discount" and discount_is_pct and value is not None:
                    text = f"{value * 100:.1f}%"
                elif numeric:
                    text = _format_number(value)
                else:
                    text = "" if value is None else str(value)

                item = QStandardItem()
                item.setEditable(False)
                item.setText(text)
                if numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    # Store raw numeric value so sorting is numeric, not lexical.
                    sort_val = value if value is not None else float("-inf")
                    item.setData(sort_val, _SORT_ROLE)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                items.append(item)
            self.model.appendRow(items)
