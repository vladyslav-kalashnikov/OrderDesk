"""Мінімальний магазин на stdlib: вхід, товари з наявністю, кошик, /ip для перевірки проксі.

Розмітка збігається з селекторами за замовчуванням у data/site.json (SiteConfig).
Користувачі — у USERS. Товар «sneakers» з'являється в наявності через --restock-after секунд.
Чекаут: /cart → /checkout (місто, відділення, оплата, сума) → /checkout/done/<номер>.
Сервер навмисно НЕ захищений від дублів: кожен POST /checkout = нове замовлення (як на багатьох сайтах).
"""
import html
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

USERS = {"demo@example.com": "demo12345", "second@example.com": "second12345"}

PRODUCTS = {
    "hoodie": {"title": "Худі Basic Grey", "price": "1 299 грн"},
    "sneakers": {"title": "Кросівки Air Runner 42", "price": "3 899 грн"},
    "watch": {"title": "Смарт-годинник Pulse S", "price": "2 499 грн"},
}


# Історія замовлень у кабінеті: email → [{id, date, total, status, ttn}]
SEED_ORDERS = {
    "demo@example.com": [
        {"id": "100245", "date": "28.09.2026 14:20", "total": "3 899 грн", "status": "Доставляється", "ttn": "20450000000001"},
        {"id": "100198", "date": "21.09.2026 09:05", "total": "1 299 грн", "status": "Отримано", "ttn": "20450000000002"},
        {"id": "100301", "date": "02.10.2026 18:40", "total": "2 499 грн", "status": "Очікує відправки", "ttn": None},
    ],
    "second@example.com": [
        {"id": "100150", "date": "15.09.2026 11:11", "total": "899 грн", "status": "Скасовано", "ttn": None},
    ],
}


class ShopState:
    def __init__(self, restock_after: float):
        self.started = time.monotonic()
        self.restock_after = restock_after
        self.sessions: dict[str, str] = {}        # токен → email
        self.carts: dict[str, list[str]] = {}     # email → [slug]
        self.forced: dict[str, bool] = {}          # ручне перемикання наявності
        self.orders = {email: [dict(o) for o in items] for email, items in SEED_ORDERS.items()}
        self.next_order_id = 100400
        self.checkout_posts = 0                   # скільки разів реально натиснули «Підтвердити» (для тестів)
        self.checkout_delay = 0.0                 # штучна затримка відповіді на підтвердження, с
        self.lock = threading.Lock()

    def in_stock(self, slug: str) -> bool:
        if slug in self.forced:
            return self.forced[slug]
        if slug == "hoodie":
            return True
        if slug == "sneakers":
            return time.monotonic() - self.started >= self.restock_after
        return False  # watch — лише через /admin/restock/watch


def _price_value(price: str) -> int:
    return int("".join(ch for ch in price if ch.isdigit()))


