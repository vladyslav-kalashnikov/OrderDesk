"""«Налаштування»: шаблон сайту (URL + селектори), поведінка рушія, Telegram."""
from dataclasses import fields

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.engine import Engine
from ..core.secrets import get_secret, set_secret
from ..core.site_config import SiteConfig
from ..widgets import SectionCard
from .common import Page, button, confirm, info

# (поле SiteConfig, підпис, підказка)
SECTIONS = {
    "Сайт": [
        ("name", "Назва", ""),
        ("base_url", "Головна", ""),
        ("login_url", "Сторінка входу", ""),
        ("account_check_url", "Перевірка входу", "сторінка, яку бачить лише залогінений"),
        ("cart_url", "Кошик", ""),
        ("ip_check_url", "Перевірка IP проксі", "має повертати {\"ip\": …} або IP текстом"),
    ],
    "Селектори (CSS)": [
        ("email_selector", "Поле email", ""),
        ("password_selector", "Поле пароля", ""),
        ("submit_selector", "Кнопка входу", ""),
        ("login_required_selector", "Ознака потреби входу", "значок / посилання, що бачить лише гість"),
        ("purchase_login_required_selector", "Кнопка авторизації покупки", "видно замість «Купити», доки акаунт не авторизований"),
        ("logged_in_selector", "Ознака входу", "елемент, який є лише після входу"),
        ("title_selector", "Назва товару", ""),
        ("price_selector", "Ціна", ""),
        ("in_stock_selector", "Ознака наявності", "видимий лише коли товар можна купити"),
        ("add_to_cart_selector", "Кнопка «в кошик»", ""),
        ("cart_item_selector", "Товар у кошику", ""),
    ],
}
# Секції без обов'язкових полів: їх не перевіряємо при збереженні
OPTIONAL_SECTIONS = {
    "Замовлення в кабінеті": [
        ("orders_url", "Сторінка «Мої замовлення»", ""),
        ("order_row_selector", "Рядок / картка замовлення", ""),
        ("order_id_selector", "Номер (усередині рядка)", ""),
        ("order_date_selector", "Дата", ""),
        ("order_total_selector", "Сума", ""),
        ("order_status_selector", "Статус", ""),
        ("order_ttn_selector", "ТТН", "якщо немає — лишиться «—»"),
    ],
    "Автооформлення: доставка і селектори": [
        ("payment_method", "Оплата", "cod — при отриманні, card_link — посилання на оплату"),
        ("delivery_city", "Місто (Нова Пошта)", ""),
        ("delivery_warehouse", "Відділення", ""),
        ("checkout_button_selector", "Кнопка «Оформити» в кошику", "порожньо — форма одразу в кошику"),
        ("city_selector", "Поле міста", ""),
        ("city_suggestion_selector", "Підказка міста", "перший варіант автопідказки, якщо є"),
        ("warehouse_selector", "Поле відділення", ""),
        ("payment_cod_selector", "Варіант «при отриманні»", ""),
        ("payment_online_selector", "Варіант «онлайн»", ""),
        ("checkout_total_selector", "Сума до сплати", "звіряється з лімітом перед підтвердженням"),
        ("place_order_selector", "Кнопка підтвердження", ""),
        ("order_number_selector", "Номер замовлення", "на сторінці подяки"),
        ("pay_link_selector", "Посилання на оплату", ""),
    ],
}
# Пояснення «простою мовою» для полів без власної підказки
HINTS = {
    "name": "Довільна назва магазину — лише для вашої зручності.",
    "base_url": "Адреса головної сторінки сайту, наприклад https://coins.bank.gov.ua",
    "login_url": "Сторінка, де вводять email і пароль.",
    "cart_url": "Сторінка кошика. Якщо сайт показує кошик у спливаючому вікні, лишіть як є.",
    "email_selector": "CSS-селектор поля email на сторінці входу. Міняти потрібно лише для іншого сайту.",
    "password_selector": "CSS-селектор поля пароля.",
    "submit_selector": "CSS-селектор кнопки «Увійти».",
    "title_selector": "Де на сторінці товару лежить його назва.",
    "price_selector": "Де на сторінці товару лежить ціна.",
    "add_to_cart_selector": "Кнопка «Купити» / «В кошик», яку натискає програма.",
    "cart_item_selector": "Елемент, за яким програма розуміє, що товар уже в кошику.",
    "orders_url": "Сторінка історії замовлень у кабінеті магазину.",
    "order_row_selector": "Один рядок або картка замовлення в списку.",
    "order_id_selector": "Номер замовлення всередині рядка.",
    "order_date_selector": "Дата замовлення всередині рядка.",
    "order_total_selector": "Сума замовлення всередині рядка.",
    "order_status_selector": "Статус замовлення всередині рядка.",
    "delivery_city": "Місто доставки за замовчуванням (для автооформлення).",
    "delivery_warehouse": "Відділення або індекс за замовчуванням.",
    "checkout_button_selector": "Кнопка переходу до оформлення в кошику.",
    "city_selector": "Поле «Місто» у формі оформлення.",
    "warehouse_selector": "Поле «Відділення» у формі оформлення.",
    "payment_cod_selector": "Перемикач «Оплата при отриманні».",
    "payment_online_selector": "Перемикач «Оплата онлайн».",
    "place_order_selector": "Кнопка остаточного підтвердження замовлення.",
    "pay_link_selector": "Посилання на сторінку оплати після оформлення.",
    "max_concurrency": "Скільки сторінок програма перевіряє одночасно. Для одного товару лишайте 1.",
    "page_timeout_sec": "Скільки секунд чекати на завантаження сторінки, перш ніж вважати збоєм.",
    "max_errors_in_row": "Після скількох збоїв поспіль завдання зупиниться саме.",
    "orders_sync_minutes": "Як часто оновлювати замовлення з кабінету. 0 — лише вручну.",
    "headless": "Увімкнено — браузер працює невидимо. Тоді ПРОЙТИ перевірку безпеки сайту неможливо, "
                "тому для купівлі лишайте вимкненим.",
}
INT_FIELDS = [
    ("max_concurrency", "Одночасних перевірок", 1, 32),
    ("page_timeout_sec", "Таймаут сторінки, с", 5, 180),
    ("max_errors_in_row", "Збоїв поспіль до зупинки", 1, 100),
    ("orders_sync_minutes", "Автооновлення замовлень, хв (0 — вимк.)", 0, 1440),
]


