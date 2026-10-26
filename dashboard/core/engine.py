"""Рушій: окремий потік зі своїм asyncio-циклом і одним Chromium на всі завдання.

UI викликає лише публічні методи (вони потокобезпечні й не блокують),
а про зміни дізнається через EngineEvents (у Qt їх перетворює ui/bridge.py на сигнали).
"""
import asyncio
import concurrent.futures
import datetime
import json
import random
import re
import threading
import time
from pathlib import Path

from . import paths
from .db import Database
from .entities import Account, AccountStatus, Proxy, ProxyStatus, TaskMode, TaskStatus
from .proxy import ProxyManager
from .secrets import get_secret
from .site_config import SiteConfig
from .adspower import AdsPowerClient, AdsPowerError
from .fingerprint import CHROME_STEALTH_ARGS, load_or_create_fingerprint


class LoginError(Exception):
    pass


class EngineEvents:
    """Колбеки рушія. Викликаються з потоку рушія — не чіпати з них віджети напряму."""

    def task_changed(self, task_id: int, status: str, log: str) -> None: ...
    def log_added(self, entry) -> None: ...
    def data_changed(self, kind: str) -> None: ...   # accounts / proxies / tasks / cart / stats / traffic
    def engine_state(self, ok: bool, message: str) -> None: ...


