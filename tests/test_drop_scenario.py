"""Тест «дропу»: сторінка товару вже є, але кнопки «Купити» немає; через кілька секунд вона з'являється.

Перевіряє справжній рушій (Engine) від початку до кінця на ІМІТОВАНОМУ сайті:
  P  — кнопка з'явилась, перевірки немає → бот має додати товар у кошик;
  C1 — після появи кнопки сайт показує перевірку безпеки при відкритті сторінки;
  C2 — перевірка з'являється після натискання «Купити».
У C1/C2 «людина» проходить перевірку через HUMAN_DELAY секунд — як це робите ви у вікні браузера.
Бот має почекати і сам довести справу до кінця.

ВАЖЛИВО: це НЕ перевіряє справжній захист coins.bank.gov.ua (Bunny Shield). Імітація доводить лише те,
що логіка бота правильна: стежить → бачить кнопку → чекає на людину → додає в кошик.
Чи пропустить справжній сайт автоматизований браузер без вашої участі — залежить від сайту, і тест
цього не обходить і не гарантує.

Запуск (потрібен екран, бо ручну перевірку можна пройти лише у видимому вікні):
    macOS:  ./venv/bin/python tests/test_drop_scenario.py
    Linux:  xvfb-run -a python tests/test_drop_scenario.py
    Окремий сценарій:  python tests/test_drop_scenario.py C1
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DROP_AFTER = 8        # через стільки секунд після старту «з'являється» кнопка
HUMAN_DELAY = 6       # стільки секунд «людина» проходить перевірку
INTERVAL = 5          # інтервал перевірки завдання, с
LIMIT = 120           # скільки максимум чекаємо результат, с

DATA = tempfile.mkdtemp(prefix="orderdesk-drop-")
os.environ["ORDERDESK_DATA"] = DATA
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402

STATE: dict = {}

CHALLENGE_HTML = """<html><head><title>Just a moment…</title></head><body>
<iframe src="/shield/challenge.html" style="width:300px;height:100px"></iframe>
<div>Establishing a secure connection…</div>
<script>setTimeout(()=>location.reload(), 1500)</script></body></html>"""

PRODUCT_JS = """
<script>
function counter(n){ document.getElementById('cart_counts').innerHTML = n ? '<div class="like-number-block"><span>'+n+'</span></div>' : ''; }
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('#r_buy_intovar button.buy'); if (!btn) return;
  const j = await (await fetch('/click', {method:'POST'})).json();
  if (j.action === 'challenge'){ location.reload(); return; }
  await fetch('/add', {method:'POST'});
  counter(1);
  const m = document.createElement('div'); m.className='modal in'; m.id='modal_cart_popup';
  m.style.cssText='position:fixed;top:50px;left:50px;width:400px;height:200px;background:#fff';
  m.innerHTML='<div class="cartContent_body"><a href="/p-2001.html">Монета</a></div>'; document.body.appendChild(m);
  document.getElementById('r_buy_intovar').innerHTML='<a class="btn-primary buy" href="shopping_cart.php">В кошику</a>';
});
</script>"""


def dropped() -> bool:
    return time.monotonic() >= STATE["drop_at"]


def start_human() -> None:
    """Запускає «людину»: через HUMAN_DELAY секунд перевірку пройдено."""
    if STATE["human_started"]:
        return
    STATE["human_started"] = True
    STATE["challenge_seen_at"] = time.monotonic()

    def solve():
        time.sleep(HUMAN_DELAY)
        STATE["passed"] = True
        STATE["passed_at"] = time.monotonic()

    threading.Thread(target=solve, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, ctype="text/html; charset=utf-8", status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        mode = STATE["mode"]
        if self.path.startswith("/p-2001.html"):
            STATE["page_loads"] += 1
            need_challenge = (mode == "C1" and dropped() and not STATE["passed"]) or \
                             (mode == "C2" and STATE["click_seen"] and not STATE["passed"])
            if need_challenge:
                start_human()
                return self._send(CHALLENGE_HTML)
            if dropped():
                STATE["button_shown_at"] = STATE["button_shown_at"] or time.monotonic()
                btn = ('<div id="r_buy_intovar"><a class="btn-primary buy" href="shopping_cart.php">В кошику</a></div>'
                       if STATE["in_cart"] else
                       '<div id="r_buy_intovar"><button type="submit" class="btn-primary buy">Купити</button></div>')
            else:
                btn = '<div id="r_buy_intovar"><span class="soon">Незабаром у продажу</span></div>'
            counter = '<div class="like-number-block"><span>1</span></div>' if STATE["in_cart"] else ""
            self._send(
                "<html><head><title>Монета</title></head><body>"
                f'<div id="cart_counts">{counter}</div>'
                "<a href=\"logoff.php\">Вихід</a>"
                "<h1 class='category_heading'>Ювілейна монета 2026</h1>"
                f"<div id='summ_price'>890 грн</div>{btn}{PRODUCT_JS}</body></html>")
        elif self.path.startswith("/shield/"):
            self._send("<html><body>challenge</body></html>")
        elif self.path.startswith("/popup_cart.php"):
            self._send('<div class="cartContent_body"><a href="p-2001.html">Монета</a></div>' if STATE["in_cart"]
                       else "<html><title>Помилка 404</title></html>")
        else:
            self._send("nf", status=404)

    def do_POST(self):
        if self.path == "/click":
            STATE["clicks"] += 1
            if STATE["mode"] == "C2" and not STATE["passed"]:
                STATE["click_seen"] = True
                start_human()
                return self._send(json.dumps({"action": "challenge"}), "application/json")
            self._send(json.dumps({"action": "ok"}), "application/json")
        elif self.path == "/add":
            STATE["in_cart"] = True
            STATE["added_at"] = time.monotonic()
            self._send(json.dumps({"ok": True}), "application/json")
        else:
            self._send("nf", status=404)


def run_scenario(mode: str, title: str, port: int) -> bool:
    STATE.clear()
    STATE.update(mode=mode, in_cart=False, passed=False, human_started=False, click_seen=False, clicks=0,
                 page_loads=0, button_shown_at=None, added_at=None, passed_at=None, challenge_seen_at=None,
                 drop_at=time.monotonic() + DROP_AFTER)
    base = f"http://127.0.0.1:{port}"
    paths.ensure_dirs()
    cfg = SiteConfig(
        base_url=base, login_url=f"{base}/login.php", account_check_url=f"{base}/account.php",
        cart_url=f"{base}/shopping_cart.php",
        email_selector="form[action*='login.php'] input[name='email_address']",
        password_selector="form[action*='login.php'] input[name='password']",
        title_selector="h1.category_heading", price_selector="#summ_price",
        in_stock_selector="#r_buy_intovar .buy:not(.login)", add_to_cart_selector="#r_buy_intovar .buy:not(.login)",
        page_timeout_sec=10, headless=False, max_concurrency=1, auto_checkout=False,
        telegram_enabled=False,
    )
    cfg.save()
    db = Database(Path(tempfile.mkdtemp(prefix="drop-db-")) / "t.db")
    account_id = db.add_account("tester@example.com", None)
    task_id = db.add_task(account_id, f"{base}/p-2001.html", "cart", INTERVAL)
    engine = Engine(db)
    engine.start()

    print(f"\n== {title}  [{mode}]")
    print(f"   Кнопка з'явиться через {DROP_AFTER} с; перевірка (якщо є) триватиме {HUMAN_DELAY} с.")
    started = time.monotonic()
    engine.start_tasks([task_id])
    status, last = "", ""
    deadline = started + LIMIT
    while time.monotonic() < deadline:
        task = db.task(task_id)
        if (task.status, task.last_log) != (status, last):
            status, last = task.status, task.last_log
            print(f"   [{time.monotonic() - started:5.1f} с] {status}: {(last or '')[:110]}")
        if status in ("Готово", "Помилка", "Зупинено"):
            break
        time.sleep(0.5)
    engine.stop_tasks([task_id])
    engine.shutdown()

    ok = status == "Готово" and STATE["in_cart"]
    reaction = (STATE["added_at"] - STATE["drop_at"]) if STATE["added_at"] else None
    waited_human = True
    if mode != "P":
        waited_human = STATE["passed"] and STATE["added_at"] is not None and STATE["added_at"] >= STATE["passed_at"]
        ok = ok and waited_human
    print(f"   Підсумок: статус={status}, у кошику={STATE['in_cart']}, кліків «Купити»={STATE['clicks']}, "
          f"завантажень сторінки={STATE['page_loads']}")
    if reaction is not None:
        print(f"   Від появи кнопки до додавання в кошик: {reaction:.1f} с (інтервал перевірки {INTERVAL} с)")
    if mode != "P":
        print(f"   Бот дочекався проходження перевірки і лише потім додав у кошик: {'так' if waited_human else 'НІ'}")
    print(f"   {'✓ ПРОЙДЕНО' if ok else '✗ ПРОВАЛЕНО'}")
    return ok


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    scenarios = [
        ("P", "Кнопка з'явилась, перевірки немає"),
        ("C1", "Перевірка безпеки при відкритті сторінки"),
        ("C2", "Перевірка безпеки після натискання «Купити»"),
    ]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = [run_scenario(m, t, port) for m, t in scenarios if only in (None, m)]
    print("\nУСІ СЦЕНАРІЇ ПРОЙДЕНО" if all(results) else "\nЄ ПРОВАЛИ", results)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
