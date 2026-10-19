"""Оформлення замовлення з кошика: доставка Новою Поштою + оплата при отриманні або посилання на оплату.

Підтримує стандартні CSS-селектори та специфічні віджети (включно з coins.bank.gov.ua).
Картку програма НЕ вводить: або накладений платіж, або сайт дає посилання (LiqPay/Portmone/WayForPay),
яке людина відкриває й оплачує сама.

Селектори й дані доставки — з SiteConfig (вкладка «Налаштування»), не з коду.
"""
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from .entities import Account
from .site_config import SiteConfig


def parse_price(text: str | None) -> int | None:
    """'3 899 грн' → 3899; None, якщо числа немає."""
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").strip()
    match = re.search(r"\d+(?:[\s ]\d+)*(?:[.,]\d+)?", cleaned)
    if not match:
        return None
    num_str = re.sub(r"[\s ]", "", match.group()).replace(",", ".")
    try:
        return int(float(num_str))
    except ValueError:
        return None


class CheckoutError(Exception):
    """submitted=True — кнопку підтвердження вже натиснуто: замовлення МОЖЕ існувати, повторювати не можна."""

    def __init__(self, message: str, submitted: bool = False):
        super().__init__(message)
        self.submitted = submitted


def check_price_limit(total: int | None, limit: int, dry_run: bool, log: Callable[[str], None]) -> None:
    """Ліміт ціни. Справжнє замовлення — лише з заданим лімітом і відомою сумою, що в нього вкладається."""
    if total is not None and limit > 0 and total > limit:
        raise CheckoutError(f"Сума {total} грн перевищує ліміт {limit} грн — замовлення не оформлюємо")
    problem = None
    if limit <= 0:
        problem = "ліміт ціни не задано (max_order_price)"
    elif total is None:
        problem = "не вдалося прочитати суму — перевірте checkout_total_selector / price_selector"
    if problem:
        if not dry_run:
            raise CheckoutError(f"Замовлення заблоковано: {problem}")
        log(f"Пробний режим: {problem} — справжнє замовлення було б заблоковане")


# Захист від повторного натискання: після першого кліку по кнопці підтвердження сторінка
# ігнорує подальші кліки та повторні submit форми, а кнопка стає disabled.
_SUBMIT_GUARD_JS = """(sel) => {
  if (window.__orderdeskGuard) return;
  window.__orderdeskGuard = true;
  let clicked = false, submitted = false;
  document.addEventListener('click', (e) => {
    if (!(e.target instanceof Element) || !e.target.closest(sel)) return;
    if (clicked) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    clicked = true;
    setTimeout(() => document.querySelectorAll(sel).forEach((b) => {
      b.disabled = true; b.setAttribute('aria-disabled', 'true'); b.style.pointerEvents = 'none';
    }), 0);
  }, true);
  document.addEventListener('submit', (e) => {
    if (submitted) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    submitted = true;
  }, true);
}"""


async def _wait_visible(page: Page, selector: str, timeout_ms: int) -> bool:
    if not selector:
        return False
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except (PlaywrightTimeoutError, PlaywrightError):
        return False


async def _fill_field(page: Page, selector: str, value: str) -> None:
    """Заповнює input або обирає пункт у select (за текстом, потім за value)."""
    field = page.locator(selector).first
    try:
        await field.wait_for(state="visible", timeout=5000)
        if not await field.is_enabled():
            raise CheckoutError(f"Поле недоступне: {selector} (URL: {page.url})")
        tag = await field.evaluate("el => el.tagName.toLowerCase()")
    except CheckoutError:
        raise
    except PlaywrightError as exc:
        raise CheckoutError(f"Не знайдено поле '{selector}' на {page.url}: {str(exc).splitlines()[0]}") from exc
    if tag == "select":
        selected = False
        try:
            await field.select_option(label=value)
            selected = True
        except PlaywrightError:
            try:
                await field.select_option(value=value)
                selected = True
            except PlaywrightError:
                options = await field.locator("option").all()
                for opt in options:
                    txt = await opt.inner_text()
                    if value.lower() in txt.lower():
                        val = await opt.get_attribute("value")
                        if val is not None:
                            await field.select_option(value=val)
                            selected = True
                            break
        if not selected:
            raise CheckoutError(f"Не знайдено варіант '{value}' у select '{selector}' на {page.url}")
    else:
        try:
            await field.fill(value, timeout=5000)
        except PlaywrightError as exc:
            raise CheckoutError(f"Не вдалося заповнити '{selector}' на {page.url}: {str(exc).splitlines()[0]}") from exc


