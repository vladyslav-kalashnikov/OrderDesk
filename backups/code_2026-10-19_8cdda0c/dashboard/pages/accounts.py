"""«Акаунти»: email + пароль (у Keychain) + проксі; вхід і збереження сесії."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ..core.db import Database
from ..core.engine import Engine
from ..core.entities import Account, AccountStatus
from ..core.secrets import delete_secret, get_secret, set_secret
from .common import DataTable, Page, TableOrEmpty, button, confirm, info, state_cell

COL_ID, COL_EMAIL, COL_PROXY, COL_DELIVERY, COL_PAYMENT, COL_SESSION, COL_STATUS, COL_ERROR = range(8)


class PasswordDialog(QDialog):
    """Новий пароль двічі — щоб помилка в одному символі не зламала вхід."""

    def __init__(self, email: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Змінити пароль")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        account = QLineEdit(email)
        account.setReadOnly(True)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat = QLineEdit()
        self.repeat.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Акаунт", account)
        form.addRow("Новий пароль", self.password)
        form.addRow("Ще раз", self.repeat)
        layout.addLayout(form)
        hint = QLabel("Тут змінюється лише пароль, збережений у програмі (Keychain). "
                      "На самому сайті пароль міняйте в його налаштуваннях.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.password.text():
            info(self, "Пароль", "Введіть новий пароль.")
            return
        if self.password.text() != self.repeat.text():
            info(self, "Пароль", "Паролі не збігаються.")
            return
        self.accept()


class AccountDialog(QDialog):
    def __init__(self, db: Database, account: Account | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Акаунт" if account else "Новий акаунт")
        self.setMinimumWidth(440)
        self._account = account
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.email = QLineEdit(account.email if account else "")
        self.email.setPlaceholderText("user@example.com")
        self.email.setReadOnly(account is not None)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        has_password = bool(account and get_secret("account", account.email))
        self.password.setPlaceholderText("збережено в Keychain — залиште порожнім" if has_password else "пароль від сайту")
        self.proxy = QComboBox()
        self.proxy.addItem("Без проксі", None)
        for proxy in db.proxies():
            self.proxy.addItem(f"{proxy.display}  ·  {proxy.status}", proxy.id)
        if account and account.proxy_id:
            self.proxy.setCurrentIndex(max(0, self.proxy.findData(account.proxy_id)))

        self.carrier = QComboBox()
        self.carrier.addItem("Нова Пошта", "nova")
        self.carrier.addItem("Укрпошта", "ukr")
        if account:
            self.carrier.setCurrentIndex(1 if getattr(account, "delivery_carrier", "nova") == "ukr" else 0)

        self.city = QLineEdit(account.delivery_city or "" if account else "")
        self.city.setPlaceholderText("напр. Київ (порожньо — з налаштувань)")

        self.warehouse = QLineEdit(account.delivery_warehouse or "" if account else "")
        self.warehouse.setPlaceholderText("напр. Відділення №1 або індекс")

        self.payment = QComboBox()
        self.payment.addItem("Оплата карткою онлайн", "card_link")
        self.payment.addItem("Післяплата (накладений платіж)", "cod")
        if account and getattr(account, "payment_method", "card_link") == "cod":
            self.payment.setCurrentIndex(1)

        form.addRow("Email", self.email)
        form.addRow("Пароль", self.password)
        form.addRow("Проксі", self.proxy)
        form.addRow("Служба доставки", self.carrier)
        form.addRow("Місто", self.city)
        form.addRow("Відділення / індекс", self.warehouse)
        form.addRow("Спосіб оплати", self.payment)
        layout.addLayout(form)
        hint = QLabel("Пароль зберігається в Keychain. Доставка та оплата діють для авточекауту цього акаунта.")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if "@" not in self.email.text():
            info(self, "Email", "Вкажіть коректний email.")
            return
        if not self._account and not self.password.text():
            info(self, "Пароль", "Для нового акаунта потрібен пароль.")
            return
        self.accept()


class AccountsPage(Page):
    def __init__(self, db: Database, engine: Engine, parent=None):
        super().__init__("Акаунти", "Сесії зберігаються після входу і використовуються всіма завданнями акаунта", parent)
        self.db, self.engine = db, engine

        add = button("+ Акаунт", "primary")
        login = button("Увійти", tooltip="Увійти автоматично (логін/пароль) і зберегти сесію")
        manual = button("Увійти вручну", tooltip="Відкриє вікно браузера: капчу чи 2FA вводите самі, сесія збережеться")
        password = button("Змінити пароль", tooltip="Оновити збережений у Keychain пароль виділеного акаунта")
        reset = button("Скинути сесію")
        delete = button("Видалити", "danger")
        for b in (add, login, manual, password, reset, delete):
            self.actions.addWidget(b)
        add.clicked.connect(self.add_account)
        password.clicked.connect(self.change_password)
        login.clicked.connect(lambda: self.login_selected(False))
        manual.clicked.connect(lambda: self.login_selected(True))
        reset.clicked.connect(self.reset_sessions)
        delete.clicked.connect(self.delete_selected)

        self.table = DataTable(["ID", "EMAIL", "ПРОКСІ", "ДОСТАВКА", "ОПЛАТА", "СЕСІЯ", "СТАТУС", "ОСТАННЯ ПОМИЛКА"], stretch=(COL_ERROR,))
        self.table.doubleClicked.connect(lambda index: self.edit_account(self.table.row_id(index.row())))
        self.stack = TableOrEmpty(self.table, "Акаунтів ще немає",
                                  "Крок 1: «+ Акаунт» (email і пароль). Крок 2: «Увійти вручну» — пройдіть вхід у вікні браузера.")
        tip = QLabel("Порада: подвійний клік по акаунту відкриває його налаштування. «Увійти вручну» — найнадійніший "
                     "спосіб, якщо сайт просить перевірку безпеки.")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        self.body.addWidget(tip)
        self.body.addWidget(self.stack)
        self.refresh()

    def refresh(self) -> None:
        proxies = {p.id: p.display for p in self.db.proxies()}
        rows = []
        for a in self.db.accounts():
            has_session = self.engine.state_path(a.id).exists()
            session = ("✓ збережена" + (f" · {a.session_saved_at}" if a.session_saved_at else "")) if has_session else "— потрібен вхід"
            carrier_label = "НП" if getattr(a, "delivery_carrier", "nova") == "nova" else "УП"
            city_str = a.delivery_city or ""
            wh_str = a.delivery_warehouse or ""
            if city_str and wh_str:
                delivery_disp = f"{carrier_label}: {city_str}, {wh_str}"
            elif city_str:
                delivery_disp = f"{carrier_label}: {city_str}"
            elif wh_str:
                delivery_disp = f"{carrier_label}: {wh_str}"
            else:
                delivery_disp = f"{carrier_label}: дефолт"
            payment_disp = "Картка" if getattr(a, "payment_method", "card_link") == "card_link" else "Післяплата"
            rows.append((a.id, [
                str(a.id), a.email, proxies.get(a.proxy_id, "—"), delivery_disp, payment_disp, session, state_cell(a.status), a.last_error or "",
            ]))
        self.table.set_rows(rows)
        self.stack.update_state()
        active = sum(1 for a in self.db.accounts() if a.status == "Активний")
        self.subtitle.setText(f"Акаунтів: {len(rows)} · активних: {active}")

    def add_account(self) -> None:
        dialog = AccountDialog(self.db, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        email = dialog.email.text().strip()
        if any(a.email == email for a in self.db.accounts()):
            info(self, "Вже є", f"Акаунт {email} уже додано.")
            return
        set_secret("account", email, dialog.password.text())
        self.db.add_account(
            email,
            dialog.proxy.currentData(),
            delivery_carrier=dialog.carrier.currentData(),
            delivery_city=dialog.city.text().strip() or None,
            delivery_warehouse=dialog.warehouse.text().strip() or None,
            payment_method=dialog.payment.currentData(),
        )
        self.refresh()

    def edit_account(self, account_id: int) -> None:
        account = self.db.account(account_id)
        if not account:
            return
        dialog = AccountDialog(self.db, account, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.password.text():
            set_secret("account", account.email, dialog.password.text())
        if dialog.proxy.currentData() != account.proxy_id:
            # Нова IP-адреса зі старими cookies виглядає підозріло — сесію скидаємо
            self._drop_session(account.id)
        self.db.update_account_settings(
            account.id,
            dialog.proxy.currentData(),
            delivery_carrier=dialog.carrier.currentData(),
            delivery_city=dialog.city.text().strip() or None,
            delivery_warehouse=dialog.warehouse.text().strip() or None,
            payment_method=dialog.payment.currentData(),
        )
        self.refresh()

    def change_password(self) -> None:
        ids = self.table.selected_ids()
        if len(ids) != 1:
            info(self, "Оберіть акаунт", "Виділіть один акаунт у таблиці.")
            return
        account = self.db.account(ids[0])
        dialog = PasswordDialog(account.email, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        set_secret("account", account.email, dialog.password.text())
        if account.status == AccountStatus.ERROR.value:
            # Помилка могла бути через старий пароль — даємо наступному входу спробувати новий
            self.db.set_account_status(account.id, AccountStatus.NEW)
        self.engine.log("INFO", f"account#{account.id}", f"{account.email}: пароль оновлено")
        self.refresh()
        info(self, "Пароль", f"Пароль для {account.email} оновлено.")

    def login_selected(self, interactive: bool) -> None:
        ids = self.table.selected_ids()
        if not ids:
            info(self, "Оберіть акаунт", "Виділіть акаунт(и) в таблиці.")
            return
        if interactive and len(ids) > 1:
            info(self, "По одному", "Ручний вхід — по одному акаунту за раз.")
            return
        for account_id in ids:
            self.table.set_cell(account_id, COL_STATUS, "Вхід…")
            self.engine.login_account(account_id, interactive)

    def _drop_session(self, account_id: int) -> None:
        self.engine.state_path(account_id).unlink(missing_ok=True)
        self.db.clear_account_session(account_id)

    def reset_sessions(self) -> None:
        ids = self.table.selected_ids()
        if not ids or not confirm(self, "Скинути сесію", f"Видалити збережені сесії ({len(ids)})? "
                                                         "Наступний запуск увійде заново."):
            return
        for account_id in ids:
            self._drop_session(account_id)
        self.refresh()

    def delete_selected(self) -> None:
        ids = self.table.selected_ids()
        if not ids:
            return
        if set(ids) & {t.account_id for t in self.db.tasks() if t.id in self.engine.running_task_ids()}:
            info(self, "Є запущені завдання", "Спочатку зупиніть завдання цих акаунтів.")
            return
        if not confirm(self, "Видалити акаунти",
                       f"Видалити акаунтів: {len(ids)}? Разом з ними видаляться їхні завдання, "
                       "сесії та паролі в Keychain."):
            return
        for account_id in ids:
            account = self.db.account(account_id)
            delete_secret("account", account.email)
            self._drop_session(account_id)
            self.db.delete_account(account_id)
        self.refresh()