def _format_price(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " грн"


def page(title: str, body: str, email: str | None) -> bytes:
    user = (
        f'<span class="user-email">{html.escape(email)}</span> · <a id="logout" href="/logout">Вийти</a>'
        if email else '<a href="/login">Увійти</a>'
    )
    return f"""<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui;max-width:720px;margin:24px auto;padding:0 16px}}
header{{display:flex;justify-content:space-between;border-bottom:1px solid #ddd;padding-bottom:8px;margin-bottom:16px}}
.out-of-stock{{color:#b00}}</style></head><body>
<header><a href="/">DevShop</a> <nav><a href="/cart">Кошик</a> · {user}</nav></header>
{body}</body></html>""".encode()


def make_handler(state: ShopState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # тиша в консолі
            pass

        def _email(self) -> str | None:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["session"].value if "session" in cookie else None
            return state.sessions.get(token) if token else None

        def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, headers=None):
            self._send(303, b"", headers={"Location": location, **(headers or {})})

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length") or 0)
            data = parse_qs(self.rfile.read(length).decode())
            return {k: v[0] for k, v in data.items()}

        def do_GET(self):
            email = self._email()
            path = self.path.split("?")[0]
            if path == "/":
                items = "".join(
                    f'<li><a href="/product/{slug}">{html.escape(p["title"])}</a></li>' for slug, p in PRODUCTS.items())
                self._send(200, page("DevShop", f"<h1>Каталог</h1><ul>{items}</ul>", email))
            elif path == "/ip":
                self._send(200, json.dumps({"ip": self.client_address[0]}).encode(), "application/json")
            elif path == "/login":
                form = ('<h1>Вхід</h1><form method="post" action="/login">'
                        '<input name="email" type="email" placeholder="Email"> '
                        '<input name="password" type="password" placeholder="Пароль"> '
                        '<button type="submit">Увійти</button></form>')
                if "error" in self.path:
                    form += '<p class="error">Невірний email або пароль</p>'
                self._send(200, page("Вхід", form, email))
            elif path == "/logout":
                self._redirect("/", {"Set-Cookie": "session=; Path=/; Max-Age=0"})
            elif path == "/account":
                if not email:
                    return self._redirect("/login")
                self._send(200, page("Кабінет", f"<h1>Кабінет</h1><p>Ви увійшли як {html.escape(email)}</p>", email))
            elif path.startswith("/product/"):
                slug = path.removeprefix("/product/")
                product = PRODUCTS.get(slug)
                if not product:
                    return self._send(404, page("404", "<h1>Товар не знайдено</h1>", email))
                stock = (
                    f'<form method="post" action="/cart/add/{slug}"><button id="add-to-cart" type="submit">'
                    'Додати в кошик</button></form>'
                    if state.in_stock(slug) else '<p class="out-of-stock">Немає в наявності</p>'
                )
                body = (f'<h1 class="product-title">{html.escape(product["title"])}</h1>'
                        f'<p class="price">{product["price"]}</p>{stock}')
                self._send(200, page(product["title"], body, email))
            elif path == "/cart":
                if not email:
                    return self._redirect("/login")
                with state.lock:
                    slugs = list(state.carts.get(email, []))
                items = "".join(f'<li class="cart-item">{html.escape(PRODUCTS[s]["title"])}</li>' for s in slugs)
                to_checkout = '<p><a id="to-checkout" href="/checkout">Оформити замовлення</a></p>' if slugs else ""
                self._send(200, page("Кошик", f"<h1>Кошик</h1><ul>{items or '<li>Порожньо</li>'}</ul>{to_checkout}", email))
            elif path == "/checkout":
                if not email:
                    return self._redirect("/login")
                with state.lock:
                    slugs = list(state.carts.get(email, []))
                if not slugs:
                    return self._redirect("/cart")
                total = sum(_price_value(PRODUCTS[s]["price"]) for s in slugs)
                body = ('<h1>Оформлення замовлення</h1><form method="post" action="/checkout">'
                        '<p><label>Місто <input id="city" name="city"></label></p>'
                        '<p><label>Відділення НП <input id="warehouse" name="warehouse"></label></p>'
                        '<p><label><input type="radio" id="pay-cod" name="payment" value="cod"> При отриманні</label> '
                        '<label><input type="radio" id="pay-online" name="payment" value="online"> Онлайн</label></p>'
                        f'<p>До сплати: <b class="checkout-total">{_format_price(total)}</b></p>'
                        '<button id="place-order" type="submit">Підтвердити замовлення</button></form>'
                        # Як у SPA-магазинах: відправка через fetch — браузер не зливає повторні кліки в один запит
                        '<script>const f=document.querySelector("form");f.addEventListener("submit",async(e)=>{'
                        'e.preventDefault();const r=await fetch("/checkout",{method:"POST",body:new URLSearchParams(new FormData(f))});'
                        'location.href=r.url;});</script>')
                self._send(200, page("Оформлення", body, email))
            elif path.startswith("/checkout/done/"):
                order_id = path.removeprefix("/checkout/done/")
                body = f'<h1>Дякуємо!</h1><p>Замовлення <span class="order-number">№{html.escape(order_id)}</span> прийнято.</p>'
                if "pay" in self.path:
                    body += f'<p><a class="pay-link" href="/pay/{html.escape(order_id)}">Оплатити онлайн</a></p>'
                self._send(200, page("Замовлення прийнято", body, email))
            elif path == "/cabinet/orders":
                if not email:
                    return self._redirect("/login")
                rows = []
                for o in state.orders.get(email, []):
                    ttn = f'<span class="order-ttn">{o["ttn"]}</span>' if o["ttn"] else ""
                    rows.append(
                        f'<div class="order-row"><span class="order-id">№{o["id"]}</span> · '
                        f'<span class="order-date">{o["date"]}</span> · <span class="order-total">{o["total"]}</span> · '
                        f'<span class="order-status">{html.escape(o["status"])}</span> {ttn}</div>')
                self._send(200, page("Мої замовлення", "<h1>Мої замовлення</h1>" + ("".join(rows) or "<p>Замовлень немає</p>"), email))
            elif path.startswith("/admin/order/"):
                # Зміна статусу для перевірок: /admin/order/100301?status=Відправлено&ttn=2045...
                order_id = path.removeprefix("/admin/order/")
                query = parse_qs(self.path.partition("?")[2])
                for items in state.orders.values():
                    for o in items:
                        if o["id"] == order_id:
                            o["status"] = query.get("status", [o["status"]])[0]
                            o["ttn"] = query.get("ttn", [o["ttn"]])[0]
                            return self._send(200, f"№{order_id}: {o['status']}, ТТН {o['ttn']}".encode(),
                                              "text/plain; charset=utf-8")
                self._send(404, "замовлення не знайдено".encode(), "text/plain; charset=utf-8")
            elif path.startswith("/admin/restock/"):
                # Ручне перемикання наявності для перевірок: /admin/restock/watch?off
                slug = path.removeprefix("/admin/restock/")
                state.forced[slug] = "off" not in self.path
                self._send(200, f"{slug}: {'в наявності' if state.forced[slug] else 'немає'}".encode(),
                           "text/plain; charset=utf-8")
            else:
                self._send(404, page("404", "<h1>404</h1>", email))

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/login":
                form = self._form()
                if USERS.get(form.get("email", "")) != form.get("password"):
                    return self._redirect("/login?error=1")
                token = secrets.token_urlsafe(24)
                state.sessions[token] = form["email"]
                self._redirect("/account", {"Set-Cookie": f"session={token}; Path=/; HttpOnly; SameSite=Lax"})
            elif path.startswith("/cart/add/"):
                email = self._email()
                if not email:
                    return self._redirect("/login")
                slug = path.removeprefix("/cart/add/")
                if slug in PRODUCTS and state.in_stock(slug):
                    with state.lock:
                        state.carts.setdefault(email, []).append(slug)
                self._redirect("/cart")
            elif path == "/checkout":
                email = self._email()
                if not email:
                    return self._redirect("/login")
                form = self._form()
                if state.checkout_delay:
                    time.sleep(state.checkout_delay)
                with state.lock:
                    state.checkout_posts += 1
                    slugs = state.carts.pop(email, [])
                    if not slugs or not form.get("city") or not form.get("warehouse") or not form.get("payment"):
                        return self._redirect("/cart")
                    order_id = str(state.next_order_id)
                    state.next_order_id += 1
                    total = sum(_price_value(PRODUCTS[s]["price"]) for s in slugs)
                    state.orders.setdefault(email, []).insert(0, {
                        "id": order_id, "date": time.strftime("%d.%m.%Y %H:%M"), "total": _format_price(total),
                        "status": "Нове", "ttn": None,
                    })
                self._redirect(f"/checkout/done/{order_id}" + ("?pay=1" if form["payment"] == "online" else ""))
            else:
                self._send(404, b"")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, restock_after: float = 30) -> ThreadingHTTPServer:
    """Створює сервер (не блокує). Запуск: server.serve_forever() — у потоці або в main."""
    state = ShopState(restock_after)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.state = state  # тести читають/міняють стан магазину (лічильник замовлень, затримка)
    return server
