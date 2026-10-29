"""Моделі даних дашборду. Жодного Qt — їх можна наповнювати з API/БД незалежно від UI."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    AWAITING_PAYMENT = "Очікує оплати"
    PAID = "Оплачено"
    CONFIRMED = "Підтверджено"
    SHIPPED = "Передано логістичній компанії"
    DELIVERING = "Доставляється"
    RECEIVED = "Отримано"
    CANCELLED = "Анульовано"


@dataclass
class Recipient:
    full_name: str
    phone: str


@dataclass
class Delivery:
    carrier: str
    tariff: str
    city: str
    warehouse: str


@dataclass
class Product:
    name: str
    quantity: int
    kit: str  # комплектація


@dataclass
class HistoryEvent:
    status: OrderStatus
    at: datetime


@dataclass
class Order:
    number: str
    account: str
    created_at: datetime
    total: float
    status: OrderStatus
    ttn: str | None
    recipient: Recipient
    delivery: Delivery
    payment: str
    products: list[Product]
    history: list[HistoryEvent] = field(default_factory=list)

    @property
    def quantity(self) -> int:
        return sum(p.quantity for p in self.products)


@dataclass
class SystemStatus:
    api_ok: bool
    accounts_active: int
    accounts_total: int
    traffic_mb: float


@dataclass
class DailyStats:
    runs: int
    success: int
    errors: int
    # Зміна відносно вчора
    runs_delta: int
    success_delta: int
    errors_delta: int

    @property
    def success_rate(self) -> float:
        return self.success / self.runs * 100 if self.runs else 0.0


@dataclass
class DashboardData:
    system: SystemStatus
    stats: DailyStats
    orders: list[Order]
    wishlist_count: int
    cart_count: int
