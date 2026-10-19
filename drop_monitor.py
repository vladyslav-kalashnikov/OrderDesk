"""Visible-browser drop monitor with manual verification and payment.

Fill in the configuration section before running.  Use this only where the
site's rules permit assisted purchasing.  CAPTCHA and payment are deliberately
left to the user.

Run:
    python drop_monitor.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright


# ================================ CONFIGURATION =============================
# Default: an upcoming NBU product observed on the public catalogue. Replace it
# with the exact product URL you intend to buy before running the monitor.
TARGET_URL = (
    "https://coins.bank.gov.ua/"
    "-gospodarska-justicija-ukrajini-n-u-suvenirnomu-pakovanni/p-1199.html"
)
# NBU shows the purchase control inside #r_buy_intovar.  Excluding .login
# prevents the monitor from clicking the manual "Авторизуватися" link.
BUY_BUTTON_SELECTOR = "#r_buy_intovar .buy:not(.login)"
# The cart count is empty initially and is populated by the site's cart update.
CHECKOUT_SIGNAL_SELECTOR = "#cart_counts:not(:empty)"
TARGET_TIME = "09:59:30"  # Europe/Kyiv local time (EEST/EET is DST-aware)

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# Set to None to work without a proxy. Supported schemes: http, https, socks5.
PROXY_CONFIG: dict[str, str] | None = {
    "scheme": "http",
    "host": "127.0.0.1",
    "port": "8080",
    "username": "",
    "password": "",
}

TIMEZONE = ZoneInfo("Europe/Kyiv")
BUTTON_TIMEOUT_MS = 900
RELOAD_TIMEOUT_MS = 10_000
CHECKOUT_TIMEOUT_MS = 300_000  # five minutes for a manual challenge, if any


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("drop-monitor")


def proxy_server() -> str | None:
    """Return the Playwright proxy server URL, without credentials."""
    if not PROXY_CONFIG:
        return None
    return f"{PROXY_CONFIG['scheme']}://{PROXY_CONFIG['host']}:{PROXY_CONFIG['port']}"


def playwright_proxy() -> dict[str, str] | None:
    """Build Playwright's proxy object, including optional authentication."""
    server = proxy_server()
    if not server:
        return None
    config = {"server": server}
    if PROXY_CONFIG and PROXY_CONFIG.get("username"):
        config["username"] = PROXY_CONFIG["username"]
        config["password"] = PROXY_CONFIG.get("password", "")
    return config


def httpx_proxy_url() -> str | None:
    """Build an encoded proxy URL compatible with httpx."""
    server = proxy_server()
    if not server or not PROXY_CONFIG or not PROXY_CONFIG.get("username"):
        return server
    username = quote(PROXY_CONFIG["username"], safe="")
    password = quote(PROXY_CONFIG.get("password", ""), safe="")
    scheme, address = server.split("://", maxsplit=1)
    return f"{scheme}://{username}:{password}@{address}"


def next_target_time() -> datetime:
    """Get the next occurrence of TARGET_TIME in Kyiv's timezone."""
    try:
        target_clock = time.fromisoformat(TARGET_TIME)
    except ValueError as error:
        raise ValueError("TARGET_TIME must have the HH:MM:SS format") from error

    now = datetime.now(TIMEZONE)
    target = datetime.combine(now.date(), target_clock, tzinfo=TIMEZONE)
    if target <= now:
        target += timedelta(days=1)
    return target


async def wait_until_target() -> None:
    target = next_target_time()
    delay = (target - datetime.now(TIMEZONE)).total_seconds()
    LOGGER.info("Waiting %.3f seconds; active monitoring starts at %s", delay, target.isoformat())
    await asyncio.sleep(max(0, delay))
    LOGGER.info("Target time reached — starting active monitoring")


async def fetch_proxy_ip() -> str:
    """Return the egress IP, or a diagnostic placeholder on failure."""
    try:
        async with httpx.AsyncClient(proxy=httpx_proxy_url(), timeout=10.0) as client:
            response = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            ip = response.json()["ip"]
            LOGGER.info("Outbound%s IP: %s", " proxy" if PROXY_CONFIG else "", ip)
            return str(ip)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        LOGGER.warning("Could not determine outbound IP: %s", error)
        return "unavailable"


