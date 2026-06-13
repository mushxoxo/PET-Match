"""Prices tab widget (editable)."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from numobel import mutations, search

# Role used to carry a numeric sort key for right-aligned numeric columns.
_SORT_ROLE = Qt.UserRole + 1
# Role used to carry the price row's database id on each item in the row.
_ID_ROLE = Qt.UserRole + 2


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
    """Editable, sortable table of all price rows."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self._conn = conn
        # Guard so programmatic (re)population doesn't trigger edit handling.
        self._loading = False
        # Whether the discount column is displayed/parsed as a percentage.
        self._discount_is_pct = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.table = QTableView(self)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        # Allow editing via double-click or an edit key; cells opt in
        # individually through item.setEditable().
        self.table.setEditTriggers(
            QTableView.DoubleClicked | QTableView.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])

        self.proxy = _NumericSortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self.proxy)

        # itemChanged fires on the *source* model with source items, which is
        # the simplest place to intercept committed edits.
        self.model.itemChanged.connect(self._on_item_changed)

        self._populate()
        self.table.resizeColumnsToContents()

    # ------------------------------------------------------------------ #
    # Population
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Reload prices from the DB — used after a catalog import."""
        self._populate()

    def _populate(self) -> None:
        rows = search.get_prices(self._conn)
        discounts = [r["discount"] for r in rows]
        self._discount_is_pct = _looks_fractional(discounts)

        self._loading = True
        try:
            self.model.setRowCount(0)
            for row in rows:
                price_id = row["id"]
                items: list[QStandardItem] = []
                for header, key, numeric in _COLUMNS:
                    item = QStandardItem()
                    self._apply_value(item, key, numeric, row[key])
                    item.setEditable(True)
                    item.setData(price_id, _ID_ROLE)
                    items.append(item)
                self.model.appendRow(items)
        finally:
            self._loading = False

    def _apply_value(
        self, item: QStandardItem, key: str, numeric: bool, value
    ) -> None:
        """Set display text, alignment, and sort role for ``value`` on ``item``.

        Mirrors the original read-only formatting logic so a value round-trips
        identically whether it was loaded or just edited.
        """
        if key == "discount" and self._discount_is_pct and value is not None:
            text = f"{value * 100:.1f}%"
        elif numeric:
            text = _format_number(value)
        else:
            text = "" if value is None else str(value)

        item.setText(text)
        if numeric:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sort_val = value if value is not None else float("-inf")
            item.setData(sort_val, _SORT_ROLE)
        else:
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    # ------------------------------------------------------------------ #
    # Editing
    # ------------------------------------------------------------------ #
    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._loading:
            return

        col = item.column()
        header, key, numeric = _COLUMNS[col]
        price_id = item.data(_ID_ROLE)

        text = item.text()

        # Parse the edited text into the value to store.
        if not numeric:
            # Seller: store the string verbatim (blank allowed).
            value = text
        else:
            stripped = text.strip()
            if stripped == "":
                value = None
            else:
                if key == "discount" and self._discount_is_pct:
                    # The display is a percentage; accept an optional trailing
                    # '%' and convert back to a fraction for storage.
                    stripped = stripped.rstrip("%").strip()
                try:
                    parsed = float(stripped)
                except ValueError:
                    self._reject_edit(item, key, numeric, price_id, text)
                    return
                if key == "discount" and self._discount_is_pct:
                    parsed = parsed / 100.0
                value = parsed

        # Persist via the sanctioned write API.
        try:
            mutations.update_price_field(self._conn, price_id, key, value)
        except mutations.MutationError as exc:
            QMessageBox.warning(self, "Edit rejected", str(exc))
            self._revert(item, key, numeric, price_id)
            return

        # Reformat the cell from the stored value so display/sort role stay
        # consistent (e.g. fraction -> "12.5%", float -> "1.5").
        self._reformat(item, key, numeric, value)

    def _reject_edit(
        self,
        item: QStandardItem,
        key: str,
        numeric: bool,
        price_id,
        attempted: str,
    ) -> None:
        QMessageBox.warning(
            self,
            "Invalid value",
            f"{attempted!r} is not a valid number for {key}.",
        )
        self._revert(item, key, numeric, price_id)

    def _revert(
        self, item: QStandardItem, key: str, numeric: bool, price_id
    ) -> None:
        """Restore the cell from the current database value."""
        row = self._conn.execute(
            "SELECT * FROM prices WHERE id = ?", (price_id,)
        ).fetchone()
        value = row[key] if row is not None else None
        self._reformat(item, key, numeric, value)

    def _reformat(
        self, item: QStandardItem, key: str, numeric: bool, value
    ) -> None:
        """Re-apply formatting for ``value`` without re-triggering edits."""
        blocked = self.model.blockSignals(True)
        try:
            self._apply_value(item, key, numeric, value)
        finally:
            self.model.blockSignals(blocked)
