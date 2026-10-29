"""Наскрізна перевірка автооформлення на devsite: пробний режим, ліміт ціни, 1 замовлення на завдання,
захист від повторних кліків по «Підтвердити».

Запуск:  ./venv/bin/python tests/e2e_checkout.py
Нічого не пише в Keychain і в data/: свій devsite на вільному порту, усе в тимчасовій папці.
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="orderdesk-e2e-checkout-")
os.environ["ORDERDESK_DATA"] = TMP
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.core import paths  # noqa: E402
from dashboard.core.checkout import CheckoutError, perform_checkout  # noqa: E402
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


class PrintEvents(EngineEvents):
    def task_changed(self, task_id, status, log):
        print(f"    task#{task_id}: {status} — {log}")


def run_task(engine: Engine, db: Database, task_id: int, timeout: float = 45) -> str:
    engine.start_tasks([task_id])
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        if task_id not in engine.running_task_ids() and db.task(task_id).status in (
                TaskStatus.DONE.value, TaskStatus.ERROR.value):
            break
    return db.task(task_id).status


def engine_scenarios(base: str, shop) -> None:
    state = shop.state
    db = Database(paths.db_path())
    account = db.add_account("demo@example.com", None)
    set_secret("account", "demo@example.com", "demo12345")
    engine = Engine(db, PrintEvents())
    engine.start()
    hoodie = f"{base}/product/hoodie"

    def demo_orders() -> list[dict]:
        return state.orders["demo@example.com"]

    seed_orders = len(demo_orders())

    print("Налаштування за замовчуванням:")
    defaults = SiteConfig()
    check(defaults.checkout_dry_run is True, "пробний режим увімкнено за замовчуванням")
    check(defaults.auto_checkout is False, "автооформлення вимкнене, доки його не ввімкнути")
    check(defaults.max_order_price == 0, "ліміт ціни не задано → справжні замовлення заборонені")

    print("1. Пробний режим (лише auto_checkout=True, решта — за замовчуванням):")
    engine.config = replace(engine.config, auto_checkout=True)
    t = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, t) == TaskStatus.DONE.value, f"завдання виконано: {db.task(t).last_log}")
    check("пробний чекаут" in (db.task(t).last_log or ""), "у статусі видно, що це пробний чекаут")
    check(state.checkout_posts == 0, "кнопку «Підтвердити» не натиснуто (0 POST /checkout)")
    check(len(demo_orders()) == seed_orders, "нових замовлень у магазині немає")
    check(db.checkout_block(t) is None, "пробний режим не займає ліміт «1 замовлення на завдання»")
    state.carts.clear()

    print("2. Без ліміту ціни справжнє замовлення заблоковане:")
    engine.config = replace(engine.config, checkout_dry_run=False, max_order_price=0)
    t = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, t) == TaskStatus.ERROR.value, f"помилка: {db.task(t).last_log}")
    check(state.checkout_posts == 0, "кнопку «Підтвердити» не натиснуто")
    state.carts.clear()

    print("3. Ціна товару вище ліміту → навіть у кошик не кладемо:")
    engine.config = replace(engine.config, max_order_price=1000)   # худі 1 299 грн
    t = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, t) == TaskStatus.ERROR.value, f"помилка: {db.task(t).last_log}")
    check(not state.carts.get("demo@example.com"), "кошик порожній")
    check(state.checkout_posts == 0, "кнопку «Підтвердити» не натиснуто")

    print("4. Сума в чекауті вище ліміту (у кошику вже лежали кросівки):")
    engine.config = replace(engine.config, max_order_price=2000)
    state.carts["demo@example.com"] = ["sneakers"]                 # 3 899 + 1 299 = 5 198 грн
    t = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, t) == TaskStatus.ERROR.value, f"помилка: {db.task(t).last_log}")
    check("5198" in (db.task(t).last_log or ""), "звірено суму з чекауту, а не ціну з картки")
    check(state.checkout_posts == 0, "кнопку «Підтвердити» не натиснуто")
    state.carts.clear()

    print("5. Справжнє замовлення в межах ліміту:")
    engine.config = replace(engine.config, max_order_price=5000)
    live = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, live) == TaskStatus.DONE.value, f"виконано: {db.task(live).last_log}")
    check(state.checkout_posts == 1, f"рівно один POST /checkout ({state.checkout_posts})")
    created = demo_orders()[0]
    check(len(demo_orders()) == seed_orders + 1 and created["total"] == "1 299 грн",
          f"у кабінеті магазину нове замовлення №{created['id']} на {created['total']}")
    block = db.checkout_block(live)
    check(block is not None and block["status"] == "success" and block["order_number"] == created["id"],
          "у базі: чекаут success з тим самим номером")

    print("6. Повторний запуск того ж завдання — друге замовлення не створюється:")
    check(run_task(engine, db, live) == TaskStatus.DONE.value, f"статус: {db.task(live).last_log}")
    check(state.checkout_posts == 1, "POST /checkout так і лишився один")
    check(not state.carts.get("demo@example.com"), "у кошик повторно не додано")
    check(db.claim_checkout(live, account, 1299) is None, "резерв у БД відхиляє друге замовлення")

    print("7. Невідомий результат після «Підтвердити» теж блокує повтор:")
    engine.config = replace(engine.config, order_number_selector="#no-such-element", page_timeout_sec=5)
    unknown = db.add_task(account, hoodie, "cart", 1)
    check(run_task(engine, db, unknown) == TaskStatus.ERROR.value, f"помилка: {db.task(unknown).last_log}")
    check(state.checkout_posts == 2, "кнопку натиснуто один раз")
    check((db.checkout_block(unknown) or {"status": None})["status"] == "unknown", "чекаут позначено «unknown»")
    engine.config = replace(engine.config, order_number_selector=".order-number", page_timeout_sec=15)
    check(run_task(engine, db, unknown) == TaskStatus.DONE.value and state.checkout_posts == 2,
          f"повторний запуск нічого не оформлює: {db.task(unknown).last_log}")

    engine.shutdown()
    db.close()


async def double_click_scenarios(base: str, shop) -> None:
    from playwright.async_api import async_playwright

    state = shop.state
    cfg = replace(SiteConfig(), base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
                  cart_url=f"{base}/cart", checkout_dry_run=False, max_order_price=10_000, page_timeout_sec=15)

    async def logged_in_page(browser):
        page = await (await browser.new_context()).new_page()
        await page.goto(cfg.login_url)
        await page.fill(cfg.email_selector, "second@example.com")
        await page.fill(cfg.password_selector, "second12345")
        await page.click(cfg.submit_selector)
        state.carts["second@example.com"] = ["hoodie"]
        return page

    async def hammer(page) -> None:
        """Щойно пішов перший POST — ще 3 кліки, requestSubmit і Enter, поки сервер «думає»."""
        await page.wait_for_event("request", predicate=lambda r: r.method == "POST" and "/checkout" in r.url)
        for _ in range(3):
            try:
                await page.evaluate("""() => {
                    document.querySelector('#place-order')?.click();
                    document.querySelector('form')?.requestSubmit();
                }""")
                await page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001 — сторінка могла вже піти на «Дякуємо»
                pass
            await asyncio.sleep(0.1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        state.checkout_delay = 2.0

        print("8. Контроль: без захисту повторні кліки створюють дублі (devsite не захищений):")
        page = await logged_in_page(browser)
        before = state.checkout_posts
        await page.goto(f"{base}/checkout")
        await page.fill("#city", "Київ")
        await page.fill("#warehouse", "1")
        await page.check("#pay-cod")
        clicks = asyncio.create_task(hammer(page))
        await page.click("#place-order")
        await clicks
        await asyncio.sleep(state.checkout_delay * 4 + 1)
        control_posts = state.checkout_posts - before
        check(control_posts > 1, f"без захисту: {control_posts} POST /checkout — тест справді ловить дублі")
        await page.context.close()

        print("9. perform_checkout із захистом: кліки, requestSubmit і Enter під час відправки:")
        page = await logged_in_page(browser)
        before = state.checkout_posts
        reserved: list[int | None] = []

        async def reserve(total):
            reserved.append(total)

        clicks = asyncio.create_task(hammer(page))
        result = await perform_checkout(page, cfg, lambda m: print("    ", m), reserve)
        await clicks
        await asyncio.sleep(state.checkout_delay * 2)
        check(state.checkout_posts - before == 1, f"рівно один POST /checkout ({state.checkout_posts - before})")
        check(result["order_number"] and result["total"] == 1299, f"замовлення №{result['order_number']}, 1299 грн")
        check(reserved == [1299], "резерв викликано один раз, до кліку, з сумою з чекауту")

        print("10. Резерв відмовив → кнопку не натиснуто:")
        state.carts["second@example.com"] = ["hoodie"]
        before = state.checkout_posts

        async def refuse(total):
            raise CheckoutError("вже є замовлення")

        try:
            await perform_checkout(page, cfg, lambda m: None, refuse)
            check(False, "мала бути CheckoutError")
        except CheckoutError as exc:
            check(not exc.submitted, "помилка до натискання (submitted=False)")
        check(state.checkout_posts == before, "POST /checkout не було")
        await browser.close()


def main() -> int:
    paths.ensure_dirs()
    port = _free_port()
    shop = serve(port=port, restock_after=0)
    threading.Thread(target=shop.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    SiteConfig(base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
               cart_url=f"{base}/cart", orders_url=f"{base}/cabinet/orders", ip_check_url=f"{base}/ip",
               page_timeout_sec=15).save()

    engine_scenarios(base, shop)
    asyncio.run(double_click_scenarios(base, shop))
    shop.shutdown()

    print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
