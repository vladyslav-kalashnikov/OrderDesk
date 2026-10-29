"""Перевірка логіки Engine._add_to_cart на імітації сайту НБУ (Bunny Shield, черга, модальний кошик).

Це НЕ справжній сайт: тест перевіряє лише, що код правильно чекає/підтверджує/кліка в різних сценаріях.
Запуск:  python tests/mock_nbu_add_to_cart.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="orderdesk-mock-")
os.environ["ORDERDESK_DATA"] = TMP
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.engine import Engine, EngineEvents, LoginError  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402

STATE = {"scenario": "A", "in_cart": False, "challenged": False, "clicks": 0}

HEADER = """<div class="small-menu-basket popup_cart"><a href="#" class="small-wrap-a" aria-label="Кошик"></a>
<div id="cart_counts">{counter}</div></div>
<a href="account_history.php" aria-label="Кабінет">Кабінет</a><a href="logoff.php">Вихід</a>"""

PRODUCT_JS = """
<script>
const SCENARIO = "%(scenario)s";
function counter(n){ document.getElementById('cart_counts').innerHTML = n ? '<div class="like-number-block"><span>'+n+'</span></div>' : ''; }
function showPopup(){
  const m = document.createElement('div'); m.className='modal in'; m.id='modal_cart_popup';
  m.style.cssText='position:fixed;top:50px;left:50px;width:400px;height:200px;background:#fff';
  m.innerHTML='<div class="cartContent_body"><a href="/p-1183.html">Архістратиг</a></div>'; document.body.appendChild(m);
}
async function doAdd(){
  const r = await fetch('/add', {method:'POST'}); const j = await r.json();
  if (j.ok){ counter(1); showPopup(); const b=document.getElementById('r_buy_intovar'); b.innerHTML='<a class="btn-primary buy" href="shopping_cart.php">В кошику</a>'; }
}
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('#r_buy_intovar button.buy'); if (!btn) return;
  const r = await fetch('/click', {method:'POST'}); const j = await r.json();
  if (j.action === 'challenge'){
    document.body.innerHTML = '<iframe src="/shield/challenge.html" style="width:300px;height:100px"></iframe><div>Establishing a secure connection...</div>';
    setTimeout(()=>location.reload(), 4000); return;
  }
  if (j.action === 'slow'){
    btn.parentElement.insertAdjacentHTML('beforeend','<div id="prodBtnLoadingSpinner">...</div>'); btn.classList.add('clicked');
    setTimeout(()=>{ document.getElementById('prodBtnLoadingSpinner').remove(); doAdd(); }, 4000); return;
  }
  if (j.action === 'silent_fail'){ return; }
  if (j.action === 'server_only'){ return; }   // додано на сервері, UI не оновлюється
  doAdd();
});
</script>
"""


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
        sc = STATE["scenario"]
        if self.path.startswith("/p-1183.html"):
            if STATE["in_cart"] and sc == "C":
                btn = '<div id="r_buy_intovar"><a class="btn-primary buy" href="shopping_cart.php">В кошику</a></div>'
            else:
                btn = '<div id="r_buy_intovar"><button type="submit" class="btn-primary buy">Купити</button></div>'
            counter = '<div class="like-number-block"><span>1</span></div>' if STATE["in_cart"] else ""
            self._send(f"<html><head><title>Тест</title></head><body>{HEADER.format(counter=counter)}"
                       f"<h1 class='category_heading'>Тест</h1>{btn}{PRODUCT_JS % {'scenario': sc}}</body></html>")
        elif self.path.startswith("/shield/"):
            self._send("<html><body>challenge</body></html>")
        elif self.path.startswith("/popup_cart.php"):
            if STATE["in_cart"]:
                self._send('<div class="cartContent_body"><a href="p-1183.html">товар</a></div>')
            else:
                self._send("<html><title>Помилка 404</title></html>")
        elif self.path.startswith("/shopping_cart.php"):
            self._send("<html><title>Помилка 404</title></html>", status=200)
        else:
            self._send("nf", status=404)

    def do_POST(self):
        sc = STATE["scenario"]
        STATE["clicks"] += 1
        if self.path == "/click":
            action = "normal"
            if sc == "B" and not STATE["challenged"]:
                STATE["challenged"] = True
                action = "challenge"
            elif sc == "E":
                action = "slow"
            elif sc == "D1":
                action = "silent_fail"
            elif sc == "D2":
                action = "server_only"
                STATE["in_cart"] = True
            self._send(json.dumps({"action": action}), "application/json")
        elif self.path == "/add":
            STATE["in_cart"] = True
            self._send(json.dumps({"ok": True}), "application/json")
        else:
            self._send("nf", status=404)


class StubDB:
    def __init__(self):
        self.logs, self.cart = [], []

    def add_log(self, level, source, message):
        self.logs.append((level, source, message))
        print(f"      [{level}] {message[:170]}")
        return (level, source, message)

    def add_cart_item(self, *a):
        self.cart.append(a)

    def update_task(self, *a, **k):
        pass


class Acc:
    id = 3
    email = "t@example.com"
    status = "active"


async def run_case(name, scenario, expect_ok, expect_clicks=None, expect_log=None):
    from playwright.async_api import async_playwright
    STATE.update(scenario=scenario, in_cart=False, challenged=False, clicks=0)
    if scenario == "C":
        STATE["in_cart"] = True
    base = f"http://127.0.0.1:{PORT}"
    cfg = SiteConfig(
        base_url=base, login_url=f"{base}/login.php", account_check_url=f"{base}/account.php",
        cart_url=f"{base}/shopping_cart.php",
        email_selector="form[action*='login.php'] input[name='email_address']",
        password_selector="form[action*='login.php'] input[name='password']",
        add_to_cart_selector="#r_buy_intovar .buy:not(.login)", page_timeout_sec=6, headless=False,
    )
    eng = Engine.__new__(Engine)
    eng.config, eng.db, eng.events = cfg, StubDB(), EngineEvents()
    eng._task_status = lambda *a, **k: None
    print(f"\n== {name} (сценарій {scenario})")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(f"{base}/p-1183.html")
        started = time.monotonic()
        ok, err = True, ""
        try:
            await eng._add_to_cart(page, 1, f"{base}/p-1183.html", Acc(), "Тест", "1 грн")
        except (RuntimeError, LoginError) as exc:
            ok, err = False, str(exc)[:160]
        took = time.monotonic() - started
        await browser.close()
    good = ok == expect_ok
    if expect_clicks is not None:
        good = good and STATE["clicks"] == expect_clicks
    if expect_log:
        good = good and any(expect_log in m for _, _, m in eng.db.logs)
    print(f"   {'✓' if good else '✗'} {name}: ok={ok} кліків-запитів={STATE['clicks']} за {took:.1f} с {err}")
    return good


async def main():
    results = [
        await run_case("Звичайне додавання", "A", True, expect_log="підтверджено сайтом"),
        await run_case("Перевірка захисту → повтор кліку", "B", True),
        await run_case("Товар уже в кошику", "C", True, expect_clicks=0, expect_log="уже в кошику"),
        await run_case("Черга/спінер, потім додано", "E", True, expect_clicks=2),
        await run_case("Додано на сервері, UI не оновився", "D2", True, expect_clicks=1, expect_log="повторний клік не потрібен"),
        await run_case("Сайт мовчки не додає", "D1", False),
    ]
    print("\nПідсумок:", "УСІ СЦЕНАРІЇ ОК" if all(results) else "ЄСТЬ ПОМИЛКИ", results)
    return 0 if all(results) else 1


if __name__ == "__main__":
    paths.ensure_dirs()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sys.exit(asyncio.run(main()))
