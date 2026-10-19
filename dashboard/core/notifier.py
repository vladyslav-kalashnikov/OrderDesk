"""Telegram-сповіщення: товар з'явився / доданий у кошик / змінився статус замовлення.

Кнопка «Оплатити» лише відкриває посилання магазину (LiqPay/WayForPay) — оплачує людина сама.
Без httpx: стандартний urllib у пулі потоків, щоб не блокувати asyncio-цикл рушія.
Токен ніколи не потрапляє в текст помилок (у URL запиту він є — тому URL у помилки не пишемо).

ORDERDESK_TELEGRAM_API — інша адреса API (для тестів з локальним фейковим сервером).
"""
import asyncio
import html
import json
import os
import urllib.error
import urllib.request

DEFAULT_API = "https://api.telegram.org"


def _esc(text: str) -> str:
    # Лише < > & — апостроф у тексті (не в атрибуті) екранувати не треба: «Levi's», «з'явився»
    return html.escape(text, quote=False)


def _api_base() -> str:
    return os.environ.get("ORDERDESK_TELEGRAM_API", DEFAULT_API).rstrip("/")


def _is_web_url(url: str | None) -> bool:
    # Telegram відхиляє ВСЕ повідомлення, якщо в кнопці не http(s)-посилання
    return bool(url) and url.startswith(("http://", "https://"))


async def send_telegram_text(bot_token: str, chat_id: str, text: str,
                             buttons: list[tuple[str, str]] | None = None) -> tuple[bool, str]:
    """Надсилає готовий HTML-текст. Повертає (успіх, пояснення) — пояснення без токена."""
    if not bot_token or not chat_id:
        return False, "не задано токен бота або chat id"

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    rows = [[{"text": label, "url": url}] for label, url in (buttons or []) if _is_web_url(url)]
    if rows:
        payload["reply_markup"] = {"inline_keyboard": rows}

    def post() -> tuple[bool, str]:
        request = urllib.request.Request(
            f"{_api_base()}/bot{bot_token}/sendMessage",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True, "надіслано"
        except urllib.error.HTTPError as exc:
            # Telegram пояснює причину в JSON: «chat not found», «Unauthorized», «can't parse entities»…
            try:
                description = json.loads(exc.read().decode()).get("description", "")
            except (ValueError, OSError):
                description = ""
            return False, f"Telegram відповів {exc.code}: {description or exc.reason}"
        except urllib.error.URLError as exc:
            return False, f"немає зв'язку з Telegram: {exc.reason}"
        except TimeoutError:
            return False, "Telegram не відповів за 10 с"

    return await asyncio.to_thread(post)


async def send_telegram_notification(bot_token: str, chat_id: str, title: str, price: str | None, account: str,
                                     payment_url: str | None = None, *, headline: str = "✅ Товар успішно додано / викуплено!",
                                     product_url: str | None = None) -> tuple[bool, str]:
    """Сповіщення про товар. Усі дані з сайту екрануються — назва з «&» чи «<» не зламає розмітку."""
    lines = [
        f"<b>{_esc(headline)}</b>",
        "",
        f"<b>Товар:</b> {_esc(title)}",
    ]
    if price:
        lines.append(f"<b>Ціна:</b> {_esc(price)}")
    lines.append(f"<b>Акаунт:</b> {_esc(account)}")

    buttons = []
    if _is_web_url(payment_url):
        lines += ["", "💳 <b>Потрібна оплата!</b> Натисніть кнопку нижче:"]
        buttons.append(("💳 Оплатити замовлення", payment_url))
    if _is_web_url(product_url):
        buttons.append(("🔗 Відкрити товар", product_url))
    return await send_telegram_text(bot_token, chat_id, "\n".join(lines), buttons)


async def send_order_update(bot_token: str, chat_id: str, number: str, status: str, account: str,
                            ttn: str | None = None, old_status: str | None = None) -> tuple[bool, str]:
    """Сповіщення про зміну статусу замовлення або появу ТТН."""
    change = f"{_esc(old_status)} → {_esc(status)}" if old_status and old_status != status \
        else _esc(status)
    lines = [
        f"<b>📦 Замовлення №{_esc(number)}</b>",
        "",
        f"<b>Статус:</b> {change}",
    ]
    buttons = []
    if ttn:
        lines.append(f"<b>ТТН:</b> <code>{_esc(ttn)}</code>")
        buttons.append(("🚚 Відстежити на Новій Пошті", f"https://novaposhta.ua/tracking/?cargo_number={ttn}"))
    lines.append(f"<b>Акаунт:</b> {_esc(account)}")
    return await send_telegram_text(bot_token, chat_id, "\n".join(lines), buttons)
