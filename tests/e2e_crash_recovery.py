"""Наскрізна перевірка стабільності: краш браузера та закриття вкладки під час моніторингу.

Запуск:  ./venv/bin/python tests/e2e_crash_recovery.py
Усе у тимчасовій папці, секрети в пам'яті, Keychain і data/ не чіпаємо.
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="orderdesk-crash-")
os.environ["ORDERDESK_DATA"] = TMP
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine, EngineEvents  # noqa: E402
from dashboard.core.entities import TaskStatus  # noqa: E402
from dashboard.core.proxy import _free_port  # noqa: E402
from dashboard.core.secrets import set_secret  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402
from devsite.server import serve  # noqa: E402

failures: list[str] = []


def check(cond: bool, message: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + message)
    if not cond:
        failures.append(message)


def wait_for(predicate, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.3)
    return False


def logs(db) -> list[str]:
    return [e.message for e in db.recent_logs()]


def main() -> int:
    paths.ensure_dirs()
    port = _free_port()
    shop = serve(port=port, restock_after=10_000)
    threading.Thread(target=shop.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    SiteConfig(
        base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
        cart_url=f"{base}/cart", ip_check_url=f"{base}/ip", page_timeout_sec=15,
    ).save()

    db = Database(paths.db_path())
    acc = db.add_account("demo@example.com", None)
    set_secret("account", "demo@example.com", "demo12345")
    engine = Engine(db, EngineEvents())
    engine.start()

    print("Сценарій 1: Chrome аварійно завершився під час моніторингу")
    t1 = db.add_task(acc, f"{base}/product/watch", "monitor", 1)
    engine.start_tasks([t1])
    check(wait_for(lambda: db.task(t1).status == TaskStatus.MONITORING.value, 40),
          f"завдання моніториться ({db.task(t1).status}: {db.task(t1).last_log}; {engine.state_message})")
    if engine._browser is None:
        print("  Браузер не запустився — далі тестувати нема чого")
        engine.shutdown()
        return 1
    time.sleep(2)
    browser = engine._browser
    check(browser is not None, "браузер запущено")
    engine._submit(browser.close()).result(20)  # той самий ефект для рушія, що й краш процесу
    check(wait_for(lambda: any("перезапускаємо" in m for m in logs(db)), 30), "рушій помітив падіння й перезапустив браузер")
    time.sleep(3)
    check(db.task(t1).status == TaskStatus.MONITORING.value, f"завдання не впало: {db.task(t1).status}")
    check(engine._browser is not None and engine._browser.is_connected(), "новий браузер працює")
    urllib.request.urlopen(f"{base}/admin/restock/watch", timeout=5).read()  # товар з'явився вже ПІСЛЯ краху
    check(wait_for(lambda: db.task(t1).status == TaskStatus.DONE.value, 40),
          f"після відновлення товар знайдено: {db.task(t1).last_log}")

    print("Сценарій 2: користувач закрив вкладку (браузер живий) — завдання зупиняється, а не висить")
    t2 = db.add_task(acc, f"{base}/product/sneakers", "monitor", 1)
    engine.start_tasks([t2])
    check(wait_for(lambda: db.task(t2).status in (TaskStatus.MONITORING.value, TaskStatus.DONE.value), 40), "завдання стартувало")
    if db.task(t2).status == TaskStatus.MONITORING.value:
        async def close_tabs():
            for ctx in engine._browser.contexts:
                for page in ctx.pages:
                    await page.close()
        engine._submit(close_tabs()).result(20)
        check(wait_for(lambda: db.task(t2).status == TaskStatus.STOPPED.value, 30),
              f"завдання зупинено: {db.task(t2).status}")
        check(t2 not in engine.running_task_ids(), "завдання вийшло з активних")
    else:
        print("  (кросівки вже з'явилися на демо-сайті — сценарій пропущено)")

    engine.shutdown()
    shop.shutdown()
    print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
