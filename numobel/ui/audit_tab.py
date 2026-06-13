"""Audit log tab widget (read-only)."""

from __future__ import annotations

import json
import sqlite3

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QTableView, QVBoxLayout, QWidget

from numobel import audit

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


# (header label, audit-row column key)
_COLUMNS: list[tuple[str, str]] = [
    ("Time", "ts"),
    ("Action", "action"),
    ("Entity", "entity"),
    ("Entity ID", "entity_id"),
    ("Details", "details"),
]


def _format_details(details) -> str:
    """Render the ``details`` value as a human-friendly string.

    A JSON object becomes a compact ``key=value, key=value`` string with
    sorted keys; any other (non-null) value is shown as-is; NULL becomes ''.
    """
    if details is None:
        return ""
    text = str(details)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(parsed, dict):
        return ", ".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    return text


class AuditTab(QWidget):
    """Read-only, sortable table of the change/audit log."""

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
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])

        self.proxy = _NumericSortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self.proxy)

        self.refresh()

    def refresh(self) -> None:
        """Re-query the audit log and repopulate the model (newest first)."""
        rows = audit.get_audit_log(self._conn)

        self.model.setRowCount(0)
        for row in rows:
            items: list[QStandardItem] = []
            for _header, key in _COLUMNS:
                value = row[key]
                if key == "ts":
                    text = "" if value is None else str(value).replace("T", " ")
                elif key == "details":
                    text = _format_details(value)
                else:
                    text = "" if value is None else str(value)

                item = QStandardItem()
                item.setEditable(False)
                item.setText(text)
                if key == "entity_id":
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    # Store raw numeric value so sorting is numeric, not lexical.
                    sort_val = value if value is not None else float("-inf")
                    item.setData(sort_val, _SORT_ROLE)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                items.append(item)
            self.model.appendRow(items)

        self.table.resizeColumnsToContents()
        # Let the Details column take the remaining horizontal space.
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
