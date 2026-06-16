"""First-run empty state: prompt the user to import a catalog workbook.

Shown by :class:`~numobel.ui.main_window.MainWindow` when the database has no
products yet. Emits :data:`OnboardingWidget.file_selected` once the user picks
an ``.xlsx`` file (via the button's file dialog or by dropping one onto the
card); the window owns the actual import.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from numobel.ui.widgets import Card

_FILE_FILTER = "Excel workbook (*.xlsx)"


class OnboardingWidget(QWidget):
    """Centered welcome card with a "choose workbook" call to action."""

    file_selected = Signal(str)
    google_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch(1)

        card = Card(raised=True)
        card.setMaximumWidth(560)
        col = QVBoxLayout(card)
        col.setContentsMargins(36, 32, 36, 32)
        col.setSpacing(14)
        col.setAlignment(Qt.AlignCenter)

        title = QLabel("Welcome to NUMOBEL")
        title.setProperty("class", "OnboardTitle")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 8)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        col.addWidget(title)

        subtitle = QLabel("Import your colour catalog workbook to get started.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        col.addWidget(subtitle)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._choose_btn = QPushButton("Choose .xlsx file…")
        self._choose_btn.setProperty("class", "AccentButton")
        self._choose_btn.setCursor(Qt.PointingHandCursor)
        self._choose_btn.clicked.connect(self._on_choose)
        button_row.addWidget(self._choose_btn)
        button_row.addStretch(1)
        col.addLayout(button_row)

        google_row = QHBoxLayout()
        google_row.addStretch(1)
        self._google_btn = QPushButton("Load from Google…")
        self._google_btn.setCursor(Qt.PointingHandCursor)
        self._google_btn.clicked.connect(self.google_requested)
        google_row.addWidget(self._google_btn)
        google_row.addStretch(1)
        col.addLayout(google_row)

        hint = QLabel(
            "Expects the NUMOBEL workbook "
            "(e.g. NUMOBEL_ACOUSTICS_COLOR_MAPS.xlsx). "
            "You can also drag the file onto this window."
        )
        hint.setProperty("class", "OnboardHint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        col.addWidget(hint)

        card_row = QHBoxLayout()
        card_row.addStretch(1)
        card_row.addWidget(card)
        card_row.addStretch(1)
        outer.addLayout(card_row)

        outer.addStretch(1)

    # ------------------------------------------------------------------ #
    # Picking
    # ------------------------------------------------------------------ #
    def _on_choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose catalog workbook", "", _FILE_FILTER
        )
        if path:
            self.file_selected.emit(path)

    # ------------------------------------------------------------------ #
    # Drag and drop
    # ------------------------------------------------------------------ #
    def _dropped_xlsx(self, event) -> str | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            path = url.toLocalFile()
            if path.lower().endswith(".xlsx"):
                return path
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._dropped_xlsx(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        path = self._dropped_xlsx(event)
        if path is not None:
            event.acceptProposedAction()
            self.file_selected.emit(path)