async def send_telegram(proxy_ip: str) -> None:
    """Send a non-fatal completion message, if Telegram credentials are set."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOGGER.warning("Telegram is not configured; notification skipped")
        return

    message = (
        "✅ *Все зроблено! Кнопку натиснуто, перевірку пройдено.*\n"
        "💳 *Залишилось тільки оплатити!*\n\n"
        f"🌐 *Проксі IP:* `{proxy_ip}`"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            )
            response.raise_for_status()
        LOGGER.info("Telegram notification sent")
    except httpx.HTTPError as error:
        LOGGER.warning("Telegram notification failed: %s", error)


async def find_visible_buy_button(page: Page):
    button = page.locator(BUY_BUTTON_SELECTOR).first
    try:
        await button.wait_for(state="visible", timeout=BUTTON_TIMEOUT_MS)
        return button
    except PlaywrightTimeoutError:
        return None


async def monitor_and_click(page: Page) -> None:
    """Reload until a visible button is available, then click it once."""
    attempts = 0
    while not page.is_closed():
        button = await find_visible_buy_button(page)
        if button:
            LOGGER.info("Buy/Mint button found; clicking")
            await button.click(timeout=BUTTON_TIMEOUT_MS)
            LOGGER.info("Button clicked")
            return

        attempts += 1
        try:
            await page.reload(wait_until="domcontentloaded", timeout=RELOAD_TIMEOUT_MS)
            LOGGER.info("Page reloaded (attempt %d); button is not available yet", attempts)
        except PlaywrightTimeoutError:
            LOGGER.warning("Reload timed out (attempt %d); retrying", attempts)
        except Exception as error:  # browser may be manually closed during reload
            if page.is_closed():
                raise RuntimeError("Browser window was closed by the user") from error
            LOGGER.warning("Reload failed (attempt %d): %s", attempts, error)

    raise RuntimeError("Browser page was closed by the user")


async def wait_for_checkout(page: Page) -> None:
    LOGGER.info("Waiting for checkout signal: %s", CHECKOUT_SIGNAL_SELECTOR)
    await page.locator(CHECKOUT_SIGNAL_SELECTOR).first.wait_for(
        state="visible", timeout=CHECKOUT_TIMEOUT_MS
    )
    LOGGER.info("Checkout signal detected")


async def close_browser_safely(browser: Browser | None) -> None:
    """Avoid an exception if the user has already closed the browser window."""
    if not browser or not browser.is_connected():
        return
    try:
        await browser.close()
    except Exception as error:
        LOGGER.debug("Browser was already disconnected while closing: %s", error)


async def main() -> None:
    browser: Browser | None = None
    proxy_ip_task = asyncio.create_task(fetch_proxy_ip())

    try:
        async with async_playwright() as playwright:
            # Visible by design: solve any anti-bot challenge manually in this window.
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--start-maximized"],
                proxy=playwright_proxy(),
            )
            context = await browser.new_context(viewport=None)
            page = await context.new_page()
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=RELOAD_TIMEOUT_MS)
            LOGGER.info(
                "Target page opened. Sign in manually through 'Кабінет' before the drop; "
                "the purchase control only appears for an authenticated session."
            )

            await wait_until_target()
            proxy_ip = await proxy_ip_task
            await monitor_and_click(page)
            await wait_for_checkout(page)
            await send_telegram(proxy_ip)

            LOGGER.info("Ready for manual payment. Keep this window open until the transaction is complete.")
            while browser.is_connected() and not page.is_closed():
                await asyncio.sleep(1)
    except PlaywrightTimeoutError as error:
        LOGGER.error("Timed out: %s", error)
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user")
    except Exception as error:
        LOGGER.exception("Run failed: %s", error)
    finally:
        if not proxy_ip_task.done():
            proxy_ip_task.cancel()
            try:
                await proxy_ip_task
            except asyncio.CancelledError:
                pass
        await close_browser_safely(browser)


if __name__ == "__main__":
    asyncio.run(main())