async def _handle_autocomplete_selection(page: Page, field_selector: str, value: str, suggestion_selector: str | None = None) -> None:
    """Обробка випадаючих списків та автопідказок (типу Select2 або автокомпліту НП)."""
    try:
        # Якщо селектор Select2 і поле приховане, спочатку клікаємо контейнер Select2
        if "select2" in field_selector and not await page.locator(field_selector).first.is_visible():
            container = page.locator(".select2-container, .select2-selection").first
            if await container.is_visible():
                await container.click()
                await page.wait_for_timeout(300)

        field = page.locator(field_selector).first
        if await field.count() and await field.evaluate("el => el.tagName.toLowerCase()") == "select":
            await _fill_field(page, field_selector, value)
            return
        await field.click(force=True)
        await field.fill(value)
        await page.wait_for_timeout(600)

        # Якщо вказано конкретний селектор підказки
        if suggestion_selector and await _wait_visible(page, suggestion_selector, 3000):
            await page.locator(suggestion_selector).first.click()
        # Специфічний випадаючий список Select2 / dropdown
        elif await _wait_visible(page, ".select2-results__option, .autocomplete-suggestions, .dropdown-menu, .pac-container", 3000):
            await page.locator(".select2-results__option, .autocomplete-suggestions > div, .dropdown-menu > li, .pac-item").first.click()
        # На звичайному input Enter часто відправляє всю форму checkout до
        # заповнення відділення та способу оплати. Якщо автопідказки немає,
        # залишаємо введене значення — сайт сам виконає валідацію на наступному кроці.
    except PlaywrightError as exc:
        raise CheckoutError(f"Не вдалося вибрати '{value}' для '{field_selector}' на {page.url}: {str(exc).splitlines()[0]}") from exc


