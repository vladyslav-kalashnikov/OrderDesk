"""Перевірка Telegram-сповіщень на фейковому API (справжній Telegram не чіпаємо).

Запуск:  ./venv/bin/python tests/e2e_telegram.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk-tg-")
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core.proxy import _free_port  # noqa: E402

GOOD_TOKEN = "123456:GOOD-secret-token"
sent: list[dict] = []


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != f"/bot{GOOD_TOKEN}/sendMessage":
            answer, code = {"ok": False, "description": "Unauthorized"}, 401
        elif body["chat_id"] != "777":
            answer, code = {"ok": False, "description": "Bad Request: chat not found"}, 400
        else:
            sent.append(body)
            answer, code = {"ok": True, "result": {}}, 200
        data = json.dumps(answer).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


tg_port = _free_port()
tg = ThreadingHTTPServer(("127.0.0.1", tg_port), FakeTelegram)
threading.Thread(target=tg.serve_forever, daemon=True).start()
os.environ["ORDERDESK_TELEGRAM_API"] = f"http://127.0.0.1:{tg_port}"

from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine  # noqa: E402
from dashboard.core.notifier import send_telegram_notification  # noqa: E402
from dashboard.core.secrets import set_secret  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402
from devsite.server import serve  # noqa: E402

failures = []


def check(cond, message):
    print(("  ✓ " if cond else "  ✗ ") + message)
    if not cond:
        failures.append(message)


print("Модуль notifier:")
ok, _ = asyncio.run(send_telegram_notification(GOOD_TOKEN, "777", "Футболка <XL> & H&M", "499 грн", "a@b.c",
                                               payment_url="https://www.liqpay.ua/checkout/abc"))
msg = sent[-1]
check(ok and "Футболка &lt;XL&gt; &amp; H&amp;M" in msg["text"], "назва з < > & екранована")
check(msg["parse_mode"] == "HTML", "parse_mode HTML")
check(msg["reply_markup"]["inline_keyboard"][0][0] == {"text": "💳 Оплатити замовлення",
                                                        "url": "https://www.liqpay.ua/checkout/abc"},
      "кнопка «Оплатити» з посиланням")
asyncio.run(send_telegram_notification(GOOD_TOKEN, "777", "X", None, "a@b.c", payment_url="javascript:alert(1)"))
check("reply_markup" not in sent[-1] and "Потрібна оплата" not in sent[-1]["text"],
      "не-http посилання відкинуто (інакше Telegram відхилив би все повідомлення)")
ok, detail = asyncio.run(send_telegram_notification("bad-token", "777", "X", None, "a@b.c"))
check(not ok and "401" in detail and "Unauthorized" in detail and "bad-token" not in detail,
      f"невірний токен: «{detail}» — без токена в тексті")
ok, detail = asyncio.run(send_telegram_notification(GOOD_TOKEN, "000", "X", None, "a@b.c"))
check(not ok and "chat not found" in detail, f"невірний chat id: «{detail}»")

print("Через рушій:")
paths.ensure_dirs()
port = _free_port()
shop = serve(port=port, restock_after=0)
threading.Thread(target=shop.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
SiteConfig(base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account", cart_url=f"{base}/cart",
           orders_url=f"{base}/cabinet/orders", telegram_enabled=True, telegram_chat_id="777").save()
set_secret("telegram", "token", GOOD_TOKEN)

db = Database(paths.db_path())
acc = db.add_account("demo@example.com", None)
set_secret("account", "demo@example.com", "demo12345")
t_monitor = db.add_task(acc, f"{base}/product/hoodie", "monitor", 1)
t_cart = db.add_task(acc, f"{base}/product/sneakers", "cart", 1)

engine = Engine(db)
engine.start()
sent.clear()
engine.start_tasks([t_monitor, t_cart])
for _ in range(60):
    if all(db.task(t).status == "Готово" for t in (t_monitor, t_cart)):
        break
    time.sleep(0.5)
time.sleep(1)
texts = [m["text"] for m in sent]
check(any("Товар з'явився в наявності" in t and "Худі Basic Grey" in t for t in texts), "моніторинг → «з'явився в наявності»")
check(any("Товар додано в кошик" in t and "Кросівки Air Runner 42" in t and "3 899 грн" in t for t in texts),
      "кошик → «додано в кошик» з ціною")
check(all(m["reply_markup"]["inline_keyboard"][0][0]["text"] == "🔗 Відкрити товар" for m in sent),
      "кнопка «Відкрити товар» з посиланням на товар")

print("Замовлення: перший збір без спаму, далі — лише зміни:")
sent.clear()
engine.sync_orders().result(60)
check(len(sent) == 0, "перший збір (3 нові замовлення) — без сповіщень")
urllib.request.urlopen(f"{base}/admin/order/100301?status=%D0%92%D1%96%D0%B4%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%BE"
                       "&ttn=20450000000099").read()  # Відправлено
engine.sync_orders().result(60)
check(len(sent) == 1, f"зміна статусу → одне сповіщення (надіслано {len(sent)})")
if sent:
    text = sent[0]["text"]
    check("№100301" in text and "Підтверджено → Передано логістичній компанії" in text
          and "20450000000099" in text, "у сповіщенні номер, «старий → новий» статус і ТТН")
    check("cargo_number=20450000000099" in sent[0]["reply_markup"]["inline_keyboard"][0][0]["url"],
          "кнопка відстеження на Новій Пошті")

print("Тестове повідомлення з налаштувань:")
ok, detail = engine.send_test_notification("777", None).result(15)
check(ok, "збережений токен з Keychain — надіслано")
ok, detail = engine.send_test_notification("777", "wrong").result(15)
check(not ok and "Unauthorized" in detail, f"введений невірний токен — «{detail}»")
check(not any(GOOD_TOKEN in e.message for e in db.recent_logs()), "токен ніде не потрапив у логи")

engine.shutdown()
shop.shutdown()
tg.shutdown()
print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
sys.exit(1 if failures else 0)
