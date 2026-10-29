"""Рядки бази у вигляді dataclass-ів."""
from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
    IDLE = "Очікує"
    STARTING = "Запуск"
    LOGIN = "Вхід"
    MONITORING = "Моніторинг"
    ADDING = "Додаємо в кошик"
    CHECKOUT = "Оформлення"
    DONE = "Готово"
    STOPPED = "Зупинено"
    ERROR = "Помилка"


ACTIVE_TASK_STATUSES = {TaskStatus.STARTING, TaskStatus.LOGIN, TaskStatus.MONITORING, TaskStatus.ADDING,
                        TaskStatus.CHECKOUT}


class TaskMode(str, Enum):
    MONITOR = "monitor"
    CART = "cart"

    @property
    def label(self) -> str:
        return {"monitor": "Моніторинг", "cart": "Моніторинг + кошик"}[self.value]


class AccountStatus(str, Enum):
    NEW = "Новий"
    ACTIVE = "Активний"
    ERROR = "Помилка"


class ProxyStatus(str, Enum):
    UNKNOWN = "Не перевірено"
    OK = "Працює"
    FAIL = "Не працює"


@dataclass
class Proxy:
    id: int
    scheme: str
    host: str
    port: int
    username: str | None
    label: str | None
    status: str
    last_ip: str | None
    latency_ms: int | None
    checked_at: str | None
    created_at: str

    @property
    def address(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def display(self) -> str:
        return self.label or self.address


@dataclass
class Account:
    id: int
    email: str
    proxy_id: int | None
    status: str
    session_saved_at: str | None
    last_error: str | None
    created_at: str
    delivery_carrier: str = "nova"
    delivery_city: str | None = None
    delivery_warehouse: str | None = None
    payment_method: str = "card_link"


@dataclass
class Task:
    id: int
    account_id: int
    url: str
    title: str | None
    mode: str
    interval_sec: int
    status: str
    last_log: str | None
    last_check_at: str | None
    created_at: str

    @property
    def mode_enum(self) -> TaskMode:
        return TaskMode(self.mode)


@dataclass
class CartItem:
    id: int
    account_id: int | None
    task_id: int | None
    url: str
    title: str | None
    price: str | None
    added_at: str


@dataclass
class LogEntry:
    id: int
    ts: str
    level: str
    source: str | None
    message: str
