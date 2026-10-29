"""Шаблон сайту: URL-и і CSS-селектори. Під новий сайт міняється лише цей конфіг (data/site.json)."""
import json
from dataclasses import asdict, dataclass, fields

from . import paths

NBU_BASE = "https://coins.bank.gov.ua"


@dataclass
class SiteConfig:
    name: str = "Нумізматичний маркетплейс НБУ"
    base_url: str = NBU_BASE

    # Вхід
    login_url: str = f"{NBU_BASE}/login.php"
    account_check_url: str = f"{NBU_BASE}/account.php"
    # Перевірено на публічній формі NBU 05.10.2026.
    email_selector: str = "form[name='login'] input[name='email_address'], input[name='email'], input#email, input#login_popup"
    password_selector: str = "form[name='login'] input[type='password'], input[name='password'], input#password, input#psw_popup"
    submit_selector: str = "form[name='login'] button[type='submit'], form[name='login'] input[type='submit'], button[type='submit'], input[type='submit']"
    # Неавторизований заголовок має посилання на login.php, авторизований —
    # на account.php або logoff.php. Це не залежить від тексту чи SVG-іконки.
    login_required_selector: str = "a[href='login.php'][aria-label='Кабінет'], a[href$='/login.php'][aria-label='Кабінет']"
    # На сторінці товару ця кнопка замінює «Купити», доки акаунт не авторизований.
    purchase_login_required_selector: str = "#r_buy_intovar .buy.login"
    logged_in_selector: str = "a[href='logoff.php'], a[href$='/logoff.php'], a[href='account.php'][aria-label='Кабінет'], a[href$='/account.php'][aria-label='Кабінет'], a[href*='logout'], .account-logout, .user-account-icon, .ty-account-info"

    # Сторінка товару
    title_selector: str = "h1.product-title, h1.page-header, .product-info h1, h1.ty-mainbox-title, h1"
    price_selector: str = ".product-price, .price, .current-price, .ty-price-num, .ty-price"
    in_stock_selector: str = "#r_buy_intovar .buy:not(.login), .btn-add-to-cart, button[name='add_to_cart'], #add-to-cart, button[id*='button_cart'], .ty-btn__add-to-cart"
    add_to_cart_selector: str = "#r_buy_intovar .buy:not(.login), .btn-add-to-cart, button[name='add_to_cart'], #add-to-cart, button[id*='button_cart'], .ty-btn__add-to-cart"

    # Кошик
    cart_url: str = f"{NBU_BASE}/cart/"
    cart_item_selector: str = ".cart-item, .cart-product-row, .basket-item, .ty-cart-content__product-title, .ty-cart-items"

    # Історія замовлень у кабінеті (лише читання)
    orders_url: str = f"{NBU_BASE}/my-orders/"
    order_row_selector: str = ".order-row, .order-card, .table-orders tbody tr, .ty-orders-list tbody tr"
    order_id_selector: str = ".order-id, .order-number, td.number, td.ty-orders-list__item"
    order_date_selector: str = ".order-date, td.date"
    order_total_selector: str = ".order-total, td.total"
    order_status_selector: str = ".order-status, td.status"
    order_ttn_selector: str = ".order-ttn, .ttn-number"
    orders_sync_minutes: int = 0

    # Автооформлення замовлення
    auto_checkout: bool = False
    checkout_dry_run: bool = True
    max_order_price: int = 0
    payment_method: str = "card_link"
    delivery_city: str = "Київ"
    delivery_warehouse: str = "Відділення №1"

    checkout_button_selector: str = ".btn-checkout, a[href*='checkout'], #to-checkout, a.ty-btn__primary, text='ОФОРМИТИ ЗАМОВЛЕННЯ', text='Оформити замовлення'"
    city_selector: str = "input[name*='city'], input#shipping_city, input#elm_city, .select2-search__field"
    city_suggestion_selector: str = ".select2-results__option, .autocomplete-suggestion, .pac-item"
    warehouse_selector: str = "input[name*='warehouse'], select#shipping_warehouse, select[name*='warehouse'], .select2-search__field"
    payment_cod_selector: str = "#pay-cod, input[value='cod'], input[id*='payment_']"
    payment_online_selector: str = "#pay-online, input[value='card'], input[value='online'], input[id*='payment_']"
    checkout_total_selector: str = ".checkout-total, .order-total-price, .cart-total, .ty-checkout-summary__total, #ty-checkout-summary__total"
    place_order_selector: str = "button#submit_order, button.btn-place-order, #place-order, button[name*='place_order'], .ty-btn__primary"
    order_number_selector: str = ".order-number, .thank-you-page, .checkout-success-id, .ty-checkout-complete__order-id"
    pay_link_selector: str = "a.pay-link, a[href*='liqpay'], a[href*='portmone'], a[href*='pay'], a[href*='wayforpay']"

    # Перевірка проксі
    ip_check_url: str = "https://api.ipify.org?format=json"

    # Поведінка
    headless: bool = False
    max_concurrency: int = 8
    page_timeout_sec: int = 30
    max_errors_in_row: int = 5
    drop_start_time: str = "09:59:00"

    # Telegram
    telegram_enabled: bool = False
    telegram_chat_id: str = ""

    # AdsPower (антидетект-браузер)
    adspower_api_url: str = "http://local.adspower.net:50325"
    adspower_api_key: str = ""

    @classmethod
    def load(cls, path=None) -> "SiteConfig":
        target = path or paths.site_config_path()
        if not target.exists():
            config = cls()
            config.save(target)
            return config
        raw = json.loads(target.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path=None) -> None:
        target = path or paths.site_config_path()
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
