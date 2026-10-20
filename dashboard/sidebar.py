"""Ліва бокова панель: навігація, стан системи, статистика за сьогодні, футер."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QPushButton, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QScrollArea, QVBoxLayout, QWidget,
)

from . import APP_AUTHOR, APP_NAME, APP_VERSION, theme
from .models import DailyStats, SystemStatus
from .widgets import RingProgress, SectionLabel, StatCard, StatusDot

NAV_ITEMS = [
    ("⌂", "Кабінет"),
    ("◉", "Акаунти"),
    ("◎", "Моніторинг"),
    ("⇄", "Проксі"),
    ("≡", "Логи"),
    ("⚙", "Налаштування"),
    ("?", "Інструкція"),
]
NAV_ROW_HEIGHT = 33


class Sidebar(QFrame):
    page_changed = Signal(int)
    theme_toggled = Signal()
    font_changed = Signal(int)
    logout_requested = Signal()
    update_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(236)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(self._build_brand())

        # Блок поточного користувача
        user_container = QWidget()
        user_layout = QVBoxLayout(user_container)
        user_layout.setContentsMargins(12, 0, 12, 6)
        user_layout.setSpacing(0)
        self.user_box = self._build_user_box()
        user_layout.addWidget(self.user_box)
        outer.addWidget(user_container)

        # Середина прокручується, якщо вікно низьке; бренд і футер завжди на місці
        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 2, 12, 8)
        body_layout.setSpacing(6)

        self.nav = self._build_nav()
        body_layout.addWidget(self.nav)
        body_layout.addSpacing(4)
        body_layout.addWidget(SectionLabel("Система"))
        body_layout.addWidget(self._build_system())
        body_layout.addSpacing(4)
        body_layout.addWidget(SectionLabel("Статистика"))
        body_layout.addLayout(self._build_stats())
        body_layout.addWidget(self._build_ring())
        body_layout.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_footer())

    def _build_user_box(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

        icon = QLabel("👤")
        icon.setStyleSheet("font-size: 13px;")
        layout.addWidget(icon)

        self._user_email_lbl = QLabel("Користувач")
        self._user_email_lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(self._user_email_lbl, 1)

        logout_btn = QPushButton("⎋")
        logout_btn.setObjectName("iconButton")
        logout_btn.setToolTip("Вийти з акаунта")
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_btn.clicked.connect(self.logout_requested.emit)
        layout.addWidget(logout_btn)
        return card

    def set_user_email(self, email: str) -> None:
        short = email if len(email) <= 17 else email[:15] + "…"
        self._user_email_lbl.setText(short)
        self._user_email_lbl.setToolTip(f"Вхід виконано як: {email}\nНатисніть ⎋ для виходу")

    def _build_brand(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(16, 12, 16, 8)
        row.setSpacing(8)
        mark = QLabel("◆")
        mark.setObjectName("logoMark")
        name = QLabel(APP_NAME)
        name.setObjectName("brand")
        row.addWidget(mark)
        row.addWidget(name)
        row.addStretch()
        return row

    def _build_nav(self) -> QListWidget:
        nav = QListWidget()
        nav.setObjectName("nav")
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setCursor(Qt.CursorShape.PointingHandCursor)
        for icon, title in NAV_ITEMS:
            item = QListWidgetItem(f"{icon}   {title}")
            item.setSizeHint(QSize(0, NAV_ROW_HEIGHT))
            nav.addItem(item)
        # +3 на вертикальні margin-и пунктів із QSS
        nav.setFixedHeight(len(NAV_ITEMS) * (NAV_ROW_HEIGHT + 3) + 4)
        nav.setCurrentRow(0)
        nav.currentRowChanged.connect(self.page_changed)
        return nav

    def _build_system(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        api_value = QHBoxLayout()
        api_value.setSpacing(6)
        self._api_dot = StatusDot(theme.GREEN)
        self._api_text = QLabel("OK")
        api_value.addWidget(self._api_dot)
        api_value.addWidget(self._api_text)

        self._accounts = QLabel("0 / 0")
        self._traffic = QLabel("0.00 MB")
        layout.addLayout(self._status_row("Стан API", api_value))
        layout.addLayout(self._status_row("Акаунти", self._accounts))
        layout.addLayout(self._status_row("Трафік", self._traffic))
        return card

    @staticmethod
    def _status_row(title: str, value) -> QHBoxLayout:
        row = QHBoxLayout()
        key = QLabel(title)
        key.setObjectName("muted")
        row.addWidget(key)
        row.addStretch()
        if isinstance(value, QHBoxLayout):
            row.addLayout(value)
        else:
            value.setStyleSheet("font-weight: 600;")
            row.addWidget(value)
        return row

    def _build_stats(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self._runs = StatCard("Запусків")
        self._success = StatCard("Успішно")
        self._errors = StatCard("Помилок", higher_is_better=False)
        grid.addWidget(self._runs, 0, 0)
        grid.addWidget(self._success, 0, 1)
        grid.addWidget(self._errors, 0, 2)
        return grid

    def _build_ring(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ring = RingProgress("успішних купівль")
        self._ring.setFixedHeight(95)
        layout.addWidget(self._ring)
        return card

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("sidebarFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(14, 8, 14, 10)
        version = QLabel(APP_VERSION)
        version.setObjectName("muted")
        author = QLabel(APP_AUTHOR)
        author.setObjectName("small")
        row.addWidget(version)
        row.addStretch()
        for text, tip, handler in (("⟳", "Перевірити оновлення", self.update_requested.emit),
                                   ("◐", "Світла / темна тема", self.theme_toggled.emit),
                                   ("A−", "Менший шрифт", lambda: self.font_changed.emit(-1)),
                                   ("A+", "Більший шрифт", lambda: self.font_changed.emit(1))):
            b = QPushButton(text)
            b.setObjectName("iconButton")
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, h=handler: h())
            row.addWidget(b)
        return footer

    def set_log_badge(self, count: int) -> None:
        item = self.nav.item(4)
        item.setText(f"{NAV_ITEMS[4][0]}   {NAV_ITEMS[4][1]}" + (f"   ({count})" if count else ""))

    def update_status(self, system: SystemStatus, stats: DailyStats, api_message: str = "") -> None:
        color = theme.GREEN if system.api_ok else theme.RED
        self._api_dot.set_color(color)
        self._api_text.setText("OK" if system.api_ok else "Помилка")
        self._api_text.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._api_text.setToolTip(api_message)
        self._accounts.setText(f"{system.accounts_active} / {system.accounts_total}")
        self._traffic.setText(f"{system.traffic_mb:.2f} MB")
        self._runs.set_values(stats.runs, stats.runs_delta)
        self._success.set_values(stats.success, stats.success_delta)
        self._errors.set_values(stats.errors, stats.errors_delta)
        self._ring.set_value(stats.success_rate)
