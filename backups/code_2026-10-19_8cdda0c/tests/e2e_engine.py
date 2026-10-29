"""Наскрізна перевірка рушія без UI: devsite + SOCKS5 з паролем + 4 завдання.

Запуск:  ./venv/bin/python tests/e2e_engine.py
Нічого не пише в Keychain і в data/: усе в тимчасовій папці, секрети в пам'яті.
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="orderdesk-e2e-")
os.environ["ORDERDESK_DATA"] = TMP
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine, EngineEvents  # noqa: E402
from dashboard.core.entities import AccountStatus, ProxyStatus, TaskStatus  # noqa: E402
from dashboard.core.proxy import _free_port  # noqa: E402
from dashboard.core.secrets import set_secret  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402
from devsite.server import serve  # noqa: E402

failures: list[str] = []


def check(cond: bool, message: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + message)
    if not cond:
        failures.append(message)


def start_socks_server(port: int, counter: list) -> None:
    """SOCKS5 з паролем на 127.0.0.1 — імітація платного проксі."""
    import pproxy

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server = pproxy.Server(f"socks5://127.0.0.1:{port}#tuser:tpass")
        loop.run_until_complete(server.start_server({"rserver": [], "verbose": lambda *a: counter.append(a)}))
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.5)


class PrintEvents(EngineEvents):
    def task_changed(self, task_id, status, log):
        print(f"    task#{task_id}: {status} — {log}")


def main() -> int:
    paths.ensure_dirs()
    shop_port, socks_port = _free_port(), _free_port()
    shop = serve(port=shop_port, restock_after=4)
    threading.Thread(target=shop.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{shop_port}"

    socks_hits: list = []
    start_socks_server(socks_port, socks_hits)

    config = SiteConfig(
        base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
        cart_url=f"{base}/cart", ip_check_url=f"{base}/ip", page_timeout_sec=15,
    )
    config.save()

    db = Database(paths.db_path())
    proxy_id = db.add_proxy("socks5", "127.0.0.1", socks_port, "tuser", "Тестовий SOCKS5")
    set_secret("proxy", proxy_id, "tpass")
    bad_proxy_id = db.add_proxy("socks5", "127.0.0.1", socks_port, "tuser", "SOCKS5 з невірним паролем")
    set_secret("proxy", bad_proxy_id, "WRONG")

    good = db.add_account("demo@example.com", proxy_id)
    set_secret("account", "demo@example.com", "demo12345")
    bad = db.add_account("second@example.com", None)
    set_secret("account", "second@example.com", "wrong-password")

    t_cart = db.add_task(good, f"{base}/product/sneakers", "cart", 1)
    t_monitor = db.add_task(good, f"{base}/product/hoodie", "monitor", 1)
    t_never = db.add_task(good, f"{base}/product/watch", "monitor", 1)
    t_badlogin = db.add_task(bad, f"{base}/product/hoodie", "monitor", 1)

    engine = Engine(db, PrintEvents())
    engine.start()

    print("Перевірка проксі:")
    engine.check_proxy(proxy_id).result(60)
    engine.check_proxy(bad_proxy_id).result(60)
    p_ok, p_bad = db.proxy(proxy_id), db.proxy(bad_proxy_id)
    check(p_ok.status == ProxyStatus.OK.value and p_ok.last_ip == "127.0.0.1",
          f"правильний SOCKS5 працює (IP {p_ok.last_ip}, {p_ok.latency_ms} мс)")
    check(p_bad.status == ProxyStatus.FAIL.value, "SOCKS5 з невірним паролем позначено «Не працює»")

    print("Завдання:")
    engine.start_tasks([t_cart, t_monitor, t_never, t_badlogin])
    deadline = time.time() + 60
    while time.time() < deadline:
        statuses = {t: db.task(t).status for t in (t_cart, t_monitor, t_badlogin)}
        if all(s in (TaskStatus.DONE.value, TaskStatus.ERROR.value) for s in statuses.values()):
            break
        time.sleep(0.5)
    check(db.task(t_never).status == TaskStatus.MONITORING.value, "годинник (немає в наявності) ще моніториться")
    engine.stop_tasks([t_never])
    time.sleep(3)

    check(db.task(t_cart).status == TaskStatus.DONE.value, f"кросівки додано в кошик: {db.task(t_cart).last_log}")
    check(db.task(t_monitor).status == TaskStatus.DONE.value, f"худі знайдено: {db.task(t_monitor).last_log}")
    check(db.task(t_never).status == TaskStatus.STOPPED.value, "годинник зупинено кнопкою «Стоп»")
    check(db.task(t_badlogin).status == TaskStatus.ERROR.value, f"невірний пароль → помилка: {db.task(t_badlogin).last_log}")
    check(db.account(bad).status == AccountStatus.ERROR.value, "акаунт з невірним паролем позначено «Помилка»")
    check(db.account(good).status == AccountStatus.ACTIVE.value, "робочий акаунт активний")
    check(engine.state_path(good).exists(), "сесію (storage_state) збережено у файл")
    check(oct(engine.state_path(good).stat().st_mode & 0o777) == "0o600", "файл сесії доступний лише власнику")
    cart = db.cart_items()
    check(len(cart) == 1 and cart[0].title == "Кросівки Air Runner 42", f"кошик у базі: {[c.title for c in cart]}")
    check(len(socks_hits) > 0, f"трафік ішов через SOCKS5 ({len(socks_hits)} з'єднань)")
    stats = db.daily_stats()
    check((stats.runs, stats.success, stats.errors) == (4, 2, 1), f"статистика: {stats.runs} запусків, "
          f"{stats.success} успішних, {stats.errors} помилок")

    print("Повторний запуск (сесія з файлу, без входу):")
    logins_before = sum("Вхід у призначений акаунт" in e.message for e in db.recent_logs())
    t_again = db.add_task(good, f"{base}/product/hoodie", "monitor", 1)
    engine.start_tasks([t_again])
    for _ in range(40):
        if db.task(t_again).status == TaskStatus.DONE.value:
            break
        time.sleep(0.5)
    logins_after = sum("Вхід у призначений акаунт" in e.message for e in db.recent_logs())
    check(db.task(t_again).status == TaskStatus.DONE.value, "завдання виконано")
    check(logins_after == logins_before, "повторного входу не було — використано збережену сесію")

    engine.shutdown()
    check(db.traffic_bytes() > 0, f"трафік пораховано: {db.traffic_bytes() / 1024:.1f} KB")
    shop.shutdown()

    print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
