"""SQLite-сховище. Одне з'єднання на процес + лок: пишуть і UI-потік, і потік рушія."""
import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from ..models import (
    DailyStats, Delivery, HistoryEvent, Order, OrderStatus, Product, Recipient,
)
from .entities import (
    Account, AccountStatus, CartItem, LogEntry, Proxy, ProxyStatus, Task, TaskStatus,
)

SCHEMA = """
create table if not exists proxies (
    id integer primary key,
    scheme text not null,
    host text not null,
    port integer not null,
    username text,
    label text,
    status text not null default 'Не перевірено',
    last_ip text,
    latency_ms integer,
    checked_at text,
    created_at text not null
);
create table if not exists accounts (
    id integer primary key,
    email text not null unique,
    proxy_id integer references proxies(id) on delete set null,
    status text not null default 'Новий',
    session_saved_at text,
    last_error text,
    delivery_carrier text not null default 'nova',
    delivery_city text,
    delivery_warehouse text,
    payment_method text not null default 'card_link',
    created_at text not null
);
create table if not exists tasks (
    id integer primary key,
    account_id integer not null references accounts(id) on delete cascade,
    url text not null,
    title text,
    mode text not null check (mode in ('monitor', 'cart')),
    interval_sec integer not null default 30,
    status text not null default 'Очікує',
    last_log text,
    last_check_at text,
    created_at text not null
);
create table if not exists runs (
    id integer primary key,
    task_id integer references tasks(id) on delete set null,
    started_at text not null,
    finished_at text,
    result text not null default 'running'   -- running / success / error / stopped
);
create table if not exists cart_items (
    id integer primary key,
    account_id integer references accounts(id) on delete set null,
    task_id integer references tasks(id) on delete set null,
    url text not null,
    title text,
    price text,
    added_at text not null
);
create table if not exists logs (
    id integer primary key,
    ts text not null,
    level text not null,
    source text,
    message text not null
);
create table if not exists traffic (
    day text primary key,
    bytes integer not null default 0
);
create table if not exists orders (
    number text primary key,
    account text not null,
    created_at text not null,
    payload text not null       -- повне замовлення в JSON (див. order_to_dict)
);
-- Спроби чекауту. Рядок зі статусом submitting/success/unknown = «замовлення по завданню вже є або могло бути»:
-- унікальний індекс не дає створити друге, навіть з двох потоків чи після перезапуску програми.
create table if not exists checkouts (
    id integer primary key,
    task_id integer not null,
    account_id integer,
    status text not null,       -- submitting / success / unknown / failed
    total integer,
    order_number text,
    error text,
    created_at text not null,
    finished_at text
);
create unique index if not exists idx_checkouts_one_per_task on checkouts(task_id)
    where status in ('submitting', 'success', 'unknown');
create index if not exists idx_runs_started on runs(started_at);
create index if not exists idx_logs_ts on logs(ts);
"""


