"""Modal dialog for choosing how to link the catalog to a Google spreadsheet.

The user either creates a brand-new spreadsheet (seeded from current data),
picks one of their existing spreadsheets from a list, or pastes a spreadsheet
URL/ID. ``selected_choice()`` collapses those into a single string the caller
hands to ``SyncService.link_spreadsheet`` ("" means create-new).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
)


class LinkSpreadsheetDialog(QDialog):
    """Ask the user to create, pick, or paste a spreadsheet to link."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link a Google spreadsheet")
        layout = QVBoxLayout(self)

        helper = QLabel(
            "Choose where to keep this catalog on Google. Create a fresh "
            "spreadsheet seeded with your current data, pick an existing one "
            "from the list, or paste a spreadsheet URL or ID."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self._create_new = QRadioButton("Create a new spreadsheet")
        self._create_new.setChecked(True)
        layout.addWidget(self._create_new)

        self._list = QListWidget()
        self._set_placeholder("Loading your spreadsheets…")
        self._list.itemSelectionChanged.connect(self._on_list_selection)
        layout.addWidget(self._list)

        self._paste = QLineEdit()
        self._paste.setPlaceholderText("…or paste a spreadsheet URL or ID")
        self._paste.textChanged.connect(self._on_paste_changed)
        layout.addWidget(self._paste)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._create_new.toggled.connect(self._on_create_new_toggled)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _set_placeholder(self, text: str) -> None:
        """Show a single disabled, non-selectable placeholder row."""
        self._list.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        self._list.addItem(item)

    def set_spreadsheets(self, items: list[dict]) -> None:
        """Replace the list contents with one row per discovered spreadsheet."""
        self._list.clear()
        if not items:
            self._set_placeholder(
                "No spreadsheets found — create a new one or paste an ID."
            )
            return
        for item in items:
            name = item.get("name", "") or "(untitled)"
            modified = item.get("modifiedTime", "")
            label = f"{name}  ({modified})" if modified else name
            row = QListWidgetItem(label)
            row.setData(Qt.UserRole, item.get("id", ""))
            self._list.addItem(row)

    def selected_choice(self) -> str:
        """Return the chosen spreadsheet id/URL, or "" for create-new.

        Precedence: a non-empty pasted value wins; else a selected list row's
        id; else "" (create a new spreadsheet).
        """
        pasted = self._paste.text().strip()
        if pasted:
            return pasted
        current = self._list.currentItem()
        if current is not None and self._list.currentItem().isSelected():
            stored = current.data(Qt.UserRole)
            if stored:
                return str(stored)
        return ""

    # ------------------------------------------------------------------ #
    # Optional polish: keep the radio + selection states consistent
    # ------------------------------------------------------------------ #
    def _on_list_selection(self) -> None:
        if self._list.selectedItems():
            self._create_new.setChecked(False)

    def _on_paste_changed(self, text: str) -> None:
        if text.strip():
            self._create_new.setChecked(False)

    def _on_create_new_toggled(self, checked: bool) -> None:
        if checked:
            self._list.clearSelection()
            self._paste.clear()
