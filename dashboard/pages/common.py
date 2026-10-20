"""Спільне для сторінок: каркас сторінки, таблиця даних, діалоги підтвердження."""
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import theme
from ..widgets import EmptyState

# Значення клітинки: просто текст або (текст, колір)
Cell = str | tuple[str, str]


def button(text: str, kind: str = "", tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if kind:
        btn.setObjectName(kind)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def confirm(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


class Page(QFrame):
    """Каркас: заголовок і підзаголовок зліва, кнопки дій справа, під ними — тіло."""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName("content")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("muted")
        titles.addWidget(title_label)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        header.addLayout(self.actions)
        layout.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        layout.addLayout(self.body, 1)

    def refresh(self) -> None:
        """Перечитати дані з бази. Перевизначається в сторінках."""


class DataTable(QTableWidget):
    """Таблиця рядків з id у першій колонці. Перебудова зберігає виділення."""

    def __init__(self, headers: list[str], stretch: tuple[int, ...] = (),
                 widths: dict[int, int] | None = None, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(40)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(36)
        widths = widths or {}
        stretch_set = set(stretch)
        for col in range(len(headers)):
            if col in widths:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(col, widths[col])
            elif col in stretch_set:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._widths = widths
        self._filter = ""

    def set_rows(self, rows: list[tuple[int, list[Cell]]]) -> None:
        selected = set(self.selected_ids())
        self.blockSignals(True)
        self.setRowCount(len(rows))
        for r, (row_id, cells) in enumerate(rows):
            for c, value in enumerate(cells):
                text, color = value if isinstance(value, tuple) else (value, None)
                item = QTableWidgetItem(text)
                if text:
                    item.setToolTip(text)
                if color:
                    item.setForeground(QColor(color))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row_id)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(r, c, item)
        # Підтримуємо задані ширини колонок без скидання при заповненні
        header = self.horizontalHeader()
        for col, width in self._widths.items():
            header.resizeSection(col, width)
        self.clearSelection()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for r, (row_id, _) in enumerate(rows):
            if row_id in selected:
                self.selectionModel().select(self.model().index(r, 0), flags)
        self.blockSignals(False)
        self.apply_filter(self._filter)

    def row_id(self, row: int) -> int:
        return self.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.row_id(r) for r in rows if self.item(r, 0)]

    def all_ids(self) -> list[int]:
        return [self.row_id(r) for r in range(self.rowCount()) if self.item(r, 0)]

    def set_cell(self, row_id: int, col: int, text: str, color: str | None = None) -> None:
        for r in range(self.rowCount()):
            if self.row_id(r) == row_id:
                item = self.item(r, col)
                item.setText(text)
                item.setToolTip(text if len(text) > 40 else "")
                if color:
                    item.setForeground(QColor(color))
                return

    def apply_filter(self, text: str) -> None:
        self._filter = text
        needle = text.strip().casefold()
        for r in range(self.rowCount()):
            haystack = " ".join(self.item(r, c).text() for c in range(self.columnCount()) if self.item(r, c))
            self.setRowHidden(r, bool(needle) and needle not in haystack.casefold())


def state_cell(status: str) -> tuple[str, str]:
    return status, theme.STATE_COLORS.get(status, theme.MUTED)


class TableOrEmpty(QStackedWidget):
    """Таблиця, а коли рядків немає — заглушка з поясненням."""

    def __init__(self, table: QTableWidget, empty_title: str, empty_subtitle: str = "", parent=None):
        super().__init__(parent)
        self.table = table
        self.addWidget(table)
        self.addWidget(EmptyState(empty_title, empty_subtitle))

    def update_state(self) -> None:
        self.setCurrentIndex(0 if self.table.rowCount() else 1)