def now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma journal_mode = wal")
        self._conn.execute("pragma foreign_keys = on")
        self._conn.executescript(SCHEMA)
        # Міграція: додаємо колонки доставки та оплати для старих баз
        cols = {r["name"] for r in self._conn.execute("pragma table_info(accounts)").fetchall()}
        if "delivery_carrier" not in cols:
            self._conn.execute("alter table accounts add column delivery_carrier text not null default 'nova'")
        if "delivery_city" not in cols:
            self._conn.execute("alter table accounts add column delivery_city text")
        if "delivery_warehouse" not in cols:
            self._conn.execute("alter table accounts add column delivery_warehouse text")
        if "payment_method" not in cols:
            self._conn.execute("alter table accounts add column payment_method text not null default 'card_link'")
        # Після аварійного виходу завдання могли лишитись «активними» — повертаємо в «Зупинено»
        self._conn.execute(
            "update tasks set status = ? where status in (?, ?, ?, ?, ?)",
            (TaskStatus.STOPPED.value, TaskStatus.STARTING.value, TaskStatus.LOGIN.value,
             TaskStatus.MONITORING.value, TaskStatus.ADDING.value, TaskStatus.CHECKOUT.value),
        )
        # Чекаут, перерваний посеред підтвердження, лишається блокуючим: замовлення могло створитися
        self._conn.execute("update checkouts set status = 'unknown', finished_at = ? where status = 'submitting'",
                           (now(),))
        self._conn.execute("update runs set result = 'stopped', finished_at = ? where result = 'running'", (now(),))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _all(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _exec(self, sql: str, params=()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).lastrowid

    # --- Проксі ---

    def proxies(self) -> list[Proxy]:
        return [Proxy(**dict(r)) for r in self._all("select * from proxies order by id")]

    def proxy(self, proxy_id: int) -> Proxy | None:
        row = self._one("select * from proxies where id = ?", (proxy_id,))
        return Proxy(**dict(row)) if row else None

    def add_proxy(self, scheme: str, host: str, port: int, username: str | None, label: str | None = None) -> int:
        return self._exec(
            "insert into proxies (scheme, host, port, username, label, status, created_at) values (?, ?, ?, ?, ?, ?, ?)",
            (scheme, host, port, username, label, ProxyStatus.UNKNOWN.value, now()),
        )

    def update_proxy_check(self, proxy_id: int, status: ProxyStatus, ip: str | None, latency_ms: int | None) -> None:
        self._exec(
            "update proxies set status = ?, last_ip = ?, latency_ms = ?, checked_at = ? where id = ?",
            (status.value, ip, latency_ms, now(), proxy_id),
        )

    def delete_proxy(self, proxy_id: int) -> None:
        self._exec("delete from proxies where id = ?", (proxy_id,))

    # --- Акаунти ---

    def accounts(self) -> list[Account]:
        return [Account(**dict(r)) for r in self._all("select * from accounts order by id")]

    def account(self, account_id: int) -> Account | None:
        row = self._one("select * from accounts where id = ?", (account_id,))
        return Account(**dict(row)) if row else None

    def add_account(self, email: str, proxy_id: int | None,
                    delivery_carrier: str = "nova",
                    delivery_city: str | None = None,
                    delivery_warehouse: str | None = None,
                    payment_method: str = "card_link") -> int:
        return self._exec(
            "insert into accounts (email, proxy_id, status, delivery_carrier, delivery_city, delivery_warehouse, payment_method, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?)",
            (email, proxy_id, AccountStatus.NEW.value, delivery_carrier, delivery_city, delivery_warehouse, payment_method, now()),
        )

    def update_account_settings(self, account_id: int, proxy_id: int | None,
                                delivery_carrier: str = "nova",
                                delivery_city: str | None = None,
                                delivery_warehouse: str | None = None,
                                payment_method: str = "card_link") -> None:
        self._exec(
            "update accounts set proxy_id = ?, delivery_carrier = ?, delivery_city = ?, delivery_warehouse = ?, payment_method = ? where id = ?",
            (proxy_id, delivery_carrier, delivery_city, delivery_warehouse, payment_method, account_id),
        )

    def set_account_proxy(self, account_id: int, proxy_id: int | None) -> None:
        self._exec("update accounts set proxy_id = ? where id = ?", (proxy_id, account_id))

    def set_account_status(self, account_id: int, status: AccountStatus, error: str | None = None,
                           session_saved: bool = False) -> None:
        if session_saved:
            self._exec(
                "update accounts set status = ?, last_error = ?, session_saved_at = ? where id = ?",
                (status.value, error, now(), account_id),
            )
        else:
            self._exec("update accounts set status = ?, last_error = ? where id = ?", (status.value, error, account_id))

    def clear_account_session(self, account_id: int) -> None:
        self._exec(
            "update accounts set session_saved_at = null, status = ?, last_error = null where id = ?",
            (AccountStatus.NEW.value, account_id),
        )

    def delete_account(self, account_id: int) -> None:
        self._exec("delete from accounts where id = ?", (account_id,))

    # --- Завдання ---

    def tasks(self) -> list[Task]:
        return [Task(**dict(r)) for r in self._all("select * from tasks order by id")]

    def task(self, task_id: int) -> Task | None:
        row = self._one("select * from tasks where id = ?", (task_id,))
        return Task(**dict(row)) if row else None

    def add_task(self, account_id: int, url: str, mode: str, interval_sec: int) -> int:
        return self._exec(
            "insert into tasks (account_id, url, mode, interval_sec, status, created_at) values (?, ?, ?, ?, ?, ?)",
            (account_id, url, mode, interval_sec, TaskStatus.IDLE.value, now()),
        )

    def update_task(self, task_id: int, status: TaskStatus, log: str | None = None, *,
                    title: str | None = None, checked: bool = False) -> None:
        sets, params = ["status = ?"], [status.value]
        if log is not None:
            sets.append("last_log = ?")
            params.append(log)
        if title:
            sets.append("title = ?")
            params.append(title)
        if checked:
            sets.append("last_check_at = ?")
            params.append(now())
        params.append(task_id)
        self._exec(f"update tasks set {', '.join(sets)} where id = ?", params)

    def delete_task(self, task_id: int) -> None:
        self._exec("delete from tasks where id = ?", (task_id,))

    # --- Запуски і статистика ---

    def start_run(self, task_id: int) -> int:
        return self._exec("insert into runs (task_id, started_at) values (?, ?)", (task_id, now()))

    def finish_run(self, run_id: int, result: str) -> None:
        self._exec("update runs set result = ?, finished_at = ? where id = ?", (result, now(), run_id))

    def _day_counts(self, day: date) -> tuple[int, int, int]:
        row = self._one(
            "select count(*) as runs,"
            " coalesce(sum(result = 'success'), 0) as success,"
            " coalesce(sum(result = 'error'), 0) as errors"
            " from runs where started_at >= ? and started_at < ?",
            (day.isoformat(), (day + timedelta(days=1)).isoformat()),
        )
        return row["runs"], row["success"], row["errors"]

    def daily_stats(self, day: date | None = None) -> DailyStats:
        day = day or date.today()
        runs, success, errors = self._day_counts(day)
        y_runs, y_success, y_errors = self._day_counts(day - timedelta(days=1))
        return DailyStats(runs, success, errors, runs - y_runs, success - y_success, errors - y_errors)

    # --- Кошик ---

    def cart_items(self) -> list[CartItem]:
        return [CartItem(**dict(r)) for r in self._all("select * from cart_items order by id desc")]

    def add_cart_item(self, account_id: int, task_id: int, url: str, title: str | None, price: str | None) -> int:
        return self._exec(
            "insert into cart_items (account_id, task_id, url, title, price, added_at) values (?, ?, ?, ?, ?, ?)",
            (account_id, task_id, url, title, price, now()),
        )

    def delete_cart_item(self, item_id: int) -> None:
        self._exec("delete from cart_items where id = ?", (item_id,))

    def clear_cart_items(self) -> None:
        self._exec("delete from cart_items")

    # --- Чекаут ---

    def checkout_block(self, task_id: int) -> sqlite3.Row | None:
        """Рядок, через який нове замовлення по завданню заборонене (успішне / невідомий результат / в процесі)."""
        return self._one("select * from checkouts where task_id = ? and status in ('submitting', 'success', 'unknown')",
                         (task_id,))

    def claim_checkout(self, task_id: int, account_id: int, total: int | None) -> int | None:
        """Атомарно резервує єдине замовлення для завдання. None — уже зарезервовано/оформлено."""
        try:
            return self._exec(
                "insert into checkouts (task_id, account_id, status, total, created_at) values (?, ?, 'submitting', ?, ?)",
                (task_id, account_id, total, now()),
            )
        except sqlite3.IntegrityError:
            return None

    def finish_checkout(self, checkout_id: int, status: str, order_number: str | None = None,
                        error: str | None = None) -> None:
        self._exec("update checkouts set status = ?, order_number = ?, error = ?, finished_at = ? where id = ?",
                   (status, order_number, error, now(), checkout_id))

    # --- Логи ---

    def add_log(self, level: str, source: str | None, message: str) -> LogEntry:
        ts = now()
        log_id = self._exec("insert into logs (ts, level, source, message) values (?, ?, ?, ?)",
                            (ts, level, source, message))
        return LogEntry(log_id, ts, level, source, message)

    def recent_logs(self, limit: int = 500) -> list[LogEntry]:
        rows = self._all("select * from logs order by id desc limit ?", (limit,))
        return [LogEntry(**dict(r)) for r in reversed(rows)]

    def clear_logs(self) -> None:
        self._exec("delete from logs")

    # --- Трафік ---

    def add_traffic(self, nbytes: int, day: date | None = None) -> None:
        if nbytes <= 0:
            return
        self._exec(
            "insert into traffic (day, bytes) values (?, ?) on conflict(day) do update set bytes = bytes + excluded.bytes",
            ((day or date.today()).isoformat(), nbytes),
        )

    def traffic_bytes(self, day: date | None = None) -> int:
        row = self._one("select bytes from traffic where day = ?", ((day or date.today()).isoformat(),))
        return row["bytes"] if row else 0

    # --- Замовлення ---

    def orders(self) -> list[Order]:
        rows = self._all("select payload from orders order by created_at desc")
        return [order_from_dict(json.loads(r["payload"])) for r in rows]

    def order(self, number: str) -> Order | None:
        row = self._one("select payload from orders where number = ?", (number,))
        return order_from_dict(json.loads(row["payload"])) if row else None

    def upsert_order(self, order: Order) -> None:
        self._exec(
            "insert into orders (number, account, created_at, payload) values (?, ?, ?, ?)"
            " on conflict(number) do update set account = excluded.account,"
            " created_at = excluded.created_at, payload = excluded.payload",
            (order.number, order.account, order.created_at.isoformat(sep=" "),
             json.dumps(order_to_dict(order), ensure_ascii=False)),
        )


def order_to_dict(order: Order) -> dict:
    return {
        "number": order.number,
        "account": order.account,
        "created_at": order.created_at.isoformat(),
        "total": order.total,
        "status": order.status.value,
        "ttn": order.ttn,
        "recipient": vars(order.recipient),
        "delivery": vars(order.delivery),
        "payment": order.payment,
        "products": [vars(p) for p in order.products],
        "history": [{"status": e.status.value, "at": e.at.isoformat()} for e in order.history],
    }


def order_from_dict(d: dict) -> Order:
    return Order(
        number=d["number"],
        account=d["account"],
        created_at=datetime.fromisoformat(d["created_at"]),
        total=d["total"],
        status=OrderStatus(d["status"]),
        ttn=d.get("ttn"),
        recipient=Recipient(**d["recipient"]),
        delivery=Delivery(**d["delivery"]),
        payment=d["payment"],
        products=[Product(**p) for p in d["products"]],
        history=[HistoryEvent(OrderStatus(e["status"]), datetime.fromisoformat(e["at"])) for e in d["history"]],
    )
