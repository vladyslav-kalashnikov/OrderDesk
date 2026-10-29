"""Тестові демо-записи. Усі імена, телефони, email і ТТН вигадані."""
from datetime import datetime

from .models import (
    DailyStats, DashboardData, Delivery, HistoryEvent, Order, OrderStatus as S,
    Product, Recipient, SystemStatus,
)


def _history(*events: tuple[S, str]) -> list[HistoryEvent]:
    return [HistoryEvent(status, datetime.strptime(at, "%d.%m.%Y %H:%M")) for status, at in events]


def build_demo_data() -> DashboardData:
    orders = [
        Order(
            number="2753120",
            account="demo.ihor@example.com",
            created_at=datetime(2026, 10, 2, 8, 15),
            total=2899,
            status=S.PAID,
            ttn=None,
            recipient=Recipient("Ткаченко Ігор Васильович", "+380 50 000 00 04"),
            delivery=Delivery("Нова Пошта", "Відділення", "Дніпро", "Відділення №14"),
            payment="LiqPay",
            products=[Product("Клавіатура KeyForge TKL", 1, "Кабель USB-C, знімач кейкапів")],
            history=_history(
                (S.AWAITING_PAYMENT, "02.10.2026 08:15"),
                (S.PAID, "02.10.2026 08:17"),
            ),
        ),
        Order(
            number="2753102",
            account="demo.max@example.com",
            created_at=datetime(2026, 9, 30, 9, 41),
            total=2598,
            status=S.DELIVERING,
            ttn="20451234500112",
            recipient=Recipient("Мельник Максим Андрійович", "+380 50 000 00 02"),
            delivery=Delivery("Нова Пошта", "Поштомат", "Львів", "Поштомат №3110"),
            payment="LiqPay",
            products=[Product("Powerbank VoltCell 20000", 2, "Кабель USB-C, чохол")],
            history=_history(
                (S.AWAITING_PAYMENT, "30.09.2026 09:41"),
                (S.PAID, "30.09.2026 09:43"),
                (S.CONFIRMED, "30.09.2026 10:20"),
                (S.SHIPPED, "01.10.2026 11:05"),
                (S.DELIVERING, "01.10.2026 19:32"),
            ),
        ),
        Order(
            number="2753091",
            account="demo.anna@example.com",
            created_at=datetime(2026, 9, 24, 14, 12),
            total=3499,
            status=S.RECEIVED,
            ttn="20451234567890",
            recipient=Recipient("Шевченко Анна Олегівна", "+380 50 000 00 01"),
            delivery=Delivery("Нова Пошта", "Відділення", "Київ", "Відділення №52"),
            payment="LiqPay",
            products=[Product("Навушники SoundPro X2", 1, "Кейс, кабель USB-C, амбушури S/M/L")],
            history=_history(
                (S.AWAITING_PAYMENT, "24.09.2026 14:12"),
                (S.PAID, "24.09.2026 14:14"),
                (S.CONFIRMED, "24.09.2026 14:40"),
                (S.SHIPPED, "25.09.2026 10:05"),
                (S.DELIVERING, "25.09.2026 18:30"),
                (S.RECEIVED, "26.09.2026 12:47"),
            ),
        ),
        Order(
            number="2752987",
            account="demo.olena@example.com",
            created_at=datetime(2026, 9, 21, 17, 3),
            total=1299,
            status=S.CANCELLED,
            ttn=None,
            recipient=Recipient("Бондар Олена Сергіївна", "+380 50 000 00 03"),
            delivery=Delivery("Нова Пошта", "Відділення", "Одеса", "Відділення №7"),
            payment="LiqPay",
            products=[Product("Смарт-годинник Pulse S", 1, "Ремінець, зарядна станція")],
            history=_history(
                (S.AWAITING_PAYMENT, "21.09.2026 17:03"),
                (S.CANCELLED, "22.09.2026 17:03"),
            ),
        ),
    ]

    return DashboardData(
        system=SystemStatus(api_ok=True, accounts_active=28, accounts_total=29, traffic_mb=929.40),
        stats=DailyStats(runs=12, success=9, errors=3, runs_delta=4, success_delta=2, errors_delta=-1),
        orders=orders,
        wishlist_count=2,
        cart_count=0,
    )
