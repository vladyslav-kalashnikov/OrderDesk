"""«Логи»: живий журнал зі зрозумілими поясненнями, фільтрами й кнопкою «Для підтримки»."""
import html
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPushButton, QTextBrowser

from .. import theme
from ..core import paths
from ..core.db import Database
from ..core.entities import LogEntry
from ..humanize import account_id_of, explain, icon
from .common import Page, button, confirm

LEVEL_COLOR = lambda lvl: {"INFO": theme.MUTED, "WARN": theme.AMBER, "ERROR": theme.RED}.get(lvl, theme.MUTED)  # noqa: E731
LEVEL_FILTERS = {"Усе": None, "Попередження й помилки": {"WARN", "ERROR"}, "Лише помилки": {"ERROR"}}


class LogsPage(Page):
    def __init__(self, db: Database, parent=None):
        super().__init__("Логи", "Подія зрозумілою мовою; технічні деталі — під нею", parent)
        self.db = db
        self._entries: list[LogEntry] = []

        copy_btn = button("Для підтримки", tooltip="Скопіювати видимі записи в буфер обміну")
        copy_btn.clicked.connect(self.copy_for_support)
        shot_btn = button("Знімок екрана", tooltip="Відкрити останній збережений знімок помилки")
        shot_btn.clicked.connect(self.open_last_screenshot)
        clear = button("Очистити", "danger")
        clear.clicked.connect(self.clear)
        for b in (copy_btn, shot_btn, clear):
            self.actions.addWidget(b)

        bar = QHBoxLayout()
        self.level_buttons: dict[str, QPushButton] = {}
        for name in LEVEL_FILTERS:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(self.render)
            self.level_buttons[name] = b
            bar.addWidget(b)
        self.level_buttons["Усе"].setChecked(True)

        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self.render)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Пошук у логах…")
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self.render)
        self.tech = QCheckBox("Технічні деталі")
        self.tech.setChecked(True)
        self.tech.toggled.connect(self.render)
        bar.addSpacing(8)
        bar.addWidget(self.account)
        bar.addWidget(self.search)
        bar.addStretch()
        bar.addWidget(self.tech)
        self.body.addLayout(bar)

        self.view = QTextBrowser()
        self.view.setObjectName("logView")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        self.body.addWidget(self.view)
        self.refresh()

    # --- дані ---

    def _fill_accounts(self) -> None:
        current = self.account.currentData()
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("Усі акаунти", None)
        for a in self.db.accounts():
            self.account.addItem(a.email, a.id)
        idx = self.account.findData(current)
        self.account.setCurrentIndex(max(0, idx))
        self.account.blockSignals(False)

    def refresh(self) -> None:
        self._fill_accounts()
        self._entries = self.db.recent_logs(1000)
        self.render()

    def append(self, entry: LogEntry) -> None:
        self._entries.append(entry)
        del self._entries[:-1000]
        if self._allowed(entry):
            bar = self.view.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 4
            self.view.append(self._format(entry))
            if at_bottom:
                bar.setValue(bar.maximum())

    # --- відображення ---

    def _levels(self):
        for name, b in self.level_buttons.items():
            if b.isChecked():
                return LEVEL_FILTERS[name]
        return None

    def _allowed(self, entry: LogEntry) -> bool:
        levels = self._levels()
        if levels is not None and entry.level not in levels:
            return False
        acc = self.account.currentData()
        if acc is not None and account_id_of(entry.source) not in (None, acc):
            return False
        needle = self.search.text().strip().casefold()
        return not needle or needle in f"{entry.message} {entry.source or ''}".casefold()

    def _format(self, entry: LogEntry) -> str:
        color = LEVEL_COLOR(entry.level)
        title, text, action = explain(entry.level, entry.message)
        parts = [f'<span style="color:{theme.MUTED_2}">{html.escape(entry.ts[-8:])}</span>&nbsp;&nbsp;'
                 f'<span style="color:{color}; font-size:15px">{icon(entry.level)}</span>&nbsp;'
                 f'<b style="color:{theme.TEXT}">{html.escape(title)}</b>']
        if action:
            parts.append(f'&nbsp;<span style="color:{theme.AMBER}">● потрібна ваша дія</span>')
        if text:
            parts.append(f'<br><span style="color:{theme.MUTED}">{html.escape(text)}</span>')
        if self.tech.isChecked():
            src = html.escape(entry.source or "")
            parts.append(f'<br><span style="color:{theme.MUTED_2}; font-family:Menlo,monospace; font-size:11px">'
                         f'{src} · {html.escape(entry.message)}</span>')
        return "".join(parts) + '<div style="margin:6px"></div>'

    def render(self) -> None:
        self.view.clear()
        self.view.setHtml("<br>".join(self._format(e) for e in self._entries if self._allowed(e))
                          or f'<p style="color:{theme.MUTED}">Записів немає</p>')
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # --- дії ---

    def copy_for_support(self) -> None:
        lines = [f"{e.ts} {e.level:<5} {e.source or ''}: {e.message}" for e in self._entries if self._allowed(e)]
        text = "\n".join(lines[-200:])
        err_file = paths.data_dir() / "error.log"
        if err_file.exists():
            try:
                err_text = err_file.read_text(encoding="utf-8")
                err_tail = "\n".join(err_text.strip().splitlines()[-100:])
                if err_tail:
                    text += f"\n\n--- Останній детальний traceback (error.log) ---\n{err_tail}"
            except Exception:
                pass
        QGuiApplication.clipboard().setText(text)
        self.subtitle.setText(f"Скопійовано записів: {min(len(lines), 200)} — вставте їх у повідомлення розробнику")

    def open_last_screenshot(self) -> None:
        shots = sorted((paths.data_dir() / "debug").glob("*.png"), key=lambda p: p.stat().st_mtime) \
            if (paths.data_dir() / "debug").exists() else []
        if not shots:
            self.subtitle.setText("Знімків екрана ще немає — вони з'являються після помилки додавання в кошик")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(shots[-1]))))

    def clear(self) -> None:
        if confirm(self, "Очистити логи", "Видалити всі записи журналу? Відновити їх не можна."):
            self.db.clear_logs()
            self._entries.clear()
            self.view.clear()