class SettingsPage(Page):
    def __init__(self, engine: Engine, parent=None):
        super().__init__("Налаштування", "Шаблон сайту: під новий магазин достатньо змінити URL і селектори", parent)
        self.engine = engine
        self._inputs: dict[str, QWidget] = {}

        reset = button("Демо-налаштування", tooltip="Повернути налаштування під локальний devsite")
        save = button("Зберегти", "primary")
        reset.clicked.connect(self.reset_to_demo)
        save.clicked.connect(self.save)
        self.actions.addWidget(reset)
        self.actions.addWidget(save)

        scroll = QScrollArea()
        scroll.setObjectName("detailsScroll")  # прозорий фон, як у панелі деталей
        scroll.setWidgetResizable(True)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(12)

        intro = QLabel("Для магазину coins.bank.gov.ua все вже заповнено — звичайно нічого міняти не треба. "
                       "Під кожним полем є пояснення, що воно означає. Селектори (CSS) змінюйте лише якщо сайт "
                       "перебудував сторінки або ви підключаєте інший магазин.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        column.addWidget(intro)

        for title, items in (SECTIONS | OPTIONAL_SECTIONS).items():
            card = SectionCard(title)
            form = self._form()
            for name, label, hint in items:
                edit = QLineEdit()
                shown = hint or HINTS.get(name, "")
                if hint:
                    edit.setPlaceholderText(hint)
                edit.setToolTip(shown)
                self._inputs[name] = edit
                form.addRow(self._label(label), self._with_hint(edit, shown))
            card.add_widget(self._wrap(form))
            column.addWidget(card)

        behaviour = SectionCard("Поведінка")
        form = self._form()
        self.headless = QCheckBox("Без вікна браузера (headless)")
        self._inputs["headless"] = self.headless
        form.addRow(self._label(""), self._with_hint(self.headless, HINTS["headless"]))
        for name, label, low, high in INT_FIELDS:
            spin = QSpinBox()
            spin.setRange(low, high)
            self._inputs[name] = spin
            form.addRow(self._label(label), self._with_hint(spin, HINTS.get(name, "")))
        behaviour.add_widget(self._wrap(form))
        column.addWidget(behaviour)

        checkout = SectionCard("Автооформлення замовлення")
        form = self._form()
        self.auto_checkout = QCheckBox("Після додавання в кошик оформлювати замовлення (завдання «Моніторинг + кошик»)")
        self.dry_run = QCheckBox("Пробний режим: заповнювати все, але НЕ натискати «Підтвердити»")
        self.max_price = QSpinBox()
        self.max_price.setRange(0, 1_000_000)
        self.max_price.setSingleStep(100)
        self.max_price.setSuffix(" грн")
        self.max_price.setSpecialValueText("не задано — справжні замовлення заборонені")
        self._inputs.update(auto_checkout=self.auto_checkout, checkout_dry_run=self.dry_run,
                            max_order_price=self.max_price)
        form.addRow(self._label(""), self.auto_checkout)
        form.addRow(self._label(""), self.dry_run)
        form.addRow(self._label("Ліміт суми замовлення"),
                    self._with_hint(self.max_price, "Замовлення дорожче за цю суму програма не оформлюватиме."))
        checkout.add_widget(self._wrap(form))
        hint = QLabel("Не більше одного замовлення на завдання. Картку програма не вводить: оплата при отриманні "
                      "або посилання на оплату, яке ви відкриваєте самі.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        checkout.add_widget(hint)
        column.addWidget(checkout)

        telegram = SectionCard("Telegram-сповіщення")
        form = self._form()
        self.tg_enabled = QCheckBox("Надсилати: товар з'явився / у кошику, зміна статусу замовлення, нова ТТН")
        self._inputs["telegram_enabled"] = self.tg_enabled
        self.tg_chat = QLineEdit()
        self.tg_chat.setPlaceholderText("напр. 123456789 — дізнатись можна в @userinfobot")
        self._inputs["telegram_chat_id"] = self.tg_chat
        self.tg_token = QLineEdit()
        self.tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tg_test = button("Надіслати тестове", tooltip="Перевірити бота з поточними значеннями (зберігати не обов'язково)")
        self.tg_test.clicked.connect(self.send_test)
        form.addRow(self._label(""), self.tg_enabled)
        form.addRow(self._label("Chat ID"), self._with_hint(self.tg_chat, "Ваш числовий ідентифікатор у Telegram."))
        form.addRow(self._label("Токен бота"), self._with_hint(self.tg_token, "Створіть бота в @BotFather і вставте токен. Він зберігається в Keychain."))
        form.addRow(self._label(""), self.tg_test)
        telegram.add_widget(self._wrap(form))
        hint = QLabel("Спершу напишіть боту /start — інакше Telegram не дозволить йому писати вам першим.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        telegram.add_widget(hint)
        column.addWidget(telegram)
        column.addStretch()

        scroll.setWidget(content)
        self.body.addWidget(scroll)
        self.load(engine.config)

    @staticmethod
    def _with_hint(widget: QWidget, hint: str) -> QWidget:
        """Поле + сірий пояснювальний текст під ним."""
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)
        box.addWidget(widget)
        if hint:
            label = QLabel(hint)
            label.setObjectName("hint")
            label.setWordWrap(True)
            box.addWidget(label)
        return holder

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setSpacing(10)
        form.setHorizontalSpacing(16)
        return form

    @staticmethod
    def _wrap(form: QFormLayout) -> QWidget:
        holder = QWidget()
        holder.setLayout(form)
        return holder

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("formLabel")
        label.setMinimumWidth(190)
        return label

    def load(self, config: SiteConfig) -> None:
        for name, widget in self._inputs.items():
            value = getattr(config, name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            else:
                widget.setText(str(value))
        self.tg_token.clear()
        self.tg_token.setPlaceholderText("збережено в Keychain — залиште порожнім" if get_secret("telegram", "token")
                                         else "123456:ABC… від @BotFather")

    def _collect(self) -> SiteConfig:
        values = {}
        for f in fields(SiteConfig):
            widget = self._inputs.get(f.name)
            if widget is None:
                continue
            if isinstance(widget, QCheckBox):
                values[f.name] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                values[f.name] = widget.value()
            else:
                values[f.name] = widget.text().strip()
        return SiteConfig(**values)

    def save(self) -> None:
        config = self._collect()
        missing = [label for items in SECTIONS.values() for name, label, _ in items
                   if not getattr(config, name) and name not in ("price_selector", "title_selector")]
        if missing:
            info(self, "Не все заповнено", "Порожні поля: " + ", ".join(missing))
            return
        if config.payment_method not in ("cod", "card_link"):
            info(self, "Оплата", "Спосіб оплати: cod або card_link.")
            return
        if config.auto_checkout and not config.checkout_dry_run:
            if config.max_order_price <= 0:
                info(self, "Ліміт ціни", "Щоб вимкнути пробний режим, задайте ліміт суми замовлення.")
                return
            was_live = self.engine.config.auto_checkout and not self.engine.config.checkout_dry_run
            if not was_live and not confirm(
                    self, "Справжні замовлення",
                    f"Пробний режим вимкнено: програма натискатиме «Підтвердити замовлення» на "
                    f"{config.name} (до {config.max_order_price} грн, одне замовлення на завдання). Продовжити?"):
                return
        config.save()
        if self.tg_token.text().strip():
            set_secret("telegram", "token", self.tg_token.text().strip())
        self.engine.reload_config()
        self.load(self.engine.config)
        info(self, "Збережено", "Налаштування збережено.")

    def send_test(self) -> None:
        chat_id = self.tg_chat.text().strip()
        token = self.tg_token.text().strip() or None
        if not chat_id or not (token or get_secret("telegram", "token")):
            info(self, "Telegram", "Вкажіть chat ID і токен бота.")
            return
        self.tg_test.setEnabled(False)
        self.tg_test.setText("Надсилаю…")
        future = self.engine.send_test_notification(chat_id, token)

        # Результат приходить у потоці рушія — опитуємо future з UI-потоку, не чіпаючи віджети звідти
        def poll() -> None:
            if not future.done():
                QTimer.singleShot(200, poll)
                return
            self.tg_test.setEnabled(True)
            self.tg_test.setText("Надіслати тестове")
            try:
                ok, detail = future.result()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, str(exc)
            info(self, "Telegram", "Повідомлення надіслано — перевірте Telegram." if ok else f"Не вдалося: {detail}")

        QTimer.singleShot(200, poll)

    def reset_to_demo(self) -> None:
        if confirm(self, "Демо-налаштування", "Замінити поточні URL і селектори демо-значеннями (devsite)?"):
            current = self._collect()
            demo = SiteConfig(telegram_enabled=current.telegram_enabled, telegram_chat_id=current.telegram_chat_id)
            self.load(demo)
