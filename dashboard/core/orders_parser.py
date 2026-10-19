"""Збір історії замовлень з особистого кабінету магазину: номер, дата, сума, статус, ТТН.

Лише читання сторінки «Мої замовлення». Селектори — з SiteConfig («Налаштування» → «Замовлення в кабінеті»).
Результат зливається з таблицею orders: нові додаються, у наявних оновлюються статус/ТТН,
а кожна зміна статусу дописується в історію замовлення (таймлайн у панелі деталей).
"""
import re
from collections.abc import Callable
from datetime import datetime

from playwright.async_api import Error as PlaywrightError, Locator, Page

from ..models import Delivery, HistoryEvent, Order, OrderStatus, Recipient
from .db import Database
from .site_config import SiteConfig

# Ключові слова у тексті статусу магазину → статус програми (перевіряються по черзі)
STATUS_KEYWORDS: list[tuple[tuple[str, ...], OrderStatus]] = [
    (("скасов", "анульов", "відмін", "відмов", "cancel"), OrderStatus.CANCELLED),
    (("отриман", "виконан", "доставлено", "заверш", "received", "complete"), OrderStatus.RECEIVED),
    (("доставля", "в дорозі", "прямує", "у відділенні", "прибул"), OrderStatus.DELIVERING),
    (("передан", "відправлен", "shipped"), OrderStatus.SHIPPED),
    # «Очікує відправки» — це не «Очікує оплати»: перевіряємо раніше загального «очіку»
    (("очікує відправ", "готуєт", "комплект"), OrderStatus.CONFIRMED),
    (("очіку", "не оплач", "pending"), OrderStatus.AWAITING_PAYMENT),
    (("оплачен", "paid"), OrderStatus.PAID),
    (("підтвердж", "прийнят", "обробц", "новий", "сформов"), OrderStatus.CONFIRMED),
]
DATE_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%Y, %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_NUMBER_RE = re.compile(r"\d[\d\s  ]*(?:[.,]\d+)?")


def map_status(text: str) -> OrderStatus | None:
    lowered = text.casefold()
    for keywords, status in STATUS_KEYWORDS:
        if any(k in lowered for k in keywords):
            return status
    return None


def parse_amount(text: str | None) -> float:
    match = _NUMBER_RE.search(text or "")
    if not match:
        return 0.0
    digits = re.sub(r"[\s  ]", "", match.group()).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0


def parse_date(text: str | None) -> datetime | None:
    cleaned = " ".join((text or "").split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


async def _text(row: Locator, selector: str) -> str | None:
    """Текст першого збігу в рядку або None — без 30-секундного очікування на відсутній елемент."""
    if not selector:
        return None
    element = row.locator(selector)
    if await element.count() == 0:
        return None
    text = await element.first.inner_text(timeout=2000)
    return " ".join(text.split()) or None


async def fetch_user_orders(page: Page, cfg: SiteConfig, log: Callable[[str], None]) -> list[dict]:
    """Парсить сторінку «Мої замовлення». Помилки не ковтає: їх покаже рушій у логах."""
    log("Перехід до історії замовлень…")
    await page.goto(cfg.orders_url, wait_until="domcontentloaded")

    rows = page.locator(cfg.order_row_selector)
    try:
        # Список може домальовуватись JS-ом; порожній кабінет — не помилка
        await rows.first.wait_for(state="attached", timeout=5000)
    except PlaywrightError:
        log("На сторінці замовлень немає жодного рядка")
        return []

    orders = []
    for i in range(await rows.count()):
        row = rows.nth(i)
        order_id = await _text(row, cfg.order_id_selector)
        if not order_id:
            continue  # рядок-заголовок або порожня картка
        orders.append({
            "order_id": order_id.removeprefix("№").removeprefix("#").strip(),
            "date": await _text(row, cfg.order_date_selector),
            "total": await _text(row, cfg.order_total_selector),
            "status": await _text(row, cfg.order_status_selector) or "",
            "ttn": await _text(row, cfg.order_ttn_selector),
        })
    log(f"Знайдено {len(orders)} замовлень у кабінеті")
    return orders


def apply_orders(db: Database, account_email: str, rows: list[dict], log: Callable[[str], None],
                 on_change: Callable[[str, str, str | None, str | None], None] | None = None) -> tuple[int, int]:
    """Зливає зібрані рядки з базою. Повертає (нових, змінених).

    on_change(номер, новий статус, ТТН, старий статус) — для змінених замовлень (статус або нова ТТН).
    Нові замовлення його не викликають: перший збір не має засипати сповіщеннями про всю історію.
    """
    new = changed = 0
    now = datetime.now().replace(microsecond=0)
    for row in rows:
        status = map_status(row["status"])
        if status is None:
            log(f"№{row['order_id']}: невідомий статус «{row['status']}» — записано як «Підтверджено», "
                "додайте ключове слово в STATUS_KEYWORDS")
            status = OrderStatus.CONFIRMED
        ttn = row["ttn"] if row["ttn"] and row["ttn"] != "—" else None
        existing = db.order(row["order_id"])

        if existing is None:
            created = parse_date(row["date"]) or now
            db.upsert_order(Order(
                number=row["order_id"],
                account=account_email,
                created_at=created,
                total=parse_amount(row["total"]),
                status=status,
                ttn=ttn,
                # У списку замовлень немає отримувача/товарів — лишаємо прочерки
                recipient=Recipient("—", "—"),
                delivery=Delivery("—", "—", "—", "—"),
                payment="—",
                products=[],
                history=[HistoryEvent(status, created)],
            ))
            new += 1
            continue

        updated = False
        old_status = existing.status.value
        if existing.status != status:
            log(f"№{existing.number}: {existing.status.value} → {status.value}")
            existing.status = status
            existing.history.append(HistoryEvent(status, now))
            updated = True
        if ttn and existing.ttn != ttn:
            log(f"№{existing.number}: ТТН {ttn}")
            existing.ttn = ttn
            updated = True
        if updated:
            db.upsert_order(existing)
            changed += 1
            if on_change:
                on_change(existing.number, existing.status.value, existing.ttn, old_status)
    return new, changed