async def perform_checkout(page: Page, cfg: SiteConfig, log: Callable[[str], None],
                           before_submit: Callable[[int | None], Awaitable[None]] | None = None,
                           account: Account | None = None) -> dict:
    """
    Проходить чекаут для того, що вже лежить у кошику.
    Повертає {"order_number", "payment_url", "dry_run", "total"}; при збої кидає CheckoutError.
    """
    wait_ms = cfg.page_timeout_sec * 1000
    carrier = (getattr(account, "delivery_carrier", None) or "nova").lower()
    delivery_city = getattr(account, "delivery_city", None) or cfg.delivery_city
    delivery_warehouse = getattr(account, "delivery_warehouse", None) or cfg.delivery_warehouse
    payment_method = getattr(account, "payment_method", None) or cfg.payment_method
    is_nbu_site = "coins.bank.gov.ua" in page.url or "coins.bank.gov.ua" in cfg.cart_url

    # 1. Кошик → «Оформити замовлення»
    log("Перехід до оформлення замовлення…")
    # На сайті НБУ кошик може бути відкритий модальним вікном на сторінці товару.
    # У такому разі працюємо з ним без переходу на неіснуючий /cart/.
    cart_already_open = await _wait_visible(page, cfg.cart_item_selector, 1000)
    if not cart_already_open:
        try:
            await page.goto(cfg.cart_url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise CheckoutError(f"Не вдалося відкрити кошик {cfg.cart_url}: {str(exc).splitlines()[0]}") from exc

    cart_items = page.locator(cfg.cart_item_selector) if cfg.cart_item_selector else None
    has_cart = False
    if cart_items:
        try:
            has_cart = await cart_items.count() > 0 and await cart_items.first.is_visible()
        except PlaywrightError:
            pass
    already_checkout = bool(cfg.city_selector and await _wait_visible(page, cfg.city_selector, 1000))
    if not has_cart and not already_checkout:
        raise CheckoutError(f"Кошик недоступний або порожній на {page.url}; перевірте cart_item_selector/cart_url")
    
    if cfg.checkout_button_selector and await _wait_visible(page, cfg.checkout_button_selector, 5000):
        cart_url = page.url
        await page.locator(cfg.checkout_button_selector).first.click()
        # wait_for_load_state() повертається одразу, якщо попередня сторінка вже
        # була завантажена. Тому після кліку очікуємо саме зміну URL; інакше
        # наступний крок інколи шукає поля доставки ще на сторінці кошика.
        try:
            await page.wait_for_url(lambda url: url != cart_url, timeout=min(wait_ms, 10_000))
            await page.wait_for_load_state("domcontentloaded")
        except PlaywrightTimeoutError:
            # Деякі магазини відкривають чекаут без зміни URL (SPA). Нижче
            # все одно перевіряємо наявність реального поля міста.
            pass

    # 2. Доставка (Нова Пошта / Укрпошта)
    carrier_label = "Нова Пошта" if carrier == "nova" else "Укрпошта"
    log(f"Доставка ({carrier_label}): {delivery_city}, {delivery_warehouse}")

    # Вибір перевізника на сторінці, якщо є радіокнопки або таби вибору служби доставки
    carrier_selector = (
        "input[value*='nova'], input[id*='nova'], input[value*='np'], label:has-text('Нова пошта'), label:has-text('Нова Пошта')"
        if carrier == "nova"
        else "input[value*='ukr'], input[id*='ukr'], input[value*='up'], label:has-text('Укрпошта'), label:has-text('Укр Пошта')"
    )
    if await _wait_visible(page, carrier_selector, 2000):
        try:
            await page.locator(carrier_selector).first.click()
            await page.wait_for_timeout(500)
        except Exception:
            pass

    if not await _wait_visible(page, cfg.city_selector, wait_ms):
        raise CheckoutError("Не знайдено поле міста — перевірте city_selector у налаштуваннях")
    
    if is_nbu_site or cfg.city_suggestion_selector:
        await _handle_autocomplete_selection(page, cfg.city_selector, delivery_city, cfg.city_suggestion_selector)
    else:
        await _fill_field(page, cfg.city_selector, delivery_city)

    if not await _wait_visible(page, cfg.warehouse_selector, wait_ms):
        raise CheckoutError("Не знайдено поле відділення — перевірте warehouse_selector")

    if is_nbu_site or "select2" in cfg.warehouse_selector:
        await _handle_autocomplete_selection(page, cfg.warehouse_selector, delivery_warehouse)
    else:
        await _fill_field(page, cfg.warehouse_selector, delivery_warehouse)

    # 3. Спосіб оплати
    option_selector = cfg.payment_cod_selector if payment_method == "cod" else cfg.payment_online_selector
    if option_selector and await _wait_visible(page, option_selector, 5000):
        try:
            loc = page.locator(option_selector).first
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            input_type = (await loc.get_attribute("type") or "").lower() if tag == "input" else ""
            if tag == "input" and input_type in ("radio", "checkbox"):
                identity = " ".join(filter(None, [await loc.get_attribute("id"), await loc.get_attribute("value"),
                                                   await loc.get_attribute("name")])).lower()
                expected = ("cod", "cash", "delivery") if payment_method == "cod" else ("online", "card", "liqpay", "portmone", "wayforpay")
                if not any(token in identity for token in expected):
                    raise CheckoutError(f"Селектор оплати вибрав неоднозначний варіант '{identity or 'без id/value'}' на {page.url}; уточніть його в налаштуваннях")
                await loc.check(force=True)
                if not await loc.is_checked():
                    raise CheckoutError(f"Не вдалося вибрати спосіб оплати на {page.url}")
            else:
                await loc.click(force=True)
        except CheckoutError:
            raise
        except PlaywrightError as exc:
            raise CheckoutError(f"Не вдалося вибрати спосіб оплати на {page.url}: {str(exc).splitlines()[0]}") from exc
    else:
        label = "оплати при отриманні" if payment_method == "cod" else "онлайн-оплати"
        raise CheckoutError(f"Не знайдено варіант {label} — перевірте селектор способу оплати")

    # 4. Фінальна кнопка
    if not await _wait_visible(page, cfg.place_order_selector, 5000):
        raise CheckoutError("Не знайдено кнопку підтвердження замовлення — перевірте place_order_selector")

    # 5. Ліміт ціни — по сумі, яку показує сам чекаут
    total = None
    if cfg.checkout_total_selector and await _wait_visible(page, cfg.checkout_total_selector, 5000):
        total = parse_price(await page.locator(cfg.checkout_total_selector).first.inner_text())
    
    check_price_limit(total, cfg.max_order_price, cfg.checkout_dry_run, log)

    if cfg.checkout_dry_run:
        log(f"Пробний режим: усе заповнено (сума {total if total is not None else '?'} грн), "
            "кнопку підтвердження НЕ натиснуто")
        return {"order_number": None, "payment_url": None, "dry_run": True, "total": total}

    if before_submit:
        try:
            await before_submit(total)
        except CheckoutError:
            raise
        except Exception as exc:
            raise CheckoutError(f"Не вдалося зарезервувати checkout перед submit на {page.url}: {exc}") from exc

    # 6. Натискання кнопки підтвердження (рівно 1 клік)
    try:
        await page.evaluate(_SUBMIT_GUARD_JS, cfg.place_order_selector)
        log(f"Підтвердження замовлення на {total if total is not None else '?'} грн…")
        button = page.locator(cfg.place_order_selector).first
        if not await button.is_enabled():
            raise CheckoutError(f"Кнопка підтвердження вимкнена на {page.url}")
        await button.click(timeout=5000)

        # Чекаємо обробки AJAX або редиректу
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass

        # 7. Обробка результату та пошук посилання на оплату (LiqPay/Portmone/НБУ)
        payment_url = None
        order_number = None

        if any(gateway in page.url.lower() for gateway in ("liqpay", "wayforpay", "portmone", "bank.gov.ua/pay", "checkout", "payment")):
            payment_url = page.url
            log("Отримано посилання на оплату через редирект")

        # Очікування номера замовлення або посилання на оплату (до 15 сек)
        for _ in range(15):
            if cfg.order_number_selector and await _wait_visible(page, cfg.order_number_selector, 1000):
                order_number = (await page.locator(cfg.order_number_selector).first.inner_text()).strip()
                order_number = order_number.removeprefix("№").removeprefix("#").strip()
                if order_number:
                    break

            if payment_method == "card_link" and not payment_url and cfg.pay_link_selector:
                if await _wait_visible(page, cfg.pay_link_selector, 1000):
                    href = await page.locator(cfg.pay_link_selector).first.get_attribute("href")
                    if href:
                        payment_url = urljoin(page.url, href)
                        break

            if any(gateway in page.url.lower() for gateway in ("liqpay", "wayforpay", "portmone", "bank.gov.ua/pay", "checkout", "payment")):
                payment_url = page.url
                break

            await page.wait_for_timeout(1000)

        if not order_number and not payment_url:
            content = await page.content()
            match = re.search(r"(?:замовлення|номер|order)\s*(?:№|#)?\s*(\d{4,10})", content, re.IGNORECASE)
            if match:
                order_number = match.group(1)
            else:
                raise CheckoutError("Після підтвердження не вдалося визначити номер замовлення — перевірте кабінет",
                                    submitted=True)

        if payment_method == "card_link" and not payment_url:
            if cfg.pay_link_selector and await _wait_visible(page, cfg.pay_link_selector, 3000):
                href = await page.locator(cfg.pay_link_selector).first.get_attribute("href")
                payment_url = urljoin(page.url, href) if href else None
            
            if not payment_url:
                log("Замовлення створено, але пряме посилання на оплату не знайдено — сплатіть в особистому кабінеті")

    except CheckoutError:
        raise
    except PlaywrightError as exc:
        raise CheckoutError(f"Збій після підтвердження ({exc}) — замовлення могло створитися", submitted=True) from exc

    log(f"Замовлення створено: №{order_number or 'успішно'}")
    return {
        "order_number": order_number or "успішно",
        "payment_url": payment_url,
        "dry_run": False,
        "total": total
    }
