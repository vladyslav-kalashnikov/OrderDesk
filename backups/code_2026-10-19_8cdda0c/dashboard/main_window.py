"""Головне вікно: бокова панель + сторінки; слухає рушій і оновлює дані."""
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QSplitter, QStackedWidget, QSystemTrayIcon,
)

from . import APP_NAME, theme
from .bridge import EngineBridge
from .core.auth import AuthManager
from .core.db import Database
from .core.engine import Engine
from .core.entities import AccountStatus
from .details_panel import DetailsPanel
from .humanize import account_id_of, explain
from .models import Order, SystemStatus
from .orders_view import OrdersView
from .pages.accounts import AccountsPage
from .pages.help import HelpPage
from .pages.logs import LogsPage
from .pages.monitoring import MonitoringPage
from .pages.proxies import ProxiesPage
from .pages.settings import SettingsPage
from .sidebar import Sidebar

DETAILS_WIDTH = 370


class MainWindow(QMainWindow):
    def __init__(self, db: Database, engine: Engine, bridge: EngineBridge, auth: AuthManager | None = None):
        super().__init__()
        self.db, self.engine, self.auth = db, engine, auth
        self.relogin_requested = False
        self.setWindowTitle(f"{APP_NAME} — моніторинг і замовлення")
        self.setMinimumSize(1180, 720)

        root = QFrame()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.pages = QStackedWidget()

        # Кабінет = таблиці + панель деталей замовлення справа
        self.cabinet = OrdersView(db)
        self.details = DetailsPanel()
        self.details.hide()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.cabinet)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        self.accounts = AccountsPage(db, engine)
        self.monitoring = MonitoringPage(db, engine)
        self.proxies = ProxiesPage(db, engine)
        self.logs = LogsPage(db)
        self.settings = SettingsPage(engine)
        self.help = HelpPage()
        # Порядок = порядок NAV_ITEMS у sidebar.py
        for page in (self.splitter, self.accounts, self.monitoring, self.proxies, self.logs, self.settings, self.help):
            self.pages.addWidget(page)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.sidebar.page_changed.connect(self._on_page_changed)
        self.cabinet.order_selected.connect(self.show_order)
        self.cabinet.sync_requested.connect(lambda: self.engine.sync_orders())
        self.details.closed.connect(self.hide_details)

        bridge.taskChanged.connect(self._on_task_changed)
        bridge.dataChanged.connect(self._on_data_changed)
        bridge.logAdded.connect(self._on_log)
        self.sidebar.theme_toggled.connect(self.toggle_theme)
        self.sidebar.font_changed.connect(self.change_font)
        self.sidebar.logout_requested.connect(self._on_logout_requested)
        if self.auth and self.auth.current_user():
            self.sidebar.set_user_email(self.auth.current_user().email)
        self._unread_problems = 0
        self._tray = QSystemTrayIcon(QApplication.windowIcon(), self)
        self._tray.setToolTip(APP_NAME)
        bridge.engineState.connect(lambda *_: self.refresh_sidebar())

        # Події рушія сиплються пачками — оновлюємо таблиці не частіше ніж раз на 300 мс
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self.refresh_all)

        # Статистика й трафік — раз на 5 с навіть без подій
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(5000)
        self._stats_timer.timeout.connect(self.refresh_sidebar)
        self._stats_timer.start()
        self.refresh_sidebar()

    def _on_log(self, entry) -> None:
        self.logs.append(entry)
        if entry.level in ("WARN", "ERROR") and self.pages.currentWidget() is not self.logs:
            self._unread_problems += 1
            self.sidebar.set_log_badge(self._unread_problems)
        title, text, action = explain(entry.level, entry.message)
        if action:
            self.monitoring.show_attention(account_id_of(entry.source), text)
            self._notify("Потрібна ваша дія", f"{title}. {text}")

    def _notify(self, title: str, message: str) -> None:
        """Системне сповіщення (macOS Notification Center); якщо недоступне — мовчки пропускаємо."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 15000)
        QApplication.alert(self, 0)

    def toggle_theme(self) -> None:
        mode = "light" if theme.MODE == "dark" else "dark"
        QSettings(APP_NAME, APP_NAME).setValue("theme", mode)
        self._restyle(mode, theme.FONT_PX)

    def change_font(self, delta: int) -> None:
        px = max(12, min(20, theme.FONT_PX + delta))
        QSettings(APP_NAME, APP_NAME).setValue("font_px", px)
        self._restyle(theme.MODE, px)

    def _restyle(self, mode: str, px: int) -> None:
        theme.set_mode(mode)
        theme.set_font_px(px)
        app = QApplication.instance()
        app.setPalette(theme.build_palette())
        app.setStyleSheet(theme.build_qss())
        self.refresh_all()
        self.logs.refresh()

    def _on_page_changed(self, index: int) -> None:
        if self.pages.widget(index) is self.logs:
            self._unread_problems = 0
            self.sidebar.set_log_badge(0)
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def _on_task_changed(self, task_id: int, status: str, log: str) -> None:
        self.monitoring.on_task_changed(task_id, status, log)
        self._schedule_refresh()

    def _on_data_changed(self, kind: str) -> None:
        if kind == "orders":
            self.cabinet.set_syncing(False)
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def refresh_all(self) -> None:
        for page in (self.cabinet, self.accounts, self.monitoring, self.proxies):
            page.refresh()
        self.refresh_sidebar()

    def refresh_sidebar(self) -> None:
        accounts = self.db.accounts()
        system = SystemStatus(
            api_ok=self.engine.ok,
            accounts_active=sum(1 for a in accounts if a.status == AccountStatus.ACTIVE.value),
            accounts_total=len(accounts),
            traffic_mb=self.db.traffic_bytes() / 1024 / 1024,
        )
        self.sidebar.update_status(system, self.db.daily_stats(), self.engine.state_message)

    def show_order(self, order: Order) -> None:
        if self.details.isVisible() and self.details.current_order is order:
            return
        self.details.show_order(order)
        if not self.details.isVisible():
            self.details.show()
            self.splitter.setSizes([max(0, self.splitter.width() - DETAILS_WIDTH), DETAILS_WIDTH])

    def hide_details(self) -> None:
        self.details.hide()
        self.cabinet.table.clearSelection()

    def _on_logout_requested(self) -> None:
        email = self.auth.current_user().email if self.auth and self.auth.current_user() else ""
        text = f"Ви дійсно бажаєте вийти з облікового запису{' (' + email + ')' if email else ''}?"
        reply = QMessageBox.question(
            self,
            "Вихід з акаунта",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self.auth:
                self.auth.logout()
            self.relogin_requested = True
            self.close()

    def closeEvent(self, event):
        self.statusBar().showMessage("Зупиняємо завдання і браузер…")
        self._stats_timer.stop()
        self.engine.shutdown()
        super().closeEvent(event)
