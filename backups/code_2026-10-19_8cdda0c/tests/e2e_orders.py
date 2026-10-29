"""Перевірка збору замовлень з кабінету: devsite → рушій → таблиця orders.

Запуск:  ./venv/bin/python tests/e2e_orders.py
"""
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk-orders-")
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine  # noqa: E402
from dashboard.core.orders_parser import map_status, parse_amount, parse_date  # noqa: E402
from dashboard.core.proxy import _free_port  # noqa: E402
from dashboard.core.secrets import set_secret  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402
from dashboard.models import OrderStatus  # noqa: E402
from devsite.server import serve  # noqa: E402

failures = []


def check(cond, message):
    print(("  ✓ " if cond else "  ✗ ") + message)
    if not cond:
        failures.append(message)


print("Розбір тексту:")
check(map_status("Доставляється") == OrderStatus.DELIVERING, "«Доставляється» → Доставляється")
check(map_status("Очікує відправки") == OrderStatus.CONFIRMED, "«Очікує відправки» → Підтверджено (не «Очікує оплати»)")
check(map_status("Очікує оплати") == OrderStatus.AWAITING_PAYMENT, "«Очікує оплати» → Очікує оплати")
check(map_status("Скасовано") == OrderStatus.CANCELLED, "«Скасовано» → Анульовано")
check(map_status("щось дивне") is None, "невідомий статус → None")
check(parse_amount("3 899 грн") == 3899 and parse_amount("1 299,50 ₴") == 1299.5, "суми з пробілами й комою")
check(parse_date("28.09.2026 14:20").hour == 14 and parse_date("2026-09-21").day == 21, "дати в різних форматах")

paths.ensure_dirs()
port = _free_port()
shop = serve(port=port, restock_after=0)
threading.Thread(target=shop.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
SiteConfig(base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
           cart_url=f"{base}/cart", orders_url=f"{base}/cabinet/orders", page_timeout_sec=15).save()

db = Database(paths.db_path())
demo = db.add_account("demo@example.com", None)
set_secret("account", "demo@example.com", "demo12345")
second = db.add_account("second@example.com", None)
set_secret("account", "second@example.com", "second12345")

engine = Engine(db)
engine.start()

print("Перший збір:")
engine.sync_orders().result(90)
orders = {o.number: o for o in db.orders()}
check(set(orders) == {"100245", "100198", "100301", "100150"}, f"зібрано 4 замовлення двох акаунтів: {sorted(orders)}")
o = orders.get("100245")
check(o and o.status == OrderStatus.DELIVERING and o.ttn == "20450000000001" and o.total == 3899,
      "№100245: статус, ТТН і сума")
check(o and o.account == "demo@example.com" and o.created_at.strftime("%d.%m %H:%M") == "28.09 14:20",
      "№100245: акаунт і дата з кабінету")
check(orders.get("100301") and orders["100301"].ttn is None, "№100301 без ТТН")
check(orders.get("100150") and orders["100150"].account == "second@example.com"
      and orders["100150"].status == OrderStatus.CANCELLED, "№100150 другого акаунта — Анульовано")

print("Зміна статусу в магазині → повторний збір:")
urllib.request.urlopen(f"{base}/admin/order/100301?status=%D0%92%D1%96%D0%B4%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%BE"
                       "&ttn=20450000000099").read()  # status=Відправлено
engine.sync_orders([demo]).result(90)
o = db.order("100301")
check(o.status == OrderStatus.SHIPPED and o.ttn == "20450000000099", "№100301 → «Передано логістичній компанії», ТТН з'явилась")
check([e.status for e in o.history] == [OrderStatus.CONFIRMED, OrderStatus.SHIPPED], "в історію дописано подію зміни статусу")
check(len(db.orders()) == 4, "дублікатів немає")
check(any("змінених 1" in e.message for e in db.recent_logs()), "у логах «нових 0, змінених 1»")

print("Невірний пароль:")
set_secret("account", "second@example.com", "WRONG")
engine.state_path(second).unlink(missing_ok=True)
engine.sync_orders([second]).result(90)
check(db.account(second).status == "Помилка", "акаунт позначено «Помилка», решта даних не зіпсована")
check(len(db.orders()) == 4, "замовлення на місці")

engine.shutdown()
shop.shutdown()
print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
sys.exit(1 if failures else 0)
