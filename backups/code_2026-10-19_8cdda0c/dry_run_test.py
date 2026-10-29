"""Non-purchasing diagnostic for the visible-browser drop monitor.

This file never clicks the buy button and never submits payment data.  Replace
the placeholder settings below before a real proxy/Telegram test.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from playwright.async_api import Browser, TimeoutError as PlaywrightTimeoutError, async_playwright


# =============================== TEST SETTINGS ===============================
TARGET_URL = "https://coins.bank.gov.ua/arhistratig-mihajil-c-/p-1126.html"
# This selector appears only after the user is signed in; it deliberately
# excludes NBU's manual "Авторизуватися" link.
BUY_BUTTON_SELECTOR = "#r_buy_intovar .buy:not(.login)"
TELEGRAM_BOT_TOKEN = "ТВІЙ_ТОКЕН"
TELEGRAM_CHAT_ID = "ТВІЙ_CHAT_ID"
PROXY_SERVER: str | None = "http://ip:port"  # e.g. "http://127.0.0.1:8080"

SELECTOR_TIMEOUT_MS = 3_000
RELOAD_COUNT = 3
RELOAD_INTERVAL_SECONDS = 1


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("dry-run")


@dataclass
class TestResult:
    proxy_ip: str = "not checked"
    proxy_ok: bool = False
    telegram: str = "not checked"
    page: str = "not checked"
    selector: str = "not checked"
    reloads: int = 0


def is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in ("твій_", "your_", "ip:port", "example"))


def browser_proxy() -> dict[str, str] | None:
    if is_placeholder(PROXY_SERVER):
        return None
    return {"server": PROXY_SERVER}  # type: ignore[dict-item]


async def check_network(result: TestResult) -> None:
    """Check the configured outbound route without contacting the target site."""
    proxy = None if is_placeholder(PROXY_SERVER) else PROXY_SERVER
    if proxy is None:
        LOGGER.warning("Proxy check skipped: PROXY_SERVER still contains a placeholder")
        result.proxy_ip = "direct route (proxy not configured)"
        return

    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=12.0) as client:
            response = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            result.proxy_ip = str(response.json()["ip"])
            result.proxy_ok = True
            LOGGER.info("Proxy connected successfully; outbound IP: %s", result.proxy_ip)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        result.proxy_ip = f"error: {error}"
        LOGGER.error("Proxy/network check failed: %s", error)


async def check_telegram(result: TestResult) -> None:
    if is_placeholder(TELEGRAM_BOT_TOKEN) or is_placeholder(TELEGRAM_CHAT_ID):
        result.telegram = "skipped — configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        LOGGER.warning("Telegram test skipped: credentials are placeholders")
        return

    text = f"🧪 *Тест системи пройшов успішно! IP: `{result.proxy_ip}`*"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            )
            response.raise_for_status()
        result.telegram = "sent"
        LOGGER.info("Telegram test message sent")
    except httpx.HTTPError as error:
        result.telegram = f"error: {error}"
        LOGGER.error("Telegram test failed: %s", error)


async def close_browser_safely(browser: Browser | None) -> None:
    if not browser or not browser.is_connected():
        return
    try:
        await browser.close()
        LOGGER.info("Browser closed safely")
    except Exception as error:
        LOGGER.warning("Browser was already disconnected while closing: %s", error)


async def check_page_and_selector(result: TestResult) -> None:
    browser: Browser | None = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--start-maximized"],
                proxy=browser_proxy(),
            )
            context = await browser.new_context(viewport=None)
            page = await context.new_page()

            response = await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=15_000)
            status = response.status if response else "no HTTP response"
            result.page = f"opened (HTTP {status})"
            LOGGER.info("Target opened: %s", result.page)

            button = page.locator(BUY_BUTTON_SELECTOR).first
            try:
                await button.wait_for(state="visible", timeout=SELECTOR_TIMEOUT_MS)
                await button.evaluate(
                    "element => { element.style.outline = '3px solid red'; element.scrollIntoView({block: 'center'}); }"
                )
                result.selector = "found and highlighted (no click performed)"
                LOGGER.info("Selector found and highlighted: %s", BUY_BUTTON_SELECTOR)
                await asyncio.sleep(2)  # makes the highlight visible during diagnostics
            except PlaywrightTimeoutError:
                result.selector = "not found"
                LOGGER.warning("Selector not found: %s; starting reload diagnostics", BUY_BUTTON_SELECTOR)
                for number in range(1, RELOAD_COUNT + 1):
                    started = asyncio.get_running_loop().time()
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15_000)
                        elapsed = asyncio.get_running_loop().time() - started
                        result.reloads += 1
                        LOGGER.info("Reload %d/%d completed in %.2fs", number, RELOAD_COUNT, elapsed)
                    except PlaywrightTimeoutError:
                        LOGGER.error("Reload %d/%d timed out", number, RELOAD_COUNT)
                    await asyncio.sleep(RELOAD_INTERVAL_SECONDS)
    except Exception as error:
        result.page = f"error: {error}"
        LOGGER.exception("Browser/site diagnostic failed: %s", error)
    finally:
        await close_browser_safely(browser)


def print_summary(result: TestResult) -> None:
    LOGGER.info("--- DRY-RUN SUMMARY ---")
    LOGGER.info("Network/proxy: %s", "OK" if result.proxy_ok else result.proxy_ip)
    LOGGER.info("Outbound IP: %s", result.proxy_ip)
    LOGGER.info("Telegram: %s", result.telegram)
    LOGGER.info("Target page: %s", result.page)
    LOGGER.info("Buy selector: %s", result.selector)
    LOGGER.info("Successful reloads: %d/%d", result.reloads, RELOAD_COUNT)


async def main() -> None:
    result = TestResult()
    await check_network(result)
    await check_telegram(result)
    await check_page_and_selector(result)
    print_summary(result)


if __name__ == "__main__":
    asyncio.run(main())
