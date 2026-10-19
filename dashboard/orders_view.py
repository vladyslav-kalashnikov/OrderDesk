"""Центральна частина: заголовок, пошук, вкладки та таблиця замовлень."""
from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .core.db import Database
from .core.entities import TaskMode
from .models import Order
from .pages.common import DataTable, TableOrEmpty, confirm, state_cell
from .widgets import StatusBadge, mono_font

COL_NUMBER, COL_ACCOUNT, COL_DATE, COL_QTY, COL_TOTAL, COL_STATUS, COL_TTN, COL_ACTIONS = range(8)
HEADERS = ["№", "АКАУНТ", "ДАТА", "ШТ.", "СУМА", "СТАТУС", "ТТН", "ДІЇ"]

TAB_WISHLIST, TAB_CART, TAB_ORDERS = range(3)


def format_money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " грн"


def _centered(widget: QWidget) -> QWidget:
    """Обгортка, щоб віджет у клітинці не розтягувався на всю її ширину."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(10, 0, 10, 0)
    layout.addWidget(widget)
    layout.addStretch()
    return holder


class OrdersTable(QTableWidget):
    order_activated = Signal(str)  # номер замовлення

    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self._orders: list[Order] = []

        self.setHorizontalHeaderLabels(HEADERS)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(56)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(48)
        for col in range(len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_ACCOUNT, QHeaderView.ResizeMode.Stretch)
        # Колонки з віджетами: ResizeToContents їх не міряє, тож ширина фіксована
        for col, width in ((COL_STATUS, 160), (COL_ACTIONS, 124)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)

        self.itemSelectionChanged.connect(self._on_selection_changed)

    def set_orders(self, orders: list[Order]) -> None:
        self._orders = list(orders)
        self.setRowCount(0)
        for order in self._orders:
            self._append(order)

    def _append(self, order: Order) -> None:
        row = self.rowCount()
        self.insertRow(row)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        number = QTableWidgetItem(f"#{order.number}")
        number.setData(Qt.ItemDataRole.UserRole, order.number)
        self.setItem(row, COL_NUMBER, number)
        self.setItem(row, COL_ACCOUNT, QTableWidgetItem(order.account))
        self.setItem(row, COL_DATE, QTableWidgetItem(order.created_at.strftime("%d.%m.%Y %H:%M")))

        # Замовлення зі списку в кабінеті не знають кількості — прочерк замість «0»
        qty = QTableWidgetItem(str(order.quantity) if order.products else "—")
        qty.setTextAlignment(right)
        self.setItem(row, COL_QTY, qty)

        total = QTableWidgetItem(format_money(order.total))
        total.setTextAlignment(right)
        self.setItem(row, COL_TOTAL, total)

        self.setItem(row, COL_STATUS, QTableWidgetItem())
        self.setCellWidget(row, COL_STATUS, _centered(StatusBadge(order.status)))

        ttn = QTableWidgetItem(order.ttn or "—")
        ttn.setFont(mono_font())
        self.setItem(row, COL_TTN, ttn)

        button = QPushButton("Дивитись")
        button.setObjectName("ghost")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, r=row: self._open_row(r))
        self.setItem(row, COL_ACTIONS, QTableWidgetItem())
        self.setCellWidget(row, COL_ACTIONS, _centered(button))

    def _open_row(self, row: int) -> None:
        self.selectRow(row)
        # Якщо рядок уже був виділений, selectionChanged не прийде — тому емітимо явно
        self._emit_row(row)

    def _on_selection_changed(self) -> None:
        rows = self.selectionModel().selectedRows()
        if rows:
            self._emit_row(rows[0].row())

    def _emit_row(self, row: int) -> None:
        self.order_activated.emit(self.item(row, COL_NUMBER).data(Qt.ItemDataRole.UserRole))

    def apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row, order in enumerate(self._orders):
            haystack = " ".join([
                order.number, order.account, order.status.value, order.ttn or "",
                order.recipient.full_name, order.recipient.phone,
            ]).casefold()
            self.setRowHidden(row, bool(needle) and needle not in haystack)


class OrdersView(QFrame):
    """Кабінет: «Бажане» = товари на моніторингу, «Кошик» = додане рушієм, «Замовлення» = з бази."""

    order_selected = Signal(object)  # Order
    sync_requested = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.setObjectName("content")
        self.db = db
        self._by_number: dict[str, Order] = {}
        self._orders_signature: tuple = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(self._build_header())
        layout.addLayout(self._build_summary())

        self.tabs = QTabBar()
        self.tabs.setObjectName("tabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        for _ in range(3):
            self.tabs.addTab("")
        layout.addWidget(self.tabs)

        self.wishlist = DataTable(["ТОВАР", "АКАУНТ", "РЕЖИМ", "СТАТУС", "ПЕРЕВІРЕНО"], stretch=(0,))
        self.cart = DataTable(["ТОВАР", "АКАУНТ", "ЦІНА", "ДОДАНО"], stretch=(0,))
        self.table = OrdersTable()
        self.table.order_activated.connect(lambda num: self.order_selected.emit(self._by_number[num]))
        # Подвійний клік — відкрити товар у звичайному браузері
        self.wishlist.doubleClicked.connect(lambda i: self._open_url(self._wishlist_urls, i.row()))
        self.cart.doubleClicked.connect(lambda i: self._open_url(self._cart_urls, i.row()))
        self._wishlist_urls: list[str] = []
        self._cart_urls: list[str] = []

        self.views = [
            TableOrEmpty(self.wishlist, "Бажане порожнє", "Додайте завдання в розділі «Моніторинг»"),
            TableOrEmpty(self.cart, "Кошик порожній", "Сюди потрапляють товари, які рушій додав у кошик"),
            TableOrEmpty(self.table, "Замовлень ще немає",
                         "Натисніть «Оновити замовлення» — програма збере їх з кабінетів акаунтів"),
        ]
        self.stack = QStackedWidget()
        for view in self.views:
            self.stack.addWidget(view)
        layout.addWidget(self.stack, 1)

        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda _: self._apply_search())
        self.refresh()
        self.tabs.setCurrentIndex(TAB_WISHLIST)

    def _build_summary(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._summary: dict[str, QLabel] = {}
        for key, caption in (("tasks", "Завдань на моніторингу"), ("cart", "Товарів у кошику"),
                             ("orders", "Замовлень")):
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(2)
            value = QLabel("0")
            value.setObjectName("statValue")
            value.setStyleSheet("font-size: 26px;")
            title = QLabel(caption)
            title.setObjectName("muted")
            box.addWidget(value)
            box.addWidget(title)
            self._summary[key] = value
            row.addWidget(card, 1)
        return row

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Кабінет")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Бажане, кошик і замовлення акаунтів")
        subtitle.setObjectName("muted")
        titles.addWidget(title)
        titles.addWidget(subtitle)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Пошук у поточній вкладці…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(lambda _: self._apply_search())

        self.sync_button = QPushButton("↻ Оновити замовлення")
        self.sync_button.setToolTip("Зібрати замовлення, статуси й ТТН з кабінетів усіх акаунтів")
        self.sync_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_button.clicked.connect(self._request_sync)

        self.remove_button = QPushButton("Видалити вибране")
        self.remove_button.setToolTip("Прибрати виділені товари зі списку «Кошик» у програмі")
        self.remove_button.clicked.connect(self.remove_selected_cart)
        self.clear_button = QPushButton("Очистити кошик")
        self.clear_button.setObjectName("danger")
        self.clear_button.setToolTip("Прибрати всі товари зі списку «Кошик» у програмі")
        self.clear_button.clicked.connect(self.clear_cart)
        for b in (self.remove_button, self.clear_button):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.hide()

        row.addLayout(titles)
        row.addStretch()
        row.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(8)
        row.addWidget(self.sync_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(8)
        row.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _request_sync(self) -> None:
        self.set_syncing(True)
        self.sync_requested.emit()

    def set_syncing(self, syncing: bool) -> None:
        self.sync_button.setEnabled(not syncing)
        self.sync_button.setText("Оновлюю…" if syncing else "↻ Оновити замовлення")

    NOTE = ("\n\nУвага: це прибирає записи лише зі списку в програмі. "
            "Сам кошик на сайті магазину не змінюється — його очищайте на сайті.")

    def _update_cart_buttons(self) -> None:
        on_cart = self.tabs.currentIndex() == TAB_CART
        self.remove_button.setVisible(on_cart)
        self.clear_button.setVisible(on_cart)
        self.remove_button.setEnabled(bool(self.cart.rowCount()))
        self.clear_button.setEnabled(bool(self.cart.rowCount()))

    def remove_selected_cart(self) -> None:
        ids = self.cart.selected_ids()
        if not ids:
            QMessageBox.information(self, "Нічого не виділено", "Виділіть у списку товари, які треба прибрати.")
            return
        if confirm(self, "Видалити з кошика", f"Прибрати зі списку товарів: {len(ids)}?" + self.NOTE):
            for i in ids:
                self.db.delete_cart_item(i)
            self.refresh()

    def clear_cart(self) -> None:
        count = self.cart.rowCount()
        if count and confirm(self, "Очистити кошик", f"Прибрати зі списку всі товари ({count})?" + self.NOTE):
            self.db.clear_cart_items()
            self.refresh()

    def _apply_search(self) -> None:
        self._update_cart_buttons()
        tables = [self.wishlist, self.cart, self.table]
        tables[self.tabs.currentIndex()].apply_filter(self.search.text())

    @staticmethod
    def _open_url(urls: list[str], row: int) -> None:
        if 0 <= row < len(urls):
            QDesktopServices.openUrl(QUrl(urls[row]))

    def refresh(self) -> None:
        emails = {a.id: a.email for a in self.db.accounts()}

        tasks = self.db.tasks()
        self._wishlist_urls = [t.url for t in tasks]
        self.wishlist.set_rows([(t.id, [
            t.title or t.url, emails.get(t.account_id, "—"), TaskMode(t.mode).label,
            state_cell(t.status), t.last_check_at or "—",
        ]) for t in tasks])

        items = self.db.cart_items()
        self._cart_urls = [c.url for c in items]
        self.cart.set_rows([(c.id, [
            c.title or c.url, emails.get(c.account_id, "—"), c.price or "—", c.added_at,
        ]) for c in items])

        orders = self.db.orders()
        signature = tuple((o.number, o.status, o.ttn) for o in orders)
        if signature != self._orders_signature:
            # Таблицю замовлень перебудовуємо лише при змінах — інакше злітало б виділення
            self._orders_signature = signature
            self._by_number = {o.number: o for o in orders}
            self.table.set_orders(orders)

        self._summary["tasks"].setText(str(len(tasks)))
        self._summary["cart"].setText(str(len(items)))
        self._summary["orders"].setText(str(len(orders)))
        self.tabs.setTabText(TAB_WISHLIST, f"Бажане ({len(tasks)})")
        self.tabs.setTabText(TAB_CART, f"Кошик ({len(items)})")
        self.tabs.setTabText(TAB_ORDERS, f"Замовлення ({len(orders)})")
        for view in self.views:
            view.update_state()
        self._apply_search()
