"""Імітація релізу: 20 акаунтів одночасно, кожен зі своїм «капризом» сайту/проксі.

Перевіряє, що ВСІ акаунти додають монету в кошик, нікому не додається зайвого (подвійні кліки
після успіху), і що 20 акаунтів справді працюють паралельно, а не чергою.
"""
import asyncio
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_cart_scenarios import FakePage  # noqa: E402
from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine  # noqa: E402

ACCOUNTS = 20
# Сценарії FakePage, що закінчуються успішним додаванням: миттєво, Turnstile, закрита галочка,
# жовта→зелена кнопка, помилка «Спробуйте ще раз», повільна капча, зникаюча галочка, зависання+оновлення.
SCENARIOS = [1, 3, 4, 5, 6, 7, 12, 14]
TIME_BUDGET_SEC = 6.0   # з прискореними таймерами (реальні ×~10 повільніші, логіка та сама)


async def main() -> None:
    os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk_release_")
    db = Database(paths.db_path())
    engine = Engine(db)
    engine.TURNSTILE_CLICK_DELAY_SEC = 0.15
    engine.TURNSTILE_CONFIRM_CLICK_SEC = 0.45
    engine.STUCK_AFTER_TURNSTILE_SEC = 0.9
    engine.STUCK_WITHOUT_TURNSTILE_SEC = 0.8
    engine.RECLICK_GREEN_MIN_WAIT_SEC = 0.25
    engine.GREEN_IDLE_RECLICK_SEC = 0.5
    engine.UNSTICK_YELLOW_SEC = 0.4

    url = "https://coins.bank.gov.ua/-bosporske-carstvo-u-phutljari/p-885.html"
    rng = random.Random(2026)
    pages, accounts, tasks, scen = [], [], [], []
    for i in range(ACCOUNTS):
        aid = db.add_account(f"release{i}@example.com", None)
        accounts.append(db.account(aid))
        tasks.append(db.add_task(aid, url, "cart", 5))
        page = FakePage(engine, url)
        sc = SCENARIOS[i % len(SCENARIOS)] if i < len(SCENARIOS) else rng.choice(SCENARIOS)
        page.reset(sc)
        pages.append(page)
        scen.append(sc)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    results = await asyncio.gather(*(
        engine._add_to_cart(pages[i], tasks[i], url, accounts[i], "Монета", "210 430 грн")
        for i in range(ACCOUNTS)
    ), return_exceptions=True)
    elapsed = loop.time() - t0

    failed = [(i, scen[i], r) for i, r in enumerate(results) if isinstance(r, Exception)]
    assert not failed, f"Збої акаунтів: {failed}"
    bad = [(i, scen[i], p.cart_count) for i, p in enumerate(pages) if p.cart_count < 1]
    assert not bad, f"Не додано в кошик: {bad}"
    over = [(i, scen[i], p.buy_clicks) for i, p in enumerate(pages) if p.buy_clicks > 6]
    assert not over, f"Забагато кліків «Купити» (спам): {over}"
    assert elapsed < TIME_BUDGET_SEC, f"Занадто повільно для {ACCOUNTS} акаунтів: {elapsed:.2f}с"
    print(f"   ✓ {ACCOUNTS}/{ACCOUNTS} акаунтів додали монету паралельно за {elapsed:.2f}с "
          f"(макс. кліків «Купити» на акаунт: {max(p.buy_clicks for p in pages)})")

    # Захист від дублю: одне завдання на (акаунт, монета)
    first = tasks[0]
    started = []
    engine._running[first] = (object(), object())
    dup = db.add_task(accounts[0].id, url, "cart", 5)
    engine.events.task_changed = lambda *a: started.append(a)
    await engine._start_task(dup)
    assert dup not in engine._running and started, "Дубль (акаунт+монета) мав бути відхилений"
    print("   ✓ Дубль того ж акаунта на ту саму монету відхиляється")
    print("\n✓ СИМУЛЯЦІЯ РЕЛІЗУ ПРОЙДЕНА")


if __name__ == "__main__":
    asyncio.run(main())
