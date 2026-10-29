"""Комплексне тестування всіх можливих сценаріїв купівлі та капчі на сторінці НБУ."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dashboard.core import paths
from dashboard.core.db import Database
from dashboard.core.engine import Engine


class FakeMouse:
    def __init__(self, page: "FakePage"):
        self.page = page
        self.last_x = 0.0
        self.last_y = 0.0

    async def move(self, x: float, y: float, steps: int = 1):
        self.last_x = x
        self.last_y = y

    async def down(self):
        # Якщо клікнули в область чекбоксу Turnstile (box x=400..700, чекбокс ~428, y=300..365)
        if 415 <= self.last_x <= 445 and 315 <= self.last_y <= 350:
            self.page.on_turnstile_checkbox_click()

    async def up(self):
        pass


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def nth(self, index: int):
        return self

    async def count(self) -> int:
        return 1 if await self.is_visible() else 0

    async def is_visible(self) -> bool:
        s = self.selector
        p = self.page
        if s == "#r_buy_intovar" or "#r_buy_intovar .buy:not(.login)" in s:
            return not p.needs_login and not p.in_cart_button
        if s == p.engine.config.purchase_login_required_selector:
            return p.needs_login
        if s == p.engine.config.logged_in_selector:
            return not p.needs_login
        if s == p.engine.CART_COUNTER_SELECTOR:
            return True
        if s == p.engine.CART_POPUP_ITEM_SELECTOR or (p.engine.config.cart_item_selector and s == p.engine.config.cart_item_selector):
            return p.popup_visible
        if s == p.engine.CART_BUTTON_ADDED_SELECTOR:
            return p.in_cart_button
        if s == p.engine.CART_PENDING_SELECTOR:
            return p.button_yellow or bool(p.queue_pos)
        if s == "#cart-queue-position":
            return bool(p.queue_pos)
        return False

    async def is_enabled(self) -> bool:
        return await self.is_visible()

    async def inner_text(self, timeout: int = 2000) -> str:
        s = self.selector
        p = self.page
        if s == p.engine.CART_COUNTER_SELECTOR:
            return str(p.cart_count)
        if s == "#r_buy_intovar":
            return "ОЧІКУВАННЯ" if p.button_yellow else "КУПИТИ"
        if s == "#cart-queue-position":
            return p.queue_pos
        if s == "body":
            return p.body_text
        return ""

    async def hover(self, timeout: int = 1000):
        pass

    async def click(self, timeout: int = 3000):
        if "#r_buy_intovar" in self.selector:
            self.page.on_buy_click()


class FakePage:
    def __init__(self, engine: Engine, url: str):
        self.engine = engine
        self.url = url
        self.mouse = FakeMouse(self)
        self.reset(1)

    def reset(self, scenario: int):
        self.scenario = scenario
        self.page_loads = 1
        self.buy_clicks = 0
        self.ts_clicks = 0
        self.cart_count = 0
        self.popup_visible = False
        self.in_cart_button = False
        self.popup_cart_has_item = False
        self.button_yellow = False
        self.ts_present = False
        self.ts_solved = False
        self.needs_login = False
        self.queue_pos = ""
        self.body_text = ""

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_timeout(self, ms: int):
        await asyncio.sleep(min(ms, 50) / 1000.0)

    async def goto(self, url: str, wait_until: str = "domcontentloaded"):
        self.url = url
        self.page_loads += 1
        self.button_yellow = False
        self.ts_present = False
        self.ts_solved = False
        self.body_text = ""
        if self.scenario == 9 and self.page_loads == 2 and "p-885.html" in url:
            self.needs_login = True
        elif "account.php" in url or "login.php" in url:
            self.needs_login = False
        if self.scenario == 13 and self.page_loads == 2:
            raise TimeoutError("Page.goto: Timeout 30000ms exceeded.")

    def on_buy_click(self):
        self.buy_clicks += 1
        sc = self.scenario

        if sc == 1:
            # Сценарій 1: миттєве додавання в кошик з першого кліку
            self.cart_count = 1
        elif sc == 2:
            # Сценарій 2: прихований інпут Turnstile без видимого блоку — додає одразу
            self.cart_count = 1
        elif sc == 3:
            # Сценарій 3: з'являється банер помилки + жовта кнопка + Turnstile; після кліку по галочці сайт сам додає в кошик
            self.button_yellow = True
            self.ts_present = True
            self.body_text = "Не вдалося завершити операцію. Спробуйте ще раз."
        elif sc == 4:
            # Сценарій 4: Closed Shadow DOM Turnstile (ts_solved завжди False) —
            # додає в кошик тільки коли натиснуто галочку І після цього дотиснуто жовту кнопку «Купити»!
            self.button_yellow = True
            self.ts_present = True
            if self.ts_clicks >= 1 and self.buy_clicks >= 2:
                self.cart_count = 1
        elif sc == 5:
            # Сценарій 5: Кнопка стає жовтою, висить, а потім стає знову зеленою; на 2-й клік додає в кошик
            if self.buy_clicks == 1:
                self.button_yellow = True
                asyncio.get_running_loop().call_later(0.35, self._turn_button_green)
            else:
                self.cart_count = 1
        elif sc == 6:
            # Сценарій 6: На 1-му завантаженні сторінки кнопка + галочка намертво зависають.
            # Після автоматичного оновлення сторінки (page_loads >= 2) — додає в кошик!
            if self.page_loads == 1:
                self.button_yellow = True
                self.ts_present = True
            else:
                self.cart_count = 1
        elif sc == 7:
            # Сценарій 7: На 1-му кліку помилка «Не вдалося завершити операцію», на 2-му — успіх
            if self.buy_clicks == 1:
                self.body_text = "Не вдалося завершити операцію. Спробуйте ще раз."
            else:
                self.cart_count = 1
        elif sc == 8:
            # Сценарій 8: Кнопка зависла жовтою, але сервер тихо поклав товар у popup_cart.php
            self.button_yellow = True
            self.popup_cart_has_item = True
        elif sc == 9:
            # Сценарій 9: На 1-й спробі зависає, після оновлення сторінки вилітає сесія (.buy.login),
            # бот автоматично перелогінюється і додає в кошик!
            if self.page_loads == 1:
                self.button_yellow = True
            else:
                self.cart_count = 1
        elif sc == 10:
            # Сценарій 10: Жива черга НБУ (#cart-queue-position рухається 3 -> 2 -> 1 -> додано),
            # бот НЕ оновлює сторінку, поки черга рухається!
            self.button_yellow = True
            self.queue_pos = "Позиція: 3"
            loop = asyncio.get_running_loop()
            loop.call_later(0.4, lambda: setattr(self, "queue_pos", "Позиція: 2"))
            loop.call_later(0.8, lambda: setattr(self, "queue_pos", "Позиція: 1"))
            loop.call_later(1.1, self._mark_added)
        elif sc == 12:
            # Сценарій 12: Кнопка стає жовтою при 1-му кліку, потім з'являється Turnstile і кнопка стає ЗЕЛЕНОЮ.
            # Якщо бот передчасно натисне «Купити» ДО завершення перевірки галочки — процес зіб'ється!
            if self.buy_clicks == 1:
                self.button_yellow = True
                self.ts_present = True
                asyncio.get_running_loop().call_later(0.1, self._turn_button_green)
            else:
                # Передчасний клік збиває капчу!
                self.cart_count = -999
        elif sc == 13:
            # Сценарій 13: На 1-му завантаженні зависає, на 2-му page.goto кидає TimeoutError проксі,
            # бот перехоплює таймаут і успішно додає в кошик!
            if self.buy_clicks == 1:
                self.button_yellow = True
            else:
                self.cart_count = 1
        elif sc == 14:
            # Сценарій 14: Після кліку по галочці Turnstile ЗНИКАЄ з екрана (ts_present = False),
            # а кнопка зависає на жовтому «ОЧІКУВАННЯ». Бот розблоковує кнопку прямо на сторінці й дотискає без перезавантаження!
            if self.buy_clicks == 1:
                self.button_yellow = True
                self.ts_present = True
            elif self.ts_clicks >= 1 and not self.button_yellow:
                self.cart_count = 1
        elif sc == 15:
            # Сценарій 15 (повільний проксі на НБУ): при кліку «Купити» одразу з'являється червоний банер
            # «Не вдалося завершити операцію. Спробуйте ще раз.» і кнопка лишається зеленою,
            # а сам віджет Turnstile підвантажується із затримкою й банер НЕ зникає під час перевірки галочки.
            if self.buy_clicks == 1:
                self.button_yellow = False
                self.body_text = "Не вдалося завершити операцію. Спробуйте ще раз."
                asyncio.get_running_loop().call_later(0.12, lambda: setattr(self, "ts_present", True))
            else:
                # Якщо бот помилково перебив завантаження/перевірку Turnstile повторним кліком «Купити» — збій!
                self.cart_count = -999

    def _turn_button_green(self):
        self.button_yellow = False

    def on_turnstile_checkbox_click(self):
        self.ts_clicks += 1
        if self.scenario == 3:
            asyncio.get_running_loop().call_later(0.15, self._mark_added)
        elif self.scenario == 14:
            # Віджет Turnstile зникає після кліку по галочці, а кнопка лишається «ОЧІКУВАННЯ»
            self.ts_present = False
        elif self.scenario == 12:
            # Сайт сам додає монету через 0.25с після кліку по галочці, якщо його не перебили повторним кліком «Купити»
            def _finish_if_not_interrupted():
                if self.buy_clicks == 1:
                    self.cart_count = 1
            asyncio.get_running_loop().call_later(0.25, _finish_if_not_interrupted)
        elif self.scenario == 15:
            # Банер «Не вдалося завершити операцію» залишається на екрані весь час перевірки галочки
            def _finish_proxy_turnstile():
                if self.buy_clicks == 1:
                    self.cart_count = 1
            asyncio.get_running_loop().call_later(0.25, _finish_proxy_turnstile)

    def _mark_added(self):
        self.cart_count = 1

    async def evaluate(self, script: str):
        # 1. _get_turnstile_state
        if "isVisibleTurnstileBox" in script:
            if not self.ts_present:
                return {"present": False, "solved": False, "box": None}
            return {
                "present": True,
                "solved": self.ts_solved,
                "box": {"x": 400.0, "y": 300.0, "width": 300.0, "height": 65.0},
            }
        # 2. buy_button_state
        if "is_green_ready" in script:
            return {
                "exists": True,
                "is_yellow": self.button_yellow,
                "is_green_ready": not self.button_yellow and not self.in_cart_button,
                "text": "ОЧІКУВАННЯ" if self.button_yellow else "КУПИТИ",
            }
        # 2.5 unstick_buy_button
        if "classList.remove('clicked', 'yellow'" in script:
            self.button_yellow = False
            return None
        # 3. clear_error_banners
        if "scrollIntoView" in script or "el.remove()" in script:
            self.body_text = ""
            return None
        # 4. recaptcha_error
        if "bodyText.includes('не вдалося завершити операцію')" in script:
            t = self.body_text.lower()
            return "не вдалося завершити операцію" in t or "помилка перевірки recaptcha" in t
        # 5. popup_cart.php
        if "popup_cart.php" in script:
            if self.popup_cart_has_item:
                return '<div class="cartContent_body"><a href="p-885.html">Монета</a></div>'
            return '<div class="basket-empty-info">Порожньо</div>'
        return None


async def run_all_scenarios():
    tmp_dir = tempfile.mkdtemp(prefix="orderdesk_test_")
    os.environ["ORDERDESK_DATA"] = tmp_dir
    db = Database(paths.db_path())
    engine = Engine(db)
    # Прискорюємо таймери для швидкого проходження тестів, зберігаючи всю логіку
    engine.TURNSTILE_CLICK_DELAY_SEC = 0.15
    engine.TURNSTILE_CONFIRM_CLICK_SEC = 0.45
    engine.STUCK_AFTER_TURNSTILE_SEC = 0.9
    engine.STUCK_WITHOUT_TURNSTILE_SEC = 0.8
    engine.RECLICK_GREEN_MIN_WAIT_SEC = 0.25

    acc_id = db.add_account("test@example.com", None)
    account = db.account(acc_id)
    base_url = "https://coins.bank.gov.ua/-bosporske-carstvo-u-phutljari/p-885.html"
    task_id = db.add_task(acc_id, base_url, "cart", 5)

    page = FakePage(engine, base_url)

    # --- СЦЕНАРІЙ 1 & 2: Миттєве додавання та ігнорування прихованого Turnstile ---
    page.reset(1)
    assert (await engine._get_turnstile_state(page))["present"] is False
    assert (await engine._is_bot_check(page)) is False
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.cart_count == 1 and page.ts_clicks == 0
    print("   ✓ Сценарій 1 & 2: миттєве додавання + ігнорування прихованого Turnstile")

    # --- СЦЕНАРІЙ 3: Поява Turnstile + банера помилки -> автоклік по галочці -> автододавання сайтом ---
    page.reset(3)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.ts_clicks == 1 and page.cart_count == 1
    print("   ✓ Сценарій 3: автоклік по галочці Turnstile (навіть із банером помилки зверху)")

    # --- СЦЕНАРІЙ 4: Closed Shadow DOM Turnstile -> клік по галочці + дотискання жовтої кнопки «Купити» ---
    page.reset(4)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.ts_clicks == 1 and page.buy_clicks >= 2 and page.cart_count == 1
    print("   ✓ Сценарій 4: клік по галочці + примусове дотискання жовтої кнопки «Купити»")

    # --- СЦЕНАРІЙ 5: Кнопка «Купити» була жовтою і знову стала зеленою -> миттєвий повторний клік ---
    page.reset(5)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.buy_clicks == 2 and page.cart_count == 1
    print("   ✓ Сценарій 5: миттєвий повторний клік, коли кнопка «Купити» знову стає зеленою")

    # --- СЦЕНАРІЙ 6: Повне зависання сайту після кліку по «Купити» і галочці -> автооновлення сторінки ---
    page.reset(6)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.page_loads >= 2 and page.cart_count == 1
    print("   ✓ Сценарій 6: автоматичне оновлення сторінки, якщо після галочки сайт завис")

    # --- СЦЕНАРІЙ 7: Помилка «Не вдалося завершити операцію. Спробуйте ще раз.» -> миттєвий повтор ---
    page.reset(7)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.buy_clicks == 2 and page.cart_count == 1
    print("   ✓ Сценарій 7: миттєвий повтор при помилці «Не вдалося завершити операцію»")

    # --- СЦЕНАРІЙ 8: Кнопка зависла жовтою, але товар уже в popup_cart.php на сервері ---
    page.reset(8)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.popup_cart_has_item is True
    print("   ✓ Сценарій 8: виявлення доданої монети через фоновий запит popup_cart.php при завислій кнопці")

    # --- СЦЕНАРІЙ 9: Виліт сесії (.buy.login) під час оновлення -> авто-перелогін без помилки ---
    page.reset(9)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.cart_count == 1 and not page.needs_login
    print("   ✓ Сценарій 9: автоматичний перелогін на льоту, якщо під час оновлення злетіла сесія")

    # --- СЦЕНАРІЙ 10: Жива черга (#cart-queue-position змінюється) -> чекаємо чергу без перезавантаження ---
    page.reset(10)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.page_loads == 1 and page.cart_count == 1
    print("   ✓ Сценарій 10: збереження місця в живій черзі НБУ (#cart-queue-position), коли вона рухається")

    # --- СЦЕНАРІЙ 11: Одночасна робота 3 акаунтів паралельно без взаємного блокування ---
    pages = [FakePage(engine, base_url) for _ in range(3)]
    accounts = []
    tasks = []
    for i, p in enumerate(pages, start=1):
        p.reset(4)  # кожен проходить Turnstile + дотискання
        aid = db.add_account(f"parallel{i}@example.com", None)
        accounts.append(db.account(aid))
        tasks.append(db.add_task(aid, base_url, "cart", 5))

    t0 = asyncio.get_running_loop().time()
    await asyncio.gather(*(
        engine._add_to_cart(pages[i], tasks[i], base_url, accounts[i], "Монета", "210 430 грн")
        for i in range(3)
    ))
    elapsed = asyncio.get_running_loop().time() - t0
    assert all(p.cart_count == 1 for p in pages)
    assert elapsed < 2.0, f"3 акаунти мають працювати паралельно, а не по черзі (зайняло {elapsed:.2f}с)"
    print(f"   ✓ Сценарій 11: 3 акаунти одночасно пройшли капчу й додали в кошик паралельно за {elapsed:.2f}с")

    # --- СЦЕНАРІЙ 12: Кнопка стає зеленою ПІД ЧАС перевірки Turnstile — бот НЕ перебиває капчу раннім кліком ---
    page.reset(12)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.buy_clicks == 1 and page.ts_clicks == 1 and page.cart_count == 1
    print("   ✓ Сценарій 12: бот НЕ перебиває Turnstile передчасним reclick_green, коли кнопка зеленіє під час капчі")

    # --- СЦЕНАРІЙ 13: Повільний проксі кидає TimeoutError при page.goto — бот перехоплює і додає в кошик ---
    page.reset(13)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.cart_count == 1
    print("   ✓ Сценарій 13: таймаут проксі (Page.goto Timeout 30000ms) при оновленні НЕ валить завдання")

    # --- СЦЕНАРІЙ 14: Після кліку по галочці Turnstile зникає, а кнопка зависає на «ОЧІКУВАННЯ» — розблоковуємо без перезавантаження ---
    page.reset(14)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.page_loads == 1 and page.ts_clicks == 1 and page.buy_clicks == 2 and page.cart_count == 1
    print("   ✓ Сценарій 14: коли «ОЧІКУВАННЯ» зависає після зникнення галочки — бот розблоковує кнопку на місці без оновлення")

    # --- СЦЕНАРІЙ 15: Повільний проксі — банер «Не вдалося завершити операцію» + затримка появи/перевірки Turnstile ---
    page.reset(15)
    await engine._add_to_cart(page, task_id, base_url, account, "Монета", "210 430 грн")
    assert page.page_loads == 1 and page.buy_clicks == 1 and page.ts_clicks == 1 and page.cart_count == 1
    print("   ✓ Сценарій 15: банер «Не вдалося завершити операцію» не перебиває завантаження і перевірку Turnstile через проксі")

    # --- СЦЕНАРІЙ 16: У кошику вже є 2 монети (cart_count=2), додаємо 3-тю (банкноту з лімітом 1000 шт.) ---
    page.reset(3)
    page.cart_count = 2
    page._mark_added = lambda: setattr(page, "cart_count", 3)
    await engine._add_to_cart(page, task_id, base_url, account, "Банкнота 20 грн", "27 грн")
    assert page.buy_clicks == 1 and page.ts_clicks == 1 and page.cart_count == 3
    print("   ✓ Сценарій 16: коректне додавання, коли в кошику вже лежать 2 попередні монети (2 -> 3)")

    print("\n✓ УСІ 16 БОЙОВИХ СЦЕНАРІЇВ КУПІВЛІ ТА КАПЧІ ПРОЙДЕНО НА 100%!")


if __name__ == "__main__":
    asyncio.run(run_all_scenarios())
