"""Права панель деталей замовлення."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .models import Order
from .orders_view import format_money
from .widgets import SectionCard, StatusBadge, Timeline, clear_layout


class DetailsPanel(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("details")
        self.setMinimumWidth(330)
        self.setMaximumWidth(480)
        self.current_order: Order | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setObjectName("detailsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(16, 16, 16, 16)
        self._body.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("detailsHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 16, 12, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("detailsTitle")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close = QPushButton("✕")
        close.setObjectName("iconButton")
        close.setToolTip("Закрити")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.closed)
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(close)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.badge = StatusBadge()
        self.meta = QLabel()
        self.meta.setObjectName("muted")
        meta.addWidget(self.badge)
        meta.addWidget(self.meta)
        meta.addStretch()

        layout.addLayout(top)
        layout.addLayout(meta)
        return header

    def show_order(self, order: Order) -> None:
        self.current_order = order
        self.title.setText(f"Замовлення #{order.number}")
        self.badge.set_status(order.status)
        self.meta.setText(f"{order.created_at:%d.%m.%Y %H:%M} · {format_money(order.total)}")

        clear_layout(self._body)

        recipient = SectionCard("Отримувач")
        recipient.add_text(order.recipient.full_name, "kvStrong")
        recipient.add_text(order.recipient.phone, "muted")
        recipient.add_row("Акаунт", order.account)
        self._body.addWidget(recipient)

        delivery = SectionCard("Доставка")
        delivery.add_row("Перевізник", order.delivery.carrier)
        delivery.add_row("Тариф", order.delivery.tariff)
        delivery.add_row("Місто", order.delivery.city)
        delivery.add_row("Відділення", order.delivery.warehouse)
        delivery.add_row("ТТН", order.ttn or "ще не створено", mono=bool(order.ttn))
        self._body.addWidget(delivery)

        payment = SectionCard("Оплата")
        payment.add_row("Платіжна система", order.payment)
        payment.add_row("Сума", format_money(order.total))
        self._body.addWidget(payment)

        # Замовлення, зібрані зі списку в кабінеті, товарів не мають — порожню картку не показуємо
        if order.products:
            products = SectionCard("Товар")
            for i, product in enumerate(order.products):
                if i:
                    products.add_separator()
                products.add_text(product.name, "kvStrong")
                products.add_row("Кількість", f"{product.quantity} шт.")
                products.add_row("Комплектація", product.kit)
            self._body.addWidget(products)

        history = SectionCard("Історія")
        timeline = Timeline()
        timeline.set_events(order.history)
        history.add_widget(timeline)
        self._body.addWidget(history)

        self._body.addStretch()