def _log_exception(source: str, exc: BaseException) -> None:
    """Записує повний стек викликів у data/error.log для швидкої діагностики."""
    try:
        import traceback
        err_file = paths.data_dir() / "error.log"
        err_file.parent.mkdir(parents=True, exist_ok=True)
        with open(err_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Source: {source}\n")
            f.write(f"Exception: {type(exc).__name__}: {exc}\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


def _short(exc: BaseException) -> str:
    # Помилки Playwright багаторядкові (з call log) — у таблицю йде лише перший рядок
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:200]


def _browser_was_closed(exc: BaseException) -> bool:
    """True when the user closed a visible Playwright window during a wait."""
    text = str(exc).lower()
    return any(marker in text for marker in (
        "target page, context or browser has been closed",
        "browser has been closed",
        "connection closed while reading from the driver",
        "browser is disconnected",
    ))


class Engine:
    STOP_GRACE_SEC = 5
    CART_COUNTER_SELECTOR = "#cart_counts"      # лічильник кошика в шапці сайту НБУ
    CART_POPUP_ITEM_SELECTOR = "#modal_cart_popup .cartContent_body"          # модальний кошик після «Купити»
    CART_BUTTON_ADDED_SELECTOR = "#r_buy_intovar a[href*='shopping_cart.php']"  # кнопка стала «В кошику»
    QUEUE_MODAL_TEXT_SELECTOR = "#modal_less_qty .text_container"             # повідомлення черги/ліміту
    # Сайт обробляє додавання (спінер на кнопці, жовта кнопка «Очікування», позиція в черзі)
    CART_PENDING_SELECTOR = "#prodBtnLoadingSpinner, #cart-queue-position, #r_buy_intovar .clicked, #r_buy_intovar .yellow"
    ADD_TO_CART_MAX_WAIT_SEC = 900              # скільки максимум чекаємо чергу/перевірку (15 хв)
    CHALLENGE_CLEAR_POLLS = 3                   # стільки секунд поспіль без перевірки = справді пройдено

    def __init__(self, db: Database, events: EngineEvents | None = None):
        self.db = db
        self.events = events or EngineEvents()
        self.config = SiteConfig.load()
        self.ok = True
        self.state_message = "OK"

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._running: dict[int, tuple[asyncio.Task, asyncio.Event]] = {}
        self._account_locks: dict[int, asyncio.Lock] = {}
        self._checkout_locks: dict[int, asyncio.Lock] = {}   # кошик спільний на акаунт → чекаути по черзі
        self._traffic_pending = 0
        self._pw = None
        self._browser = None
        self._browser_lock: asyncio.Lock | None = None
        self._sem: asyncio.Semaphore | None = None
        self._proxies: ProxyManager | None = None
        self._flusher: asyncio.Task | None = None
        self._orders_loop: asyncio.Task | None = None
        self._orders_lock: asyncio.Lock | None = None
        self._adspower = AdsPowerClient(getattr(self.config, "adspower_api_url", ""),
                                        getattr(self.config, "adspower_api_key", ""))
        self._adspower_browsers: dict[str, tuple[object, int]] = {}
        self._adspower_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------ потік

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="engine", daemon=True)
        self._thread.start()
        self._ready.wait(10)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._browser_lock = asyncio.Lock()
        self._orders_lock = asyncio.Lock()
        self._adspower_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max(1, self.config.max_concurrency))
        self._proxies = ProxyManager(self.log)
        self._flusher = loop.create_task(self._flush_traffic_loop())
        self._orders_loop = loop.create_task(self._orders_sync_loop())
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------ публічне API

    def start_tasks(self, task_ids) -> None:
        for task_id in task_ids:
            self._submit(self._start_task(task_id))

    def stop_tasks(self, task_ids) -> None:
        for task_id in task_ids:
            self._submit(self._stop_task(task_id))

    def running_task_ids(self) -> set[int]:
        return set(self._running)

    def login_account(self, account_id: int, interactive: bool = False) -> concurrent.futures.Future:
        return self._submit(self._login_account(account_id, interactive))

    def check_proxy(self, proxy_id: int) -> concurrent.futures.Future:
        return self._submit(self._check_proxy(proxy_id))

    def sync_orders(self, account_ids: list[int] | None = None) -> concurrent.futures.Future:
        """Зібрати замовлення з кабінету: вказаних акаунтів або всіх."""
        return self._submit(self._sync_orders(account_ids))

    def reload_config(self) -> None:
        self.config = SiteConfig.load()
        self._submit(self._apply_config())

    def state_path(self, account_id: int) -> Path:
        return paths.sessions_dir() / f"account_{account_id}.json"

    def log(self, level: str, source: str | None, message: str) -> None:
        entry = self.db.add_log(level, source, message)
        self.events.log_added(entry)

    def shutdown(self, timeout: float = 20) -> None:
        if not self._loop or not self._loop.is_running():
            return
        try:
            self._submit(self._shutdown()).result(timeout)
        except Exception as exc:
            print("Помилка зупинки рушія:", exc)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(5)

    # -------------------------------------------------------------- браузер

    async def _get_browser(self):
        async with self._browser_lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            try:
                if self._pw is None:
                    from playwright.async_api import async_playwright
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    channel="chrome",
                    headless=self.config.headless,
                    args=list(CHROME_STEALTH_ARGS),
                )
            except Exception as exc:
                _log_exception("engine.browser", exc)
                message = _short(exc)
                if "Executable doesn't exist" in str(exc):
                    message = "Chromium не встановлено: виконайте `python -m playwright install chromium`"
                self._set_engine_state(False, message)
                raise
            self._set_engine_state(True, "OK")
            return self._browser

    def _set_engine_state(self, ok: bool, message: str) -> None:
        if (ok, message) != (self.ok, self.state_message):
            self.ok, self.state_message = ok, message
            self.events.engine_state(ok, message)
            if not ok:
                self.log("ERROR", "engine", message)

    async def _get_adspower_browser(self, account: Account):
        profile_key = account.adspower_id.strip()
        async with self._adspower_lock:
            if profile_key in self._adspower_browsers:
                browser, ref_count = self._adspower_browsers[profile_key]
                if browser.is_connected():
                    self._adspower_browsers[profile_key] = (browser, ref_count + 1)
                    return browser
                else:
                    del self._adspower_browsers[profile_key]

            if self._pw is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()

            self.log("INFO", f"account#{account.id}", f"Запуск профілю AdsPower #{profile_key}…")
            try:
                ws_endpoint = await self._adspower.start_browser(profile_key)
            except Exception as exc:
                _log_exception(f"adspower#{profile_key}", exc)
                raise AdsPowerError(f"Помилка запуску AdsPower: {_short(exc)}") from exc

            try:
                browser = await self._pw.chromium.connect_over_cdp(ws_endpoint)
            except Exception as exc:
                _log_exception(f"adspower.cdp#{profile_key}", exc)
                raise AdsPowerError(f"Не вдалося підключитися через CDP до AdsPower: {_short(exc)}") from exc

            self._adspower_browsers[profile_key] = (browser, 1)
            return browser

    async def _release_adspower_browser(self, account: Account | None, close_browser: bool = False):
        if not account or not getattr(account, "adspower_id", None):
            return
        profile_key = account.adspower_id.strip()
        async with self._adspower_lock:
            if profile_key not in self._adspower_browsers:
                return
            browser, ref_count = self._adspower_browsers[profile_key]
            ref_count -= 1
            if ref_count <= 0 or close_browser:
                del self._adspower_browsers[profile_key]
                try:
                    await browser.close()
                except Exception:
                    pass
                if close_browser:
                    try:
                        await self._adspower.stop_browser(profile_key)
                    except Exception:
                        pass
            else:
                self._adspower_browsers[profile_key] = (browser, ref_count)

    async def _new_context(self, account: Account | None, proxy: Proxy | None, browser=None):
        if account and getattr(account, "adspower_id", None):
            browser = await self._get_adspower_browser(account)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            timeout_ms = self.config.page_timeout_sec * 1000
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            context.on("requestfinished", self._on_request_finished)
            return context

        browser = browser or await self._get_browser()
        kwargs = {}
        if account:
            load_or_create_fingerprint(account.id, seed_extra=account.email)
        if proxy:
            kwargs["proxy"] = await self._proxies.playwright_settings(proxy)
        if account and self.state_path(account.id).exists():
            kwargs["storage_state"] = str(self.state_path(account.id))
        context = await browser.new_context(**kwargs)
        timeout_ms = self.config.page_timeout_sec * 1000
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        context.on("requestfinished", self._on_request_finished)
        return context

    def _on_request_finished(self, request) -> None:
        asyncio.ensure_future(self._count_traffic(request))

    async def _count_traffic(self, request) -> None:
        try:
            sizes = await request.sizes()
        except Exception:
            return
        self._traffic_pending += sum(max(0, v) for v in sizes.values())

    async def _flush_traffic_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            self._flush_traffic()

    def _flush_traffic(self) -> None:
        pending, self._traffic_pending = self._traffic_pending, 0
        if pending:
            self.db.add_traffic(pending)
            self.events.data_changed("traffic")

    # ------------------------------------------------------------- допоміжне

    @staticmethod
    async def _visible(page, selector: str) -> bool:
        from playwright.async_api import Error as PWError
        if not selector:
            return False
        locator = page.locator(selector)
        try:
            for index in range(await locator.count()):
                if await locator.nth(index).is_visible():
                    return True
            return False
        except PWError:
            return False

    @staticmethod
    async def _first_visible_enabled(page, selector: str, timeout_ms: int):
        """Повертає перший видимий активний збіг, зокрема серед CSS-селекторів через кому."""
        from playwright.async_api import Error as PWError
        if not selector:
            return None
        locator = page.locator(selector)
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            try:
                for index in range(await locator.count()):
                    candidate = locator.nth(index)
                    if await candidate.is_visible() and await candidate.is_enabled():
                        return candidate
            except PWError:
                return None
            if time.monotonic() >= deadline:
                return None
            await page.wait_for_timeout(min(200, max(1, int((deadline - time.monotonic()) * 1000))))

    @staticmethod
    async def _text(page, selector: str) -> str | None:
        from playwright.async_api import Error as PWError
        if not selector:
            return None
        locator = page.locator(selector)
        try:
            if await locator.count() == 0:
                return None
            text = (await locator.first.inner_text(timeout=2000)).strip()
        except PWError:
            return None
        return " ".join(text.split()) or None

    @staticmethod
    async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    def _task_status(self, task_id: int, status: TaskStatus, log: str, *, title: str | None = None,
                     checked: bool = False) -> None:
        self.db.update_task(task_id, status, log, title=title, checked=checked)
        self.events.task_changed(task_id, status.value, log)

    # ----------------------------------------------------------------- вхід

    BOT_CHECK_MARKERS = (
        "captcha",
        "challenge",
        "verify you are human",
        "verify that you are human",
        "checking your browser",
        "checking your connection",
        "checking if the site connection is secure",
        "just a moment",
        "attention required",
        "access denied",
        "sorry, you have been blocked",
        "enable javascript and cookies",
        "security verification",
        "performing security verification",
        "please wait while we verify",
        "unusual traffic",
        "ddos-guard",
        "security check",
        "перевірте, що ви людина",
        "підтвердіть, що ви людина",
        "перевірка безпеки",
        "перевіряємо ваш браузер",
        "перевіряємо з'єднання",
        "доступ заборонено",
        "увага потрібна",
    )

    async def _get_turnstile_state(self, page) -> dict:
        """Перевіряє стан РЕАЛЬНО ВИДИМОГО блоку Cloudflare Turnstile на сторінці (300x65 праворуч від кнопки)."""
        try:
            return await page.evaluate(r"""() => {
                const inputs = [...document.querySelectorAll('input[name="cf-turnstile-response"]')];
                if (inputs.some(inp => inp.value && inp.value.trim().length > 10)) {
                    return { present: true, solved: true, box: null };
                }

                const buyWrap = document.querySelector('#r_buy_intovar');
                const buyRect = buyWrap ? buyWrap.getBoundingClientRect() : null;

                const isVisibleTurnstileBox = (el) => {
                    if (!el || !el.getBoundingClientRect) return null;
                    if (buyWrap && (el === buyWrap || el.contains(buyWrap) || buyWrap.contains(el))) return null;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return null;
                    const r = el.getBoundingClientRect();
                    // Віджет Turnstile має фіксований розмір ~300x65
                    if (r.width >= 240 && r.width <= 340 && r.height >= 48 && r.height <= 85 && r.top >= 0 && r.left >= 0) {
                        if (buyRect && buyRect.width > 0 && r.left < buyRect.right - 10) {
                            return null;
                        }
                        return { x: r.left, y: r.top, width: r.width, height: r.height };
                    }
                    return null;
                };

                const iframes = [...document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]')];
                for (const f of iframes) {
                    const b = isVisibleTurnstileBox(f);
                    if (b) return { present: true, solved: false, box: b };
                }

                const containers = [...document.querySelectorAll('.cf-turnstile, [data-sitekey], #cf-turnstile')];
                for (const c of containers) {
                    const b = isVisibleTurnstileBox(c);
                    if (b) return { present: true, solved: false, box: b };
                }

                for (const inp of inputs) {
                    const p = inp.parentElement;
                    const b = isVisibleTurnstileBox(p);
                    if (b) return { present: true, solved: false, box: b };
                }

                if (buyWrap && buyWrap.parentElement && buyWrap.parentElement.parentElement) {
                    const candidates = [...buyWrap.parentElement.parentElement.querySelectorAll('div, iframe')];
                    for (const s of candidates) {
                        const b = isVisibleTurnstileBox(s);
                        if (b) return { present: true, solved: false, box: b };
                    }
                }

                return { present: false, solved: false, box: null };
            }""")
        except Exception:
            return {"present": False, "solved": False, "box": None}

    async def _try_click_turnstile(self, page, box: dict | None = None) -> bool:
        """Плавно наводить курсор і натискає чекбокс «Підтвердьте, що ви людина» у блоці Cloudflare Turnstile."""
        try:
            if not box:
                state = await self._get_turnstile_state(page)
                if state.get("solved") or not state.get("box"):
                    return False
                box = state["box"]
            # Чекбокс у віджеті Cloudflare Turnstile (300x65) розташований за ~28px від лівого краю по центру висоти
            click_x = box["x"] + 28 + random.uniform(-2.5, 2.5)
            click_y = box["y"] + (box["height"] / 2.0) + random.uniform(-2.5, 2.5)
            await page.mouse.move(click_x, click_y, steps=random.randint(7, 12))
            await page.wait_for_timeout(random.randint(50, 110))
            await page.mouse.down()
            await page.wait_for_timeout(random.randint(35, 75))
            await page.mouse.up()
            return True
        except Exception:
            return False

    async def _is_bot_check(self, page) -> bool:
        """Визначає сторінку перевірки, враховуючи чи вже розв'язаний віджет Turnstile."""
        try:
            url = (page.url or "").lower()
            if any(marker in url for marker in self.BOT_CHECK_MARKERS):
                return True
            ts_state = await self._get_turnstile_state(page)
            if ts_state.get("present"):
                if not ts_state.get("solved"):
                    return True
            challenge_nodes = (
                "#challenge-form, #cf-challenge-running, .cf-browser-verification, "
                "[data-cf-challenge]"
            )
            if await page.locator(challenge_nodes).count():
                return True
            try:
                title = (await page.title()).lower()
            except Exception:
                title = ""
            if any(marker in title for marker in self.BOT_CHECK_MARKERS):
                return True
            body = (await page.locator("body").inner_text(timeout=1500)).lower()
            hits = sum(marker in body for marker in self.BOT_CHECK_MARKERS)
            strong = any(marker in body for marker in (
                "verify you are human",
                "checking your browser",
                "security check",
                "перевірте, що ви людина",
                "перевірка безпеки",
            ))
            return strong or hits >= 2
        except Exception:
            return False

    async def _wait_for_manual_challenge(self, page, account: Account, timeout_sec: int = 300,
                                        headed: bool | None = None) -> None:
        """Чекає проходження перевірки та автоматично натискає чекбокс Cloudflare Turnstile, якщо він з'явився."""
        self.log(
            "WARN",
            f"account#{account.id}",
            "Виявлено перевірку безпеки — пробуємо автоматично натиснути чекбокс у вікні браузера…",
        )
        if self.config.headless if headed is None else not headed:
            raise LoginError(
                "Виявлено перевірку сайту. Для проходження потрібно запустити браузер у headed mode (headless=False)."
            )

        deadline = time.monotonic() + timeout_sec
        clear_polls = 0
        last_click = 0.0
        while time.monotonic() < deadline:
            if await self._is_bot_check(page):
                clear_polls = 0
                if time.monotonic() - last_click >= 8.0:
                    ts_state = await self._get_turnstile_state(page)
                    if ts_state.get("present") and not ts_state.get("solved") and ts_state.get("box"):
                        last_click = time.monotonic()
                        await self._try_click_turnstile(page, ts_state["box"])
            else:
                clear_polls += 1
                if clear_polls >= self.CHALLENGE_CLEAR_POLLS:
                    return
            try:
                await page.wait_for_timeout(500)
            except Exception as exc:
                if _browser_was_closed(exc):
                    raise LoginError("Браузер закрито під час очікування ручної перевірки") from exc
                raise

        raise LoginError("Час очікування anti-bot перевірки вичерпано (5 хвилин)")

    async def _wait_for_rate_limit_page(self, page, account: Account, timeout_sec: int = 300) -> None:
        if self.config.headless:
            raise LoginError(
                "Сайт обмежив частоту запитів (HTTP 429). Для ручної перевірки "
                "потрібно запустити браузер у headed mode (headless=False)."
            )
        self.log(
            "WARN", f"account#{account.id}",
            "Сайт повернув HTTP 429. Вікно браузера залишено відкритим: пройдіть перевірку "
            "або дочекайтеся дозволу сайту, потім перезавантажте сторінку вручну. Автоматичних повторів не буде."
        )
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                if (await self._visible(page, self.config.logged_in_selector)
                        or await self._login_form_available(page)):
                    self.log("INFO", f"account#{account.id}",
                             "Сторінка входу знову доступна, продовжуємо авторизацію.")
                    return
                if await self._is_bot_check(page):
                    await self._wait_for_manual_challenge(page, account, timeout_sec=max(1, int(deadline - time.monotonic())))
                    if (await self._visible(page, self.config.logged_in_selector)
                            or await self._login_form_available(page)):
                        return
            except Exception as exc:
                if _browser_was_closed(exc):
                    raise LoginError("Браузер закрито під час очікування після HTTP 429") from exc
                raise
            try:
                await page.wait_for_timeout(1000)
            except Exception as exc:
                if _browser_was_closed(exc):
                    raise LoginError("Браузер закрито під час очікування після HTTP 429") from exc
                raise
        raise LoginError(
            "Сайт продовжує повертати HTTP 429; форма входу не стала доступною за 5 хвилин. "
            "Завдання зупинено без повторних запитів. Спробуйте пізніше."
        )

    async def _ensure_logged_in(self, page, account: Account) -> None:
        cfg = self.config
        lock = self._account_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            try:
                check_response = await page.goto(cfg.account_check_url, wait_until="domcontentloaded")
            except Exception as exc:
                if _browser_was_closed(exc):
                    raise LoginError(f"Браузер закрито під час перевірки входу ({page.url})") from exc
                raise LoginError(f"Не вдалося відкрити сторінку перевірки входу {cfg.account_check_url}: {_short(exc)}") from exc
            if check_response and check_response.status == 429 and not await self._is_bot_check(page):
                await self._wait_for_rate_limit_page(page, account)
            if await self._is_bot_check(page):
                await self._wait_for_manual_challenge(page, account)
                if await self._visible(page, cfg.logged_in_selector):
                    await self._save_session(page.context, account)
                    return
            if await self._visible(page, cfg.logged_in_selector):
                self._mark_active(account)
                return

            if await self._visible(page, cfg.login_required_selector):
                self.log("INFO", f"account#{account.id}",
                         f"Виявлено значок авторизації — виконуємо вхід для {account.email}")

            password = get_secret("account", account.email)
            if not password:
                raise LoginError("Пароль акаунта не знайдено в Keychain — задайте його в «Акаунтах»")

            self.log("INFO", f"account#{account.id}", f"Вхід у призначений акаунт: {account.email}")
            try:
                login_response = await page.goto(cfg.login_url, wait_until="domcontentloaded")
            except Exception as exc:
                raise LoginError(f"Не вдалося відкрити сторінку входу {cfg.login_url}: {_short(exc)}") from exc

            if login_response and login_response.status == 429 and not await self._is_bot_check(page):
                await self._wait_for_rate_limit_page(page, account)
                if await self._visible(page, cfg.logged_in_selector):
                    await self._save_session(page.context, account)
                    return
            elif login_response and login_response.status in (401, 403) and not await self._is_bot_check(page):
                raise LoginError(
                    f"Сайт повернув HTTP {login_response.status} на {page.url}; форма входу недоступна "
                    "(можлива перевірка або заборона доступу). Перевірте сторінку у звичайному браузері."
                )

            deadline = time.monotonic() + min(60, max(15, cfg.page_timeout_sec))
            while True:
                if await self._is_bot_check(page):
                    await self._wait_for_manual_challenge(page, account)
                    if await self._visible(page, cfg.logged_in_selector):
                        await self._save_session(page.context, account)
                        return
                    if not await self._login_form_available(page):
                        await page.goto(cfg.login_url, wait_until="domcontentloaded")
                if await self._visible(page, cfg.logged_in_selector):
                    self._mark_active(account)
                    await self._save_session(page.context, account)
                    return
                if await self._login_form_available(page):
                    break
                if time.monotonic() >= deadline:
                    title = ""
                    try:
                        title = (await page.title()).strip()
                    except Exception:
                        pass
                    email_count = password_count = 0
                    try:
                        email_count = await page.locator(cfg.email_selector).count() if cfg.email_selector else 0
                        password_count = await page.locator(cfg.password_selector).count() if cfg.password_selector else 0
                    except Exception:
                        pass
                    title_info = f", title='{title[:100]}'" if title else ""
                    raise LoginError(
                        f"Форма входу не з'явилася на {page.url}{title_info}; "
                        f"email matches={email_count}, password matches={password_count}. "
                        "Перевірте селектори, HTTP-доступ і чи не показує сайт сторінку перевірки."
                    )
                await page.wait_for_timeout(250)

            async def require_field(selector: str, label: str):
                if not selector:
                    raise LoginError(f"Не задано селектор {label}; поточний URL: {page.url}")
                loc = page.locator(selector).first
                try:
                    await loc.wait_for(state="visible", timeout=3000)
                    if not await loc.is_enabled():
                        raise LoginError(f"Поле {label} недоступне на {page.url}")
                    return loc
                except LoginError:
                    raise
                except Exception as exc:
                    raise LoginError(f"Не знайдено доступне поле {label} за селектором '{selector}' на {page.url}: {_short(exc)}") from exc

            email_field = await require_field(cfg.email_selector, "email")
            password_field = await require_field(cfg.password_selector, "пароля")
            submit = await require_field(cfg.submit_selector, "кнопки входу")
            try:
                await email_field.fill(account.email, timeout=3000)
                await password_field.fill(password, timeout=3000)
                await submit.click(timeout=3000)
            except Exception as exc:
                if await self._is_bot_check(page):
                    await self._wait_for_manual_challenge(page, account)
                raise LoginError(f"Не вдалося відправити форму входу на {page.url}: {_short(exc)}") from exc

            # Очікування підтвердження входу збільшено до 90 секунд для Cloudflare
            deadline = time.monotonic() + min(90, max(10, cfg.page_timeout_sec))
            while time.monotonic() < deadline:
                if await self._visible(page, cfg.logged_in_selector):
                    break
                if await self._is_bot_check(page):
                    await self._wait_for_manual_challenge(page, account)
                    if await self._visible(page, cfg.logged_in_selector):
                        break
                await page.wait_for_timeout(250)
            else:
                reason = "перевірте пароль і logged_in_selector"
                if await self._login_form_available(page):
                    reason = "сайт залишився на формі входу; перевірте credentials"
                raise LoginError(f"Вхід не підтверджено на {page.url}; {reason}")
            await self._save_session(page.context, account)

    async def _login_form_available(self, page) -> bool:
        """True лише коли обидва поля входу присутні, видимі та активні."""
        if await self._is_bot_check(page):
            return False
        for selector in (self.config.email_selector, self.config.password_selector):
            if not selector:
                return False
            try:
                field = page.locator(selector).first
                if not await field.is_visible() or not await field.is_enabled():
                    return False
            except Exception:
                return False
        return True

    async def _handle_product_challenge(self, page, account: Account, product_url: str) -> None:
        """Ручна перевірка може з'явитися на картці товару або після кліку покупки."""
        if not await self._is_bot_check(page):
            return
        self.log("WARN", f"account#{account.id}",
                 "Виявлено перевірку після відкриття товару/дії покупки; очікується ручне проходження.")
        await self._wait_for_manual_challenge(page, account)
        if page.url != product_url:
            await page.goto(product_url, wait_until="domcontentloaded")

    def _mark_active(self, account: Account) -> None:
        if account.status != AccountStatus.ACTIVE.value:
            self.db.set_account_status(account.id, AccountStatus.ACTIVE)
            account.status = AccountStatus.ACTIVE.value
            self.events.data_changed("accounts")

    async def _save_session(self, context, account: Account) -> None:
        path = self.state_path(account.id)
        await context.storage_state(path=str(path))
        path.chmod(0o600)
        self.db.set_account_status(account.id, AccountStatus.ACTIVE, session_saved=True)
        account.status = AccountStatus.ACTIVE.value
        self.events.data_changed("accounts")
        self.log("INFO", f"account#{account.id}", f"Сесію {account.email} збережено")

    async def _login_account(self, account_id: int, interactive: bool) -> None:
        account = self.db.account(account_id)
        if not account:
            return
        proxy = self.db.proxy(account.proxy_id) if account.proxy_id else None
        source = f"account#{account.id}"
        headed = None
        context = None
        page = None
        is_adspower = bool(account and getattr(account, "adspower_id", None))
        try:
            if interactive:
                if is_adspower:
                    context = await self._new_context(account, proxy)
                    page = context.pages[0] if context.pages else await context.new_page()
                else:
                    await self._get_browser()
                    headed = await self._pw.chromium.launch(
                        channel="chrome",
                        headless=False,
                        args=list(CHROME_STEALTH_ARGS),
                    )
                    context = await self._new_context(account, proxy, browser=headed)
                    page = await context.new_page()

                await page.goto(self.config.login_url, wait_until="domcontentloaded")
                if await self._is_bot_check(page):
                    await self._wait_for_manual_challenge(page, account, headed=True)
                if await self._login_form_available(page) and self.config.email_selector:
                    try:
                        await page.locator(self.config.email_selector).first.fill(account.email, timeout=3000)

                        password = get_secret("account", account.email)
                        if password and self.config.password_selector:
                            await page.locator(self.config.password_selector).first.fill(password, timeout=3000)

                    except Exception as exc:
                        raise LoginError(f"Не вдалося підготувати форму на {page.url}: {_short(exc)}") from exc
                self.log("INFO", source, f"Відкрито вікно {'AdsPower' if is_adspower else 'браузера'} — завершіть вхід вручну (чекаємо до 5 хв)")
                try:
                    await page.locator(self.config.logged_in_selector).first.wait_for(state="visible", timeout=300_000)
                except Exception as exc:
                    raise LoginError(f"Ручний вхід не підтверджено на {page.url}: {_short(exc)}") from exc
                await self._save_session(context, account)
            else:
                context = await self._new_context(account, proxy)
                page = context.pages[0] if (is_adspower and context.pages) else await context.new_page()
                await self._ensure_logged_in(page, account)
                self.log("INFO", source, f"{account.email}: вхід підтверджено")
        except Exception as exc:
            _log_exception(source, exc)
            if interactive and _browser_was_closed(exc):
                message = "Вікно ручного входу закрито до завершення; сесію не збережено"
                self.events.data_changed("accounts")
                self.log("INFO", source, f"{account.email}: {message}")
            else:
                message = _short(exc)
                self.db.set_account_status(account.id, AccountStatus.ERROR, message)
                self.events.data_changed("accounts")
                self.log("ERROR", source, f"{account.email}: {message}")
        finally:
            if is_adspower:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                await self._release_adspower_browser(account, close_browser=True)
            else:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if headed:
                    try:
                        if headed.is_connected():
                            await headed.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------- завдання

    async def _start_task(self, task_id: int) -> None:
        if task_id in self._running:
            return
        stop = asyncio.Event()
        task = asyncio.create_task(self._run_task(task_id, stop), name=f"task#{task_id}")
        self._running[task_id] = (task, stop)
        self.events.data_changed("tasks")

    async def _stop_task(self, task_id: int) -> None:
        entry = self._running.get(task_id)
        if not entry:
            return
        task, stop = entry
        stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), self.STOP_GRACE_SEC)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:
            pass

    async def _run_task(self, task_id: int, stop: asyncio.Event) -> None:
        from playwright.async_api import Error as PWError

        cfg = self.config
        source = f"task#{task_id}"
        task = self.db.task(task_id)
        account = self.db.account(task.account_id) if task else None
        if not task or not account:
            self._running.pop(task_id, None)
            return
        proxy = self.db.proxy(account.proxy_id) if account.proxy_id else None
        auto_checkout = task.mode == TaskMode.CART.value and cfg.auto_checkout
        
        if auto_checkout and not cfg.checkout_dry_run and (block := self.db.checkout_block(task_id)):
            message = ("Замовлення по завданню вже оформлено" + (f": №{block['order_number']}" if block["order_number"] else
                       " або результат невідомий — перевірте кабінет магазину") + "; повторне не створюємо")
            self._task_status(task_id, TaskStatus.DONE, message)
            self.log("WARN", f"task#{task_id}", message)
            self._running.pop(task_id, None)
            self.events.data_changed("tasks")
            return
            
        run_id = self.db.start_run(task_id)
        result = "error"
        context = None
        page = None
        is_adspower = bool(account and getattr(account, "adspower_id", None))
        loop = asyncio.get_running_loop()
        
        try:
            self._task_status(task_id, TaskStatus.STARTING, "Запуск браузера…")
            context = await self._new_context(account, proxy)
            page = await context.new_page()

            # --- ПЕРВИННА АВТОРИЗАЦІЯ ТА ТАЙМЕР ЗАПУСКУ ---
            drop_time_str = getattr(cfg, 'drop_start_time', None)
            if drop_time_str:
                try:
                    now = datetime.datetime.now()
                    target_time = datetime.datetime.strptime(drop_time_str, "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    if target_time > now:
                        self._task_status(task_id, TaskStatus.STARTING, "Первинна перевірка авторизації…")
                        try:
                            async with self._sem:
                                await self._ensure_logged_in(page, account)
                                await page.goto(task.url, wait_until="domcontentloaded")
                                await self._handle_product_challenge(page, account, task.url)
                        except Exception as auth_err:
                            self.log("WARN", source, f"Первинна перевірка входу: {_short(auth_err)}")

                        now = datetime.datetime.now()
                        if target_time > now:
                            wait_sec = (target_time - now).total_seconds()
                            self._task_status(task_id, TaskStatus.STARTING, f"Авторизовано. Чекаємо до {drop_time_str}...")
                            self.log("INFO", source, f"Таймер: чекаємо {int(wait_sec)} сек до {drop_time_str}")
                            await self._sleep_or_stop(stop, wait_sec)
                except Exception as e:
                    self.log("WARN", source, f"Помилка формату таймера: {e}")
            # ---------------------------------------------

            attempt = errors = 0
            while not stop.is_set():
                attempt += 1
                started = loop.time()
                
                try:
                    async with self._sem:
                        response = await page.goto(task.url, wait_until="domcontentloaded")
                        if response and response.status == 429:
                            raise RuntimeError(
                                "Сайт повернув HTTP 429 на сторінці товару. "
                                "Завдання зупинено без повторних запитів; спробуйте пізніше."
                            )

                        # --- ІМІТАЦІЯ РУХУ МИШКОЮ НА ЗАВАНТАЖЕНІЙ СТОРІНЦІ ---
                        try:
                            await page.mouse.move(random.randint(100, 800), random.randint(100, 800), steps=10)
                        except Exception:
                            pass
                        # ----------------------------------------------------

                        await self._handle_product_challenge(page, account, task.url)

                        needs_account_login = (
                            await self._login_form_available(page)
                            or await self._visible(page, cfg.purchase_login_required_selector)
                        )
                        if needs_account_login:
                            self._task_status(task_id, TaskStatus.LOGIN,
                                              f"Сайт вимагає вхід для {account.email}…")
                            await self._ensure_logged_in(page, account)
                            await page.goto(task.url, wait_until="domcontentloaded")
                            await self._handle_product_challenge(page, account, task.url)

                            auth_deadline = time.monotonic() + min(10, cfg.page_timeout_sec)
                            while await self._visible(page, cfg.purchase_login_required_selector):
                                if await self._is_bot_check(page):
                                    await self._wait_for_manual_challenge(page, account)
                                    await page.goto(task.url, wait_until="domcontentloaded")
                                if time.monotonic() >= auth_deadline:
                                    raise LoginError(
                                        f"Акаунт не розблокував кнопку покупки на {page.url}; "
                                        "перевірте logged_in_selector та purchase_login_required_selector"
                                    )
                                await page.wait_for_timeout(250)
                        if cfg.title_selector:
                            try:
                                await page.locator(cfg.title_selector).first.wait_for(timeout=5000)
                            except PWError:
                                pass
                        title = await self._text(page, cfg.title_selector)
                        price = await self._text(page, cfg.price_selector)
                        available = await self._visible(page, cfg.in_stock_selector)
                except PWError as exc:
                    errors += 1
                    message = f"Збій перевірки {errors}/{cfg.max_errors_in_row}: {_short(exc)}"
                    self.log("WARN", source, message)
                    self._task_status(task_id, TaskStatus.MONITORING, message, checked=True)
                    if errors >= cfg.max_errors_in_row:
                        raise RuntimeError(f"{errors} збоїв поспіль: {_short(exc)}") from exc
                else:
                    errors = 0
                    if available:
                        payment_url = None
                        if task.mode == TaskMode.CART.value:
                            if auto_checkout:
                                self._check_product_price(price)
                            self._task_status(task_id, TaskStatus.ADDING, "Товар з'явився — додаємо в кошик…", title=title)
                            async with self._sem:
                                await self._add_to_cart(page, task.id, task.url, account, title, price)
                            outcome = "Додано в кошик"
                            headline = "🛒 Товар додано в кошик"
                            if auto_checkout:
                                self._task_status(task_id, TaskStatus.CHECKOUT, "Оформлюємо замовлення…", title=title)
                                async with self._sem:
                                    checkout = await self._checkout(page, task.id, account)
                                if checkout["dry_run"]:
                                    outcome = "Додано в кошик, пробний чекаут пройдено"
                                else:
                                    outcome = f"Замовлення №{checkout['order_number']} оформлено"
                                    headline = f"✅ Замовлення №{checkout['order_number']} оформлено"
                                    payment_url = checkout["payment_url"]
                        else:
                            outcome = "Товар у наявності"
                            headline = "🔔 Товар з'явився в наявності"
                        message = f"{outcome}: {title or task.url}" + (f" ({price})" if price else "")
                        self._task_status(task_id, TaskStatus.DONE, message, title=title, checked=True)
                        self.log("INFO", source, message)
                        await self._notify_product(headline, title or task.url, price, account, task.url, payment_url)
                        result = "success"

                        # Якщо режим тільки додавання в кошик — залишаємо вікно відкритим для ручного оформлення
                        if not auto_checkout:
                            self.log("INFO", source, "Товар додано в кошик! Вікно браузера залишено відкритим для ручного оформлення.")
                            try:
                                while not stop.is_set() and not page.is_closed():
                                    await asyncio.sleep(1)
                            except Exception:
                                pass
                        return
                    self._task_status(task_id, TaskStatus.MONITORING,
                                      f"Спроба #{attempt}: немає в наявності", title=title, checked=True)
                await self._sleep_or_stop(stop, task.interval_sec - (loop.time() - started))

            result = "stopped"
            self._task_status(task_id, TaskStatus.STOPPED, "Зупинено")
        except asyncio.CancelledError:
            result = "stopped"
            self._task_status(task_id, TaskStatus.STOPPED, "Зупинено (примусово)")
        except LoginError as exc:
            self.db.set_account_status(account.id, AccountStatus.ERROR, str(exc))
            self.events.data_changed("accounts")
            self._task_status(task_id, TaskStatus.ERROR, str(exc))
            self.log("ERROR", source, str(exc))
        except Exception as exc:
            _log_exception(source, exc)
            message = _short(exc)
            self._task_status(task_id, TaskStatus.ERROR, message)
            self.log("ERROR", source, message)
        finally:
            if is_adspower:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                await self._release_adspower_browser(account)
            else:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
            self.db.finish_run(run_id, result)
            self._running.pop(task_id, None)
            self.events.data_changed("stats")
            self.events.data_changed("tasks")

    async def _add_to_cart(self, page, task_id: int, url: str, account: Account,
                           title: str | None, price: str | None) -> None:
        """Натискає «Купити» і чекає підтвердження, що товар справді в кошику.

        Сайт НБУ сам доводить додавання до кінця (prepare_buy -> add_product), зокрема після
        ручного проходження перевірки/черги, тому повторно кнопку не тиснемо одразу.
        Підтвердженням вважається будь-що з: лічильник #cart_counts виріс, у модальному кошику
        (#modal_cart_popup) з'явився товар, або кнопка на сторінці товару стала посиланням на кошик.
        """
        cfg = self.config
        source = f"account#{account.id}"

        async def cart_count() -> int:
            text = await self._text(page, self.CART_COUNTER_SELECTOR)
            digits = "".join(ch for ch in (text or "") if ch.isdigit())
            return int(digits) if digits else 0

        async def added(before: int) -> bool:
            return (
                await cart_count() > before
                or await self._visible(page, self.CART_POPUP_ITEM_SELECTOR)
                or await self._visible(page, self.CART_BUTTON_ADDED_SELECTOR)
                or (bool(cfg.cart_item_selector) and await self._visible(page, cfg.cart_item_selector))
            )

        async def queue_message() -> str | None:
            return await self._text(page, self.QUEUE_MODAL_TEXT_SELECTOR)

        async def pending_state() -> str | None:
            """Сайт ще обробляє додавання (спінер/черга/повідомлення) — треба просто чекати."""
            if await self._visible(page, self.CART_PENDING_SELECTOR):
                return "сайт обробляє додавання (черга/очікування)"
            if message := await queue_message():
                return f"повідомлення сайту: {message}"
            return None

        async def page_notices() -> str:
            """Видимі повідомлення сайту (alert/модальні вікна) та iframe перевірки — для діагностики."""
            try:
                data = await page.evaluate(r"""() => {
                  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                  const texts = [...document.querySelectorAll('.alert, .alert-danger, .alert-warning, .modal.in, .modal.show')]
                    .filter(vis).map(e => (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 200)).filter(Boolean);
                  const frames = [...document.querySelectorAll('iframe[src]')]
                    .filter(vis).map(f => f.src.slice(0, 80));
                  return {texts: [...new Set(texts)].slice(0, 4), frames: frames.slice(0, 3)};
                }""")
            except Exception:
                return ""
            parts = []
            if data.get("texts"):
                parts.append("на сторінці: " + " | ".join(data["texts"]))
            if data.get("frames"):
                parts.append("iframe: " + ", ".join(data["frames"]))
            return "; ".join(parts)

        async def diagnostics(before: int) -> str:
            button_text = await self._text(page, "#r_buy_intovar") or "—"
            queue_pos = await self._text(page, "#cart-queue-position") or "—"
            notices = await page_notices()
            return (f"лічильник={await cart_count()} (було {before}), кнопка='{button_text[:40]}', "
                    f"позиція в черзі='{queue_pos[:60]}'" + (f"; {notices}" if notices else ""))

        async def challenge_frame() -> str | None:
            """Видимий iframe захисту сайту (Bunny Shield / активна captcha).
            Ігнорує фоновий значок Google reCAPTCHA v3 та вже розв'язаний Cloudflare Turnstile.
            """
            try:
                ts_state = await self._get_turnstile_state(page)
                if ts_state.get("present"):
                    return None if ts_state.get("solved") else "cloudflare-turnstile"
                return await page.evaluate(r"""() => {
                  const isVisibleOnScreen = (e) => {
                    const style = window.getComputedStyle(e);
                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
                    const r = e.getBoundingClientRect();
                    return r.width > 40 && r.height > 40 && r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
                  };
                  const f = [...document.querySelectorAll('iframe[src]')].find(f => {
                    if (!isVisibleOnScreen(f)) return false;
                    const src = f.src || '';
                    if (/recaptcha/i.test(src)) {
                      if (/\/anchor/i.test(src)) return false;
                      const r = f.getBoundingClientRect();
                      return r.width >= 250 && r.height >= 250;
                    }
                    if (/challenges\.cloudflare\.com|turnstile/i.test(src)) {
                      return false;
                    }
                    return /shield|challenge|bunny|hcaptcha/i.test(src);
                  });
                  return f ? f.src.slice(0, 120) : null;
                }""")
            except Exception:
                return None

        cart_html_holder: list[str] = []

        async def in_cart_page() -> bool:
            """Перевірка без переходів між сторінками: той самий AJAX-запит, яким сайт сам будує вікно кошика.
            (Окремої сторінки кошика на сайті НБУ немає: shopping_cart.php віддає 404, а перехід на нову
            адресу ще й викликає перевірку захисту.)"""
            match = re.search(r"p-(\d+)\.html", url)
            product_id = match.group(1) if match else None
            try:
                html = await page.evaluate(r"""async () => {
                  const r = await fetch('./popup_cart.php', {
                    credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
                  return await r.text();
                }""")
            except Exception:
                return False
            cart_html_holder[:] = [(html or "")[:20000]]
            if not html or "Помилка 404" in html[:4000]:
                return False
            return product_id is not None and f"p-{product_id}.html" in html

        async def save_debug() -> str:
            """Скріншот і розмітка кнопки при невдачі — щоб бачити, що саме було на екрані."""
            try:
                folder = paths.sessions_dir().parent / "debug"
                folder.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                await page.screenshot(path=str(folder / f"add_to_cart_{stamp}.png"))
                html = await page.evaluate(
                    "() => (document.querySelector('#r_buy_intovar') || document.body).outerHTML.slice(0, 4000)")
                (folder / f"add_to_cart_{stamp}.html").write_text(html, encoding="utf-8")
                if cart_html_holder:
                    (folder / f"add_to_cart_{stamp}_cart.html").write_text(cart_html_holder[0], encoding="utf-8")
                return str(folder)
            except Exception:
                return ""

        async def recaptcha_error() -> bool:
            """Визначає, чи сайт повернув помилку reCAPTCHA або банер «Не вдалося завершити операцію. Спробуйте ще раз.»."""
            try:
                cur_url = (page.url or "").lower()
                if "action=add_product" in cur_url:
                    body_txt = (await page.locator("body").inner_text(timeout=1000)).lower()
                    if "recaptcha" in body_txt or "спробуйте ще раз" in body_txt or '"success"' in body_txt:
                        return True
                if not await pending_state():
                    has_err_banner = await page.evaluate(r"""() => {
                        const bodyText = (document.body && document.body.innerText) ? document.body.innerText.toLowerCase() : '';
                        return bodyText.includes('не вдалося завершити операцію') ||
                               bodyText.includes('помилка перевірки recaptcha');
                    }""")
                    if has_err_banner:
                        return True
            except Exception:
                pass
            return False

        async def clear_error_banners() -> None:
            """Прибирає старі банери помилок («Не вдалося завершити операцію»), щоб не плутати з новою спробою."""
            try:
                await page.evaluate(r"""() => {
                    const buyWrap = document.querySelector('#r_buy_intovar');
                    document.querySelectorAll('div, p, span, td, li').forEach(el => {
                        if (buyWrap && (el === buyWrap || el.contains(buyWrap))) return;
                        const t = (el.innerText || '').trim().toLowerCase();
                        if (t.length > 0 && t.length < 300 && (
                            t.includes('не вдалося завершити операцію') ||
                            t.includes('помилка перевірки recaptcha')
                        )) {
                            const childHasSame = [...el.children].some(c => {
                                const ct = (c.innerText || '').trim().toLowerCase();
                                return ct.includes('не вдалося завершити операцію') || ct.includes('помилка перевірки recaptcha');
                            });
                            if (!childHasSame) {
                                el.remove();
                            }
                        }
                    });
                }""")
            except Exception:
                pass

        async def prepare_human_click(btn, extra_warmup: bool = False) -> None:
            """Швидка підготовка перед кліком «Купити»: миттєвий клік на 1-й спробі, прогрів на повторних."""
            try:
                max_wait_ms = 1200 if extra_warmup else 350
                await page.evaluate(f"""async () => {{
                    const hasRecaptchaScript = !!document.querySelector('script[src*="recaptcha"]');
                    if (hasRecaptchaScript) {{
                        const start = Date.now();
                        while (Date.now() - start < {max_wait_ms}) {{
                            if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') break;
                            await new Promise(r => setTimeout(r, 50));
                        }}
                    }}
                }}""")
            except Exception:
                pass
            if extra_warmup:
                try:
                    await page.mouse.move(random.randint(220, 620), random.randint(200, 480), steps=6)
                    await btn.hover(timeout=1000)
                    await page.wait_for_timeout(random.randint(150, 300))
                except Exception:
                    pass

        async def wait_until_added(before: int, timeout_sec: float) -> str:
            """Чекає підтвердження, натискає чекбокс «Підтвердьте, що ви людина», а якщо прогрузка зависла — сигналізує про оновлення."""
            started = time.monotonic()
            deadline = started + timeout_sec
            hard_deadline = started + self.ADD_TO_CART_MAX_WAIT_SEC
            last_note = 0.0
            ts_first_seen = 0.0
            last_ts_click = 0.0
            ts_solved_at = 0.0
            reclicked_after_solve = False
            last_status_change = started
            last_status_snapshot = None

            while time.monotonic() < min(deadline, hard_deadline):
                if await added(before):
                    return "added"

                ts_state = await self._get_turnstile_state(page)
                if ts_state.get("present"):
                    # Банер «Не вдалося завершити операцію» з'являється разом із Turnstile — прибираємо його,
                    # щоб він не переривав очікування одразу після кліку по чекбоксу.
                    await clear_error_banners()
                    if not ts_state.get("solved") and ts_state.get("box"):
                        ts_solved_at = 0.0
                        if ts_first_seen == 0.0:
                            ts_first_seen = time.monotonic()
                            last_status_change = time.monotonic()
                        # Натискаємо чекбокс Turnstile не частіше ніж раз на 8 сек (щоб не збивати перевірку і не заважати мишці)
                        if (time.monotonic() - ts_first_seen) >= 1.2 and (time.monotonic() - last_ts_click) >= 8.0:
                            last_ts_click = time.monotonic()
                            last_status_change = time.monotonic()
                            if await self._try_click_turnstile(page, ts_state.get("box")):
                                self.log("INFO", source, "Автоматично натиснуто чекбокс «Підтвердьте, що ви людина»…")
                    elif ts_state.get("solved"):
                        if ts_solved_at == 0.0:
                            ts_solved_at = time.monotonic()
                            last_status_change = time.monotonic()
                        # Якщо чекбокс пройдено (є токен), але сайт за 0.7 сек ще не відправив запит сам — тиснемо «Купити»!
                        elif not reclicked_after_solve and (time.monotonic() - ts_solved_at) >= 0.7 and not await pending_state():
                            reclicked_after_solve = True
                            last_status_change = time.monotonic()
                            btn = await self._first_visible_enabled(page, cfg.add_to_cart_selector, 1000)
                            if btn is not None and not await added(before):
                                self.log("INFO", source, "Перевірку Cloudflare пройдено — підтверджуємо натисканням «Купити»…")
                                await clear_error_banners()
                                try:
                                    await btn.click()
                                except Exception:
                                    pass
                else:
                    if (time.monotonic() - started) >= 0.45 and await recaptcha_error():
                        return "retry_error"

                bot = await self._is_bot_check(page) or bool(await challenge_frame())
                pending = await pending_state()
                btn_text = (await self._text(page, "#r_buy_intovar") or "").strip()
                queue_pos = (await self._text(page, "#cart-queue-position") or "").strip()
                cur_snapshot = (
                    btn_text,
                    queue_pos,
                    pending or "",
                    bool(ts_state.get("present")),
                    bool(ts_state.get("solved")),
                )
                if cur_snapshot != last_status_snapshot:
                    if last_status_snapshot is not None:
                        last_status_change = time.monotonic()
                    last_status_snapshot = cur_snapshot

                # Якщо іде прогрузка «Купити» (або очікування) і статус ніяк не змінюється 6 сек — оновлюємо сайт
                if not bot and (time.monotonic() - last_status_change) >= 6.0:
                    return "stuck_reload"
                if ts_state.get("present") and not ts_state.get("solved") and last_ts_click > 0 and (time.monotonic() - last_status_change) >= 10.0:
                    return "stuck_reload"

                reason = "перевірка на сторінці" if bot else pending
                if reason:
                    if bot and cfg.headless:
                        raise LoginError("Потрібна ручна перевірка, але браузер у headless-режимі (headless=False).")
                    deadline = max(deadline, time.monotonic() + 10)
                    if time.monotonic() - last_note >= 30:
                        last_note = time.monotonic()
                        hint = (" ПОТРІБНА ВАША ДІЯ: пройдіть перевірку захисту у вікні браузера (не закривайте його) —"
                                " додавання завершиться автоматично.") if bot else ""
                        self.log("WARN", source, f"Очікуємо: {reason}; {await diagnostics(before)}.{hint}")
                try:
                    await page.wait_for_timeout(150)
                except Exception as exc:
                    if _browser_was_closed(exc):
                        raise RuntimeError("Браузер закрито під час очікування додавання в кошик") from exc
                    raise
            return "timeout"

        async def click_purchase() -> None:
            for recaptcha_attempt in range(1, 9):
                button = await self._first_visible_enabled(
                    page, cfg.add_to_cart_selector, cfg.page_timeout_sec * 1000
                )
                if button is None:
                    raise RuntimeError(
                        f"Не з'явилася active кнопка покупки за селектором "
                        f"'{cfg.add_to_cart_selector}' на {page.url}"
                    )
                before = await cart_count()
                if await self._visible(page, self.CART_BUTTON_ADDED_SELECTOR):
                    self.log("INFO", source, "Товар уже в кошику (кнопка «В кошику») — повторно не додаємо")
                    return
                await clear_error_banners()
                await prepare_human_click(button, extra_warmup=(recaptcha_attempt > 1))
                try:
                    await button.click()
                except Exception as exc:
                    if _browser_was_closed(exc):
                        raise RuntimeError(f"Браузер закрито до натискання кнопки покупки на {page.url}") from exc
                    raise RuntimeError(
                        f"Не вдалося натиснути кнопку покупки '{cfg.add_to_cart_selector}' на {page.url}: {_short(exc)}"
                    ) from exc

                wait_status = await wait_until_added(before, cfg.page_timeout_sec)
                if wait_status == "added":
                    self.log("INFO", source, "Товар додано в кошик (підтверджено сайтом)")
                    return

                if wait_status == "stuck_reload":
                    self.log(
                        "WARN",
                        source,
                        f"Прогрузка «Купити» зависла без зміни статусу — оновлюємо сайт ({recaptcha_attempt}/8)…",
                    )
                    await page.goto(url, wait_until="domcontentloaded")
                    await self._handle_product_challenge(page, account, url)
                    if await self._needs_account_login(page):
                        await self._login_on_product_page(page, account, url)
                    if await added(before) or await in_cart_page():
                        self.log("INFO", source, "Товар уже у кошику після оновлення — повторний клік не потрібен")
                        return
                    continue

                # Якщо сайт показав «Не вдалося завершити операцію. Спробуйте ще раз.» або помилку reCAPTCHA:
                if wait_status == "retry_error" or await recaptcha_error():
                    on_json_page = "action=add_product" in (page.url or "").lower()
                    self.log(
                        "WARN",
                        source,
                        f"Сайт відповів «Спробуйте ще раз» — миттєвий повтор ({recaptcha_attempt}/8)…",
                    )
                    await clear_error_banners()
                    if on_json_page or recaptcha_attempt % 2 == 0:
                        await page.goto(url, wait_until="domcontentloaded")
                        await self._handle_product_challenge(page, account, url)
                        if await self._needs_account_login(page):
                            await self._login_on_product_page(page, account, url)
                    else:
                        await page.wait_for_timeout(200)
                    if await added(before) or await in_cart_page():
                        self.log("INFO", source, "Товар уже у кошику — повторний клік не потрібен")
                        return
                    continue
                break

            # Перед повторним кліком звіряємось із кошиком (AJAX, без переходів): товар міг уже додатись,
            # а повторний клік додав би зайву штуку
            if await in_cart_page():
                self.log("INFO", source, "Товар знайдено у кошику (відповідь сайту) — повторний клік не потрібен")
                return

            # Аварійний шлях: нічого не змінилося — один повторний клік, якщо кнопка ще активна
            retry = await self._first_visible_enabled(page, cfg.add_to_cart_selector, 2000)
            if retry is not None and not await added(before) and not await pending_state() \
                    and not await self._is_bot_check(page) and not await challenge_frame():
                self.log("INFO", source, "Підтвердження немає — повторюємо натискання «Купити» один раз")
                await prepare_human_click(retry, extra_warmup=True)
                await retry.click()
                if (await wait_until_added(before, cfg.page_timeout_sec)) == "added":
                    self.log("INFO", source, "Товар додано в кошик (підтверджено сайтом)")
                    return

            details = await diagnostics(before)
            if await in_cart_page():
                self.log("INFO", source, "Товар знайдено на сторінці кошика — додано")
                return
            if await self._login_form_available(page):
                return
            debug_dir = await save_debug()
            raise RuntimeError(
                f"Не вдалося підтвердити додавання в кошик на {page.url}; {details}"
                + (f"; знімок екрана й розмітка збережені в {debug_dir}" if debug_dir else "")
            )

        await click_purchase()

        if await self._login_form_available(page):
            self._task_status(task_id, TaskStatus.LOGIN, f"Сайт вимагає вхід для {account.email}…")
            await self._ensure_logged_in(page, account)
            await page.goto(url, wait_until="domcontentloaded")
            await self._handle_product_challenge(page, account, url)
            await click_purchase()

        self.db.add_cart_item(account.id, task_id, url, title, price)
        self.events.data_changed("cart")

    def _check_product_price(self, price: str | None) -> None:
        """Рання перевірка ліміту по ціні з картки — щоб не класти в кошик те, що однаково не оформимо."""
        from .checkout import CheckoutError, parse_price
        limit, value = self.config.max_order_price, parse_price(price)
        if limit > 0 and value is not None and value > limit:
            raise CheckoutError(f"Ціна {value} грн перевищує ліміт {limit} грн — у кошик не додаємо")

    async def _checkout(self, page, task_id: int, account: Account) -> dict:
        from .checkout import CheckoutError, perform_checkout

        source = f"task#{task_id}"
        claimed: list[int] = []

        async def reserve(total: int | None) -> None:
            checkout_id = self.db.claim_checkout(task_id, account.id, total)
            if checkout_id is None:
                raise CheckoutError("По цьому завданню вже є замовлення — повторне не створюємо")
            claimed.append(checkout_id)

        lock = self._checkout_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            try:
                result = await perform_checkout(page, self.config, lambda m: self.log("INFO", source, m), reserve, account=account)
            except CheckoutError as exc:
                if claimed:
                    self.db.finish_checkout(claimed[0], "unknown" if exc.submitted else "failed", error=str(exc))
                raise
            except BaseException as exc:
                if claimed:
                    self.db.finish_checkout(claimed[0], "unknown", error=_short(exc) or type(exc).__name__)
                raise
        if claimed:
            self.db.finish_checkout(claimed[0], "success", order_number=result["order_number"])
        return result

    # --------------------------------------------------------------- проксі

    async def _check_proxy(self, proxy_id: int) -> None:
        proxy = self.db.proxy(proxy_id)
        if not proxy:
            return
        source = f"proxy#{proxy.id}"
        context = None
        status, ip, latency = ProxyStatus.FAIL, None, None
        try:
            context = await self._new_context(None, proxy)
            page = await context.new_page()
            started = time.monotonic()
            response = await page.goto(self.config.ip_check_url, wait_until="domcontentloaded", timeout=20_000)
            latency = int((time.monotonic() - started) * 1000)
            body = (await page.inner_text("body")).strip()
            try:
                ip = json.loads(body).get("ip")
            except (ValueError, AttributeError):
                ip = body[:64] or None
            if response and response.ok and ip:
                status = ProxyStatus.OK
                self.log("INFO", source, f"{proxy.display}: працює, IP {ip}, {latency} мс")
            else:
                self.log("WARN", source, f"{proxy.display}: відповідь {response.status if response else '—'}")
        except Exception as exc:
            self.log("WARN", source, f"{proxy.display}: не працює — {_short(exc)}")
        finally:
            if context:
                await context.close()
            self.db.update_proxy_check(proxy.id, status, ip, latency)
            self.events.data_changed("proxies")

    # ------------------------------------------------------------ замовлення

    async def _sync_orders(self, account_ids: list[int] | None) -> None:
        from .orders_parser import apply_orders, fetch_user_orders

        accounts = [a for a in self.db.accounts() if account_ids is None or a.id in account_ids]
        if not accounts:
            self.log("WARN", "orders", "Немає акаунтів для збору замовлень")
        if self._orders_lock.locked():
            self.log("INFO", "orders", "Збір замовлень вже йде — чекаємо, поки завершиться")
        async with self._orders_lock:
            total_new = total_changed = 0
            for account in accounts:
                source = f"account#{account.id}"
                proxy = self.db.proxy(account.proxy_id) if account.proxy_id else None
                is_adspower = bool(account and getattr(account, "adspower_id", None))
                page = None
                context = None
                try:
                    async with self._sem:
                        context = await self._new_context(account, proxy)
                        page = context.pages[0] if (is_adspower and context.pages) else await context.new_page()
                        await self._ensure_logged_in(page, account)
                        rows = await fetch_user_orders(page, self.config, lambda m: self.log("INFO", source, m))
                    updates: list[tuple] = []
                    new, changed = apply_orders(self.db, account.email, rows,
                                                lambda m: self.log("INFO", source, m),
                                                on_change=lambda *u: updates.append(u))
                    for number, status, ttn, old_status in updates:
                        await self._notify_order(number, status, account.email, ttn, old_status)
                    total_new += new
                    total_changed += changed
                    self.log("INFO", source, f"{account.email}: нових замовлень {new}, змінених {changed}")
                except LoginError as exc:
                    self.db.set_account_status(account.id, AccountStatus.ERROR, str(exc))
                    self.events.data_changed("accounts")
                    self.log("ERROR", source, f"{account.email}: {exc}")
                except Exception as exc:
                    _log_exception(source, exc)
                    self.log("ERROR", source, f"{account.email}: збір замовлень не вдався — {_short(exc)}")
                finally:
                    if is_adspower:
                        if page:
                            try:
                                await page.close()
                            except Exception:
                                pass
                        await self._release_adspower_browser(account, close_browser=True)
                    elif context:
                        try:
                            await context.close()
                        except Exception:
                            pass
            if accounts:
                self.log("INFO", "orders", f"Замовлення оновлено: нових {total_new}, змінених {total_changed}")
        self.events.data_changed("orders")

    async def _orders_sync_loop(self) -> None:
        """Автооновлення за розкладом (orders_sync_minutes > 0). Лише акаунти зі збереженою сесією."""
        while True:
            minutes = self.config.orders_sync_minutes
            await asyncio.sleep(max(60, minutes * 60) if minutes > 0 else 60)
            if self.config.orders_sync_minutes <= 0:
                continue
            ids = [a.id for a in self.db.accounts()
                   if a.status == AccountStatus.ACTIVE.value and self.state_path(a.id).exists()]
            if ids:
                try:
                    await self._sync_orders(ids)
                except Exception as exc:
                    self.log("ERROR", "orders", f"Автооновлення замовлень: {_short(exc)}")

    # ------------------------------------------------------------- Telegram

    def _telegram_target(self) -> tuple[str, str] | None:
        """(токен, chat id), якщо сповіщення увімкнено й налаштовано; інакше None."""
        cfg = self.config
        if not (cfg.telegram_enabled and cfg.telegram_chat_id):
            return None
        token = get_secret("telegram", "token")
        if not token:
            self.log("WARN", "telegram", "Telegram увімкнено, але токен бота не задано")
            return None
        return token, cfg.telegram_chat_id

    async def _notify_product(self, headline: str, title: str, price: str | None, account: Account,
                              product_url: str, payment_url: str | None = None) -> None:
        from .notifier import send_telegram_notification
        if not (target := self._telegram_target()):
            return
        ok, detail = await send_telegram_notification(*target, title, price, account.email, payment_url,
                                                      headline=headline, product_url=product_url)
        self.log("INFO" if ok else "WARN", "telegram", f"{title}: {detail}")

    async def _notify_order(self, number: str, status: str, account_email: str, ttn: str | None,
                            old_status: str | None) -> None:
        from .notifier import send_order_update
        if not (target := self._telegram_target()):
            return
        ok, detail = await send_order_update(*target, number, status, account_email, ttn, old_status)
        self.log("INFO" if ok else "WARN", "telegram", f"№{number}: {detail}")

    def send_test_notification(self, chat_id: str, token: str | None) -> concurrent.futures.Future:
        """Тестове повідомлення з кнопки в налаштуваннях. token=None — взяти збережений у Keychain."""
        from .notifier import send_telegram_text

        async def run() -> tuple[bool, str]:
            ok, detail = await send_telegram_text(
                token or get_secret("telegram", "token") or "", chat_id,
                "<b>✅ OrderDesk</b>\nТестове повідомлення: сповіщення налаштовано правильно.")
            self.log("INFO" if ok else "WARN", "telegram", f"Тестове повідомлення: {detail}")
            return ok, detail

        return self._submit(run())

    # ------------------------------------------------------- конфіг і вихід

    async def _apply_config(self) -> None:
        if self._running:
            self.log("INFO", "engine", "Налаштування збережено; браузер і ліміт застосуються після зупинки завдань")
            return
        self._sem = asyncio.Semaphore(max(1, self.config.max_concurrency))
        self._adspower.base_url = getattr(self.config, "adspower_api_url", "http://local.adspower.net:50325")
        self._adspower.api_key = getattr(self.config, "adspower_api_key", "")
        if self._browser:
            await self._browser.close()
            self._browser = None
        self.log("INFO", "engine", "Налаштування застосовано")

    async def _shutdown(self) -> None:
        await asyncio.gather(*(self._stop_task(t) for t in list(self._running)), return_exceptions=True)
        self._flush_traffic()
        for loop_task in (self._flusher, self._orders_loop):
            if loop_task:
                loop_task.cancel()
        for profile_key, (b, _) in list(self._adspower_browsers.items()):
            try:
                await b.close()
            except Exception:
                pass
        self._adspower_browsers.clear()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        if self._proxies:
            await self._proxies.close()
