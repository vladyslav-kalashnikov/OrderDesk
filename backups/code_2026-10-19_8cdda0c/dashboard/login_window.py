"""Вікно авторизації та керування акаунтами клієнтів (Supabase / Локальний режим)."""
from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, theme
from .core.auth import AuthManager


class AuthWorker(QThread):
    finished_signal = Signal(bool, str, object)

    def __init__(self, target_fn, *args, **kwargs) -> None:
        super().__init__()
        self._target_fn = target_fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._target_fn(*self._args, **self._kwargs)
            if isinstance(result, tuple) and len(result) == 3:
                ok, msg, data = result
                self.finished_signal.emit(ok, msg, data)
            elif isinstance(result, tuple) and len(result) == 2:
                ok, msg = result
                self.finished_signal.emit(ok, msg, None)
            else:
                self.finished_signal.emit(True, "", result)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc), None)


class CloudConfigDialog(QDialog):
    """Діалог налаштування параметрів Supabase (URL, Anon Key, Admin Key)."""

    def __init__(self, auth: AuthManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.setWindowTitle("Налаштування хмари Supabase")
        self.setFixedWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Хмарна база користувачів (Supabase)")
        title.setObjectName("detailsTitle")
        layout.addWidget(title)

        desc = QLabel(
            "Введіть облікові дані вашого проекту Supabase для віддаленого "
            "створення та контролю доступу клієнтів до програми."
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # URL
        layout.addWidget(QLabel("Project URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://xxxxxxxx.supabase.co")
        self.url_input.setText(self.auth.config.supabase_url)
        layout.addWidget(self.url_input)

        # Anon Key
        layout.addWidget(QLabel("API Anon Key (Public):"))
        self.anon_input = QLineEdit()
        self.anon_input.setPlaceholderText("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
        self.anon_input.setText(self.auth.config.supabase_anon_key)
        layout.addWidget(self.anon_input)

        # Service Role Key (Admin)
        layout.addWidget(QLabel("Service Role Key (Admin, необов'язково):"))
        self.service_input = QLineEdit()
        self.service_input.setPlaceholderText("Потрібен для автопідтвердження пошти при створенні з додатку")
        self.service_input.setText(self.auth.config.supabase_service_role_key)
        self.service_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.service_input)

        # Admin passcode
        layout.addWidget(QLabel("Секретний код адміністратора додатку:"))
        self.passcode_input = QLineEdit()
        self.passcode_input.setPlaceholderText("Пароль для відкриття реєстрації новим клієнтам")
        self.passcode_input.setText(self.auth.config.admin_passcode)
        layout.addWidget(self.passcode_input)

        # GitHub Repo for auto-updates
        layout.addWidget(QLabel("GitHub репозиторій для автооновлень (необов'язково):"))
        self.github_input = QLineEdit()
        self.github_input.setPlaceholderText("username/repository-name (наприклад: vladyslav/orderdesk)")
        self.github_input.setText(self.auth.config.github_repo)
        layout.addWidget(self.github_input)

        # Status text
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        self.status_lbl.hide()
        layout.addWidget(self.status_lbl)

        # Buttons
        btns = QHBoxLayout()
        self.test_btn = QPushButton("Перевірити з'єднання")
        self.test_btn.setObjectName("ghost")
        self.test_btn.clicked.connect(self._on_test)
        btns.addWidget(self.test_btn)
        btns.addStretch()

        cancel_btn = QPushButton("Скасувати")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        save_btn = QPushButton("Зберегти")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)

        layout.addLayout(btns)
        self._worker: AuthWorker | None = None

    def _on_test(self) -> None:
        url = self.url_input.text().strip()
        key = self.anon_input.text().strip()
        if not url or not key:
            self.status_lbl.setText("⚠️ Введіть Project URL та Anon Key для перевірки.")
            self.status_lbl.setStyleSheet(f"color: {theme.AMBER};")
            self.status_lbl.show()
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("⏳ Перевірка…")
        self.status_lbl.hide()

        self._worker = AuthWorker(self.auth.test_connection, url, key)
        self._worker.finished_signal.connect(self._on_test_finished)
        self._worker.start()

    def _on_test_finished(self, ok: bool, msg: str, _) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Перевірити з'єднання")
        self.status_lbl.setText(("✅ " if ok else "❌ ") + msg)
        self.status_lbl.setStyleSheet(f"color: {theme.GREEN if ok else theme.RED};")
        self.status_lbl.show()

    def _on_save(self) -> None:
        self.auth.update_config(
            supabase_url=self.url_input.text(),
            anon_key=self.anon_input.text(),
            service_key=self.service_input.text(),
            admin_code=self.passcode_input.text(),
            github_repo=self.github_input.text(),
        )
        self.accept()


class LoginDialog(QDialog):
    """Головне вікно входу та реєстрації користувачів OrderDesk."""

    def __init__(self, auth: AuthManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.setWindowTitle(f"{APP_NAME} — Вхід у систему")
        self.setFixedWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(14)

        # Header: Brand logo + Title
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        brand_row.addStretch()
        logo_mark = QLabel("◆")
        logo_mark.setObjectName("logoMark")
        logo_mark.setStyleSheet(f"color: {theme.ACCENT}; font-size: 20px; font-weight: bold;")
        brand_name = QLabel(APP_NAME)
        brand_name.setObjectName("brand")
        brand_name.setStyleSheet("font-size: 22px; font-weight: 700;")
        brand_row.addWidget(logo_mark)
        brand_row.addWidget(brand_name)
        brand_row.addStretch()
        layout.addLayout(brand_row)

        subtitle = QLabel("Вхід у систему керування замовленнями")
        subtitle.setObjectName("muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(6)

        # Tab buttons: [ Вхід ]  [ Створити акаунт ]
        tab_box = QHBoxLayout()
        tab_box.setSpacing(6)
        self.tab_login_btn = QPushButton("Вхід")
        self.tab_login_btn.setCheckable(True)
        self.tab_login_btn.setChecked(True)
        self.tab_login_btn.setObjectName("chip")
        self.tab_login_btn.clicked.connect(lambda: self._switch_tab(0))

        self.tab_signup_btn = QPushButton("Створити акаунт")
        self.tab_signup_btn.setCheckable(True)
        self.tab_signup_btn.setObjectName("chip")
        self.tab_signup_btn.clicked.connect(lambda: self._switch_tab(1))

        tab_box.addWidget(self.tab_login_btn)
        tab_box.addWidget(self.tab_signup_btn)
        layout.addLayout(tab_box)
        layout.addSpacing(4)

        # Status / Error Banner
        self.banner = QFrame()
        self.banner.hide()
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        banner_layout.addWidget(self.banner_text)
        layout.addWidget(self.banner)

        # Stacked pages (0: Login, 1: Signup)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_login_page())
        self.stack.addWidget(self._build_signup_page())
        layout.addWidget(self.stack)

        layout.addStretch()

        # Footer: Cloud Status + Settings
        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.cloud_badge = QLabel()
        self._update_cloud_badge()
        footer.addWidget(self.cloud_badge)
        footer.addStretch()

        self.config_btn = QPushButton("⚙ Налаштування хмари")
        self.config_btn.setObjectName("ghost")
        self.config_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.config_btn.clicked.connect(self._open_cloud_config)
        footer.addWidget(self.config_btn)

        layout.addLayout(footer)

        self._worker: AuthWorker | None = None

    def _update_cloud_badge(self) -> None:
        if self.auth.is_configured:
            self.cloud_badge.setText("🟢 Supabase підключено")
            self.cloud_badge.setStyleSheet(f"color: {theme.GREEN}; font-size: 11px;")
            self.cloud_badge.setToolTip(f"Хмара: {self.auth.config.supabase_url}")
        else:
            self.cloud_badge.setText("🟡 Офлайн / Локальний режим")
            self.cloud_badge.setStyleSheet(f"color: {theme.AMBER}; font-size: 11px;")
            self.cloud_badge.setToolTip("Натисніть «Налаштування хмари», щоб підключити Supabase")

    def _switch_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.tab_login_btn.setChecked(index == 0)
        self.tab_signup_btn.setChecked(index == 1)
        self._hide_banner()

    def _show_banner(self, text: str, is_error: bool = True) -> None:
        self.banner_text.setText(text)
        self.banner.setObjectName("statusBannerError" if is_error else "statusBannerSuccess")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner.show()

    def _hide_banner(self) -> None:
        self.banner.hide()

    # -------------------------------------------------------------------------
    # Вкладка Входу
    # -------------------------------------------------------------------------
    def _build_login_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

        v.addWidget(QLabel("Email:"))
        self.login_email = QLineEdit()
        self.login_email.setPlaceholderText("user@example.com")
        self.login_email.textChanged.connect(self._hide_banner)
        v.addWidget(self.login_email)

        v.addWidget(QLabel("Пароль:"))
        pwd_box = QHBoxLayout()
        pwd_box.setSpacing(6)
        self.login_pwd = QLineEdit()
        self.login_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_pwd.setPlaceholderText("••••••••")
        self.login_pwd.textChanged.connect(self._hide_banner)
        self.login_pwd.returnPressed.connect(self._do_login)

        eye_btn = QPushButton("👁")
        eye_btn.setObjectName("iconButton")
        eye_btn.setToolTip("Показати / сховати пароль")
        eye_btn.setFixedWidth(34)
        eye_btn.clicked.connect(lambda: self._toggle_pwd_visibility(self.login_pwd, eye_btn))
        pwd_box.addWidget(self.login_pwd)
        pwd_box.addWidget(eye_btn)
        v.addLayout(pwd_box)

        self.remember_cb = QCheckBox("Запам'ятати мене на цьому комп'ютері")
        self.remember_cb.setChecked(True)
        v.addWidget(self.remember_cb)
        v.addSpacing(6)

        self.login_btn = QPushButton("Увійти в OrderDesk")
        self.login_btn.setObjectName("primary")
        self.login_btn.setMinimumHeight(40)
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.clicked.connect(self._do_login)
        v.addWidget(self.login_btn)
        return w

    # -------------------------------------------------------------------------
    # Вкладка Реєстрації / Створення акаунта
    # -------------------------------------------------------------------------
    def _build_signup_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)

        info = QLabel("Створення акаунта для клієнта або нового користувача.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        v.addWidget(info)

        v.addWidget(QLabel("Email клієнта:"))
        self.signup_email = QLineEdit()
        self.signup_email.setPlaceholderText("client@example.com")
        self.signup_email.textChanged.connect(self._hide_banner)
        v.addWidget(self.signup_email)

        v.addWidget(QLabel("Пароль (від 6 символів):"))
        self.signup_pwd = QLineEdit()
        self.signup_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.signup_pwd.setPlaceholderText("••••••••")
        self.signup_pwd.textChanged.connect(self._hide_banner)
        v.addWidget(self.signup_pwd)

        v.addWidget(QLabel("Підтвердження пароля:"))
        self.signup_pwd2 = QLineEdit()
        self.signup_pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        self.signup_pwd2.setPlaceholderText("••••••••")
        self.signup_pwd2.textChanged.connect(self._hide_banner)
        v.addWidget(self.signup_pwd2)

        v.addWidget(QLabel("Секретний пароль адміністратора:"))
        self.signup_admin_code = QLineEdit()
        self.signup_admin_code.setEchoMode(QLineEdit.EchoMode.Password)
        self.signup_admin_code.setPlaceholderText("Код доступу для створення нових користувачів")
        self.signup_admin_code.textChanged.connect(self._hide_banner)
        self.signup_admin_code.returnPressed.connect(self._do_signup)
        v.addWidget(self.signup_admin_code)

        hint = QLabel("Захищає від самостійної реєстрації сторонніх осіб.")
        hint.setObjectName("hint")
        v.addWidget(hint)
        v.addSpacing(4)

        self.signup_btn = QPushButton("Створити новий акаунт")
        self.signup_btn.setObjectName("primary")
        self.signup_btn.setMinimumHeight(40)
        self.signup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signup_btn.clicked.connect(self._do_signup)
        v.addWidget(self.signup_btn)
        return w

    # -------------------------------------------------------------------------
    # Обробники дій
    # -------------------------------------------------------------------------
    def _toggle_pwd_visibility(self, field: QLineEdit, btn: QPushButton) -> None:
        if field.echoMode() == QLineEdit.EchoMode.Password:
            field.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("🔒")
        else:
            field.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("👁")

    def _do_login(self) -> None:
        email = self.login_email.text().strip()
        pwd = self.login_pwd.text().strip()
        remember = self.remember_cb.isChecked()

        if not email or not pwd:
            self._show_banner("Будь ласка, введіть email та пароль.", is_error=True)
            return

        self._set_loading(True, "⏳ Перевірка даних…")
        self._worker = AuthWorker(self.auth.login, email, pwd, remember=remember)
        self._worker.finished_signal.connect(self._on_login_finished)
        self._worker.start()

    def _on_login_finished(self, ok: bool, msg: str, session: object) -> None:
        self._set_loading(False)
        if ok:
            self.accept()
        else:
            self._show_banner(msg or "Помилка авторизації.", is_error=True)

    def _do_signup(self) -> None:
        email = self.signup_email.text().strip()
        pwd = self.signup_pwd.text().strip()
        pwd2 = self.signup_pwd2.text().strip()
        admin_code = self.signup_admin_code.text().strip()

        if not email or not pwd:
            self._show_banner("Будь ласка, заповніть усі поля.", is_error=True)
            return
        if pwd != pwd2:
            self._show_banner("Паролі не співпадають.", is_error=True)
            return
        if len(pwd) < 6:
            self._show_banner("Пароль має містити не менше 6 символів.", is_error=True)
            return

        self._set_loading(True, "⏳ Створення облікового запису…")
        self._worker = AuthWorker(self.auth.signup, email, pwd, admin_passcode=admin_code)
        self._worker.finished_signal.connect(self._on_signup_finished)
        self._worker.start()

    def _on_signup_finished(self, ok: bool, msg: str, _) -> None:
        self._set_loading(False)
        if ok:
            # Перемикаємо на вкладку входу з підставленим email
            created_email = self.signup_email.text().strip()
            self._switch_tab(0)
            self.login_email.setText(created_email)
            self.login_pwd.clear()
            self.login_pwd.setFocus()
            self._show_banner(msg or "Акаунт успішно створено! Можна увійти.", is_error=False)
        else:
            self._show_banner(msg or "Не вдалося створити акаунт.", is_error=True)

    def _set_loading(self, loading: bool, text: str = "") -> None:
        self.login_btn.setEnabled(not loading)
        self.signup_btn.setEnabled(not loading)
        self.login_email.setEnabled(not loading)
        self.login_pwd.setEnabled(not loading)
        self.signup_email.setEnabled(not loading)
        self.signup_pwd.setEnabled(not loading)
        self.signup_pwd2.setEnabled(not loading)
        self.signup_admin_code.setEnabled(not loading)
        if loading:
            if self.stack.currentIndex() == 0:
                self.login_btn.setText(text)
            else:
                self.signup_btn.setText(text)
        else:
            self.login_btn.setText("Увійти в OrderDesk")
            self.signup_btn.setText("Створити новий акаунт")

    def _open_cloud_config(self) -> None:
        dlg = CloudConfigDialog(self.auth, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._update_cloud_badge()
            self._hide_banner()
