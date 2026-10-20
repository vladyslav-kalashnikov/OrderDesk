"""«Моніторинг»: завдання (товар × акаунт), Старт/Стоп, статус і лог кожного."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import theme

from ..core.db import Database
from ..core.engine import Engine
from ..core.entities import ACTIVE_TASK_STATUSES, TaskMode
from .common import DataTable, Page, TableOrEmpty, button, confirm, info, state_cell

COL_ID, COL_ACCOUNT, COL_PRODUCT, COL_MODE, COL_INTERVAL, COL_STATUS, COL_LOG, COL_ACTIONS = range(8)
ATTENTION = "Потрібна дія"
FILTERS = ("Усі", "Працюють", ATTENTION, "Готово", "Помилки")


class TaskDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Нові завдання")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.account = QComboBox()
        for account in db.accounts():
            self.account.addItem(account.email, account.id)

        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://shop.example.com/product/123\nПо одному посиланню на рядок — на кожне буде своє завдання")
        self.urls.setMinimumHeight(110)

        self.mode = QComboBox()
        for mode in TaskMode:
            self.mode.addItem(mode.label, mode.value)
        self.mode.setCurrentIndex(1)

        self.interval = QSpinBox()
        self.interval.setRange(5, 3600)
        self.interval.setValue(30)
        self.interval.setSuffix(" с")

        form.addRow("Акаунт", self.account)
        form.addRow("Товари", self.urls)
        form.addRow("Режим", self.mode)
        form.addRow("Інтервал перевірки", self.interval)
        layout.addLayout(form)
        hint = QLabel("Не ставте інтервал надто малим: часті запити до чужого сайту можуть закінчитись блокуванням.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def url_list(self) -> list[str]:
        return [u.strip() for u in self.urls.toPlainText().splitlines() if u.strip()]

    def _accept(self) -> None:
        urls = self.url_list()
        bad = [u for u in urls if not u.startswith(("http://", "https://"))]
        if not urls or bad:
            info(self, "Перевірте посилання", "Потрібне хоча б одне посилання, і всі мають починатися з http:// або https://"
                 + (f"\n\nНевірні: {', '.join(bad[:3])}" if bad else ""))
            return
        self.accept()


class MonitoringPage(Page):
    def __init__(self, db: Database, engine: Engine, parent=None):
        super().__init__("Моніторинг", "Завдання перевіряють наявність товару і за потреби додають його в кошик", parent)
        self.db, self.engine = db, engine
        self.attention: set[int] = set()
        self.filter_name = "Усі"

        self.add_btn = button("+ Завдання", "primary", "Додати кілька посилань одразу")
        self.start_btn = button("▶ Старт", tooltip="Запустити виділені (або всі, якщо нічого не виділено)")
        self.stop_btn = button("■ Стоп", tooltip="Зупинити виділені (або всі запущені)")
        self.delete_btn = button("Видалити", "danger")
        for b in (self.add_btn, self.start_btn, self.stop_btn, self.delete_btn):
            self.actions.addWidget(b)
        self.add_btn.clicked.connect(self.add_tasks)
        self.start_btn.clicked.connect(self.start_selected)
        self.stop_btn.clicked.connect(self.stop_selected)
        self.delete_btn.clicked.connect(self.delete_selected)

        self._build_banner()
        self._build_quick_add()
        self._build_filters()

        self.table = DataTable(
            ["ID", "АКАУНТ", "ТОВАР", "РЕЖИМ", "ІНТЕРВАЛ", "СТАТУС", "ОСТАННЯ ПОДІЯ", ""],
            stretch=(COL_LOG,),
            widths={
                COL_ID: 44,
                COL_ACCOUNT: 155,
                COL_PRODUCT: 230,
                COL_MODE: 120,
                COL_INTERVAL: 65,
                COL_STATUS: 90,
                COL_ACTIONS: 58,
            },
        )
        self.stack = TableOrEmpty(self.table, "Завдань ще немає",
                                  "Вставте посилання на товар у рядку зверху і натисніть Enter")
        self.body.addWidget(self.stack)
        self._attention_timer = QTimer(self)
        self._attention_timer.setSingleShot(True)
        self._attention_timer.timeout.connect(self.clear_attention)
        self.refresh()

    # --- верхні блоки ---

    def _build_banner(self) -> None:
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(14, 10, 14, 10)
        icon = QLabel("🛡")
        icon.setStyleSheet("font-size: 22px;")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.banner_title = QLabel("Потрібна ваша дія")
        self.banner_title.setObjectName("bannerTitle")
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        texts.addWidget(self.banner_title)
        texts.addWidget(self.banner_text)
        done = button("Я пройшов перевірку")
        done.clicked.connect(self.clear_attention)
        row.addWidget(icon)
        row.addLayout(texts, 1)
        row.addWidget(done)
        self.banner.hide()
        self.body.addWidget(self.banner)

    def _build_quick_add(self) -> None:
        row = QHBoxLayout()
        self.quick_url = QLineEdit()
        self.quick_url.setObjectName("quickUrl")
        self.quick_url.setPlaceholderText("Вставте посилання на товар і натисніть Enter — завдання створиться одразу")
        self.quick_url.returnPressed.connect(self.quick_add)
        self.quick_account = QComboBox()
        self.quick_account.setMaximumWidth(180)
        self.quick_mode = QComboBox()
        self.quick_mode.setMaximumWidth(160)
        for mode in TaskMode:
            self.quick_mode.addItem(mode.label, mode.value)
        self.quick_mode.setCurrentIndex(1)
        add = button("Додати", "primary")
        add.clicked.connect(self.quick_add)
        row.addWidget(self.quick_url, 1)
        row.addWidget(self.quick_account)
        row.addWidget(self.quick_mode)
        row.addWidget(add)
        self.body.addLayout(row)

    def _build_filters(self) -> None:
        row = QHBoxLayout()
        self.chips: dict[str, QPushButton] = {}
        for name in FILTERS:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(lambda _=False, n=name: self._set_filter(n))
            self.chips[name] = b
            row.addWidget(b)
        self.chips["Усі"].setChecked(True)
        row.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Пошук за товаром або акаунтом…")
        self.search.setMaximumWidth(280)
        self.search.textChanged.connect(lambda t: self.table.apply_filter(t))
        row.addWidget(self.search)
        self.body.addLayout(row)

    def _set_filter(self, name: str) -> None:
        self.filter_name = name
        self.refresh()

    # --- дані ---

    def _category(self, t) -> set[str]:
        cats = {"Усі"}
        if t.id in self.attention:
            cats.add(ATTENTION)
        if t.status in {s.value for s in ACTIVE_TASK_STATUSES} or t.id in self.engine.running_task_ids():
            cats.add("Працюють")
        if t.status == "Готово":
            cats.add("Готово")
        if t.status == "Помилка":
            cats.add("Помилки")
        return cats

    def _status_cell(self, t) -> tuple[str, str]:
        if t.id in self.attention:
            return ATTENTION, theme.AMBER
        return state_cell(t.status)

    def refresh(self) -> None:
        accounts = self.db.accounts()
        emails = {a.id: a.email for a in accounts}
        current = self.quick_account.currentData()
        self.quick_account.blockSignals(True)
        self.quick_account.clear()
        for a in accounts:
            self.quick_account.addItem(a.email, a.id)
        self.quick_account.setCurrentIndex(max(0, self.quick_account.findData(current)))
        self.quick_account.blockSignals(False)

        tasks = self.db.tasks()
        counts = {name: 0 for name in FILTERS}
        for t in tasks:
            for c in self._category(t):
                counts[c] += 1
        for name, b in self.chips.items():
            b.setText(f"{name} · {counts[name]}")

        running = self.engine.running_task_ids()
        shown = [t for t in tasks if self.filter_name in self._category(t)]
        rows = [(t.id, [
            str(t.id), emails.get(t.account_id, "—"), t.title or t.url, t.mode_enum.label,
            f"{t.interval_sec} с", self._status_cell(t), t.last_log or "", "",
        ]) for t in shown]
        self.table.set_rows(rows)
        for r, t in enumerate(shown):
            self.table.setCellWidget(r, COL_ACTIONS, self._row_buttons(t.id, t.id in running))
        self.table.apply_filter(self.search.text())
        self.stack.update_state()
        self.subtitle.setText(f"Завдань: {len(tasks)} · запущено: {len(running)}")

    def _row_buttons(self, task_id: int, running: bool) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        if running:
            b = button("■", "rowStop", "Зупинити це завдання")
            b.clicked.connect(lambda _=False, i=task_id: self.engine.stop_tasks([i]))
        else:
            b = button("▶", "rowStart", "Запустити це завдання")
            b.clicked.connect(lambda _=False, i=task_id: self.engine.start_tasks([i]))
        lay.addWidget(b)
        return box

    def on_task_changed(self, task_id: int, status: str, log: str) -> None:
        if task_id in self.attention and status in {"Готово", "Зупинено", "Помилка"}:
            self.attention.discard(task_id)
            if not self.attention:
                self.banner.hide()
        text, color = self._status_cell(self.db.task(task_id)) if self.db.task(task_id) else state_cell(status)
        self.table.set_cell(task_id, COL_STATUS, text, color)
        self.table.set_cell(task_id, COL_LOG, log)

    # --- «потрібна дія» ---

    def show_attention(self, account_id: int | None, message: str) -> None:
        ids = {t.id for t in self.db.tasks()
               if (account_id is None or t.account_id == account_id) and t.id in self.engine.running_task_ids()}
        self.attention |= ids
        self.banner_text.setText(message or "Сайт просить пройти перевірку. Пройдіть її у вікні браузера.")
        self.banner_title.setText(f"Потрібна ваша дія — завдання {', '.join(f'№{i}' for i in sorted(self.attention))}"
                                  if self.attention else "Потрібна ваша дія")
        self.banner.show()
        self._attention_timer.start(5 * 60 * 1000)
        self.refresh()

    def clear_attention(self) -> None:
        self.attention.clear()
        self.banner.hide()
        self.refresh()

    # --- додавання ---

    def quick_add(self) -> None:
        url = self.quick_url.text().strip()
        if self.quick_account.currentData() is None:
            info(self, "Немає акаунтів", "Спочатку додайте акаунт у розділі «Акаунти».")
            return
        if not url.startswith(("http://", "https://")):
            info(self, "Перевірте посилання", "Посилання має починатися з http:// або https://")
            return
        task_id = self.db.add_task(self.quick_account.currentData(), url, self.quick_mode.currentData(), 30)
        self.quick_url.clear()
        self.refresh()
        self.subtitle.setText(f"Завдання №{task_id} додано — натисніть ▶ у рядку, щоб запустити")

    def add_tasks(self) -> None:
        if not self.db.accounts():
            info(self, "Немає акаунтів", "Спочатку додайте акаунт у розділі «Акаунти».")
            return
        dialog = TaskDialog(self.db, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for url in dialog.url_list():
            self.db.add_task(dialog.account.currentData(), url, dialog.mode.currentData(), dialog.interval.value())
        self.refresh()

    def start_selected(self) -> None:
        ids = self.table.selected_ids() or self.table.all_ids()
        running = self.engine.running_task_ids()
        to_start = [i for i in ids if i not in running]
        if to_start:
            self.engine.start_tasks(to_start)

    def stop_selected(self) -> None:
        running = self.engine.running_task_ids()
        ids = [i for i in (self.table.selected_ids() or list(running)) if i in running]
        if ids:
            self.engine.stop_tasks(ids)
            for i in ids:
                self.table.set_cell(i, COL_LOG, "Зупиняється…")

    def delete_selected(self) -> None:
        ids = self.table.selected_ids()
        if not ids or not confirm(self, "Видалити завдання", f"Видалити завдань: {len(ids)}?"):
            return
        running = self.engine.running_task_ids()
        if active := [i for i in ids if i in running or self.db.task(i).status in {s.value for s in ACTIVE_TASK_STATUSES}]:
            info(self, "Завдання запущені", f"Спочатку зупиніть: {', '.join(map(str, active))}")
            return
        for i in ids:
            self.db.delete_task(i)
        self.refresh()
