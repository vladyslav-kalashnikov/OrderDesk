"""«Проксі»: імпорт списком, перевірка (IP і затримка), видалення."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)

from ..core.db import Database
from ..core.engine import Engine
from ..core.proxy import SCHEMES, ParsedProxy, parse_proxy_line
from ..core.secrets import delete_secret, set_secret
from .common import DataTable, Page, TableOrEmpty, button, confirm, info, state_cell


class ProxyImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Додати проксі")
        self.setMinimumWidth(560)
        self.parsed: list[ParsedProxy] = []
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.lines = QPlainTextEdit()
        self.lines.setMinimumHeight(140)
        self.lines.setPlaceholderText(
            "http://user:pass@1.2.3.4:8080\nsocks5://user:pass@5.6.7.8:1080\n1.2.3.4:8080\n1.2.3.4:8080:user:pass")
        self.default_scheme = QComboBox()
        self.default_scheme.addItems(SCHEMES)
        self.label = QLineEdit()
        self.label.setPlaceholderText("необов'язково, напр. «UA мобільні» — додасться номер")

        form.addRow("Проксі (по рядку)", self.lines)
        form.addRow("Схема для рядків без неї", self.default_scheme)
        form.addRow("Назва", self.label)
        layout.addLayout(form)
        hint = QLabel("Паролі проксі зберігаються в Keychain. SOCKS5 з паролем працює через локальний форвардер.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        parsed, errors = [], []
        for n, line in enumerate(self.lines.toPlainText().splitlines(), 1):
            if not line.strip():
                continue
            try:
                parsed.append(parse_proxy_line(line, self.default_scheme.currentText()))
            except ValueError as exc:
                errors.append(f"рядок {n}: {exc}")
        if errors:
            info(self, "Не вдалося розібрати", "\n".join(errors[:10]))
            return
        if not parsed:
            info(self, "Порожньо", "Вставте хоча б один проксі.")
            return
        self.parsed = parsed
        self.accept()


class ProxiesPage(Page):
    def __init__(self, db: Database, engine: Engine, parent=None):
        super().__init__("Проксі", "Проксі прив'язуються до акаунтів; кожен акаунт ходить через свій", parent)
        self.db, self.engine = db, engine

        add = button("+ Проксі", "primary")
        check = button("Перевірити", tooltip="Перевірити виділені (або всі): IP і затримка")
        delete = button("Видалити", "danger")
        for b in (add, check, delete):
            self.actions.addWidget(b)
        add.clicked.connect(self.add_proxies)
        check.clicked.connect(self.check_selected)
        delete.clicked.connect(self.delete_selected)

        self.table = DataTable(
            ["ID", "НАЗВА", "АДРЕСА", "ЛОГІН", "IP", "ЗАТРИМКА", "СТАТУС", "ПЕРЕВІРЕНО"],
            stretch=(1,),
            widths={
                0: 44,
                2: 160,
                3: 110,
                4: 120,
                5: 75,
                6: 85,
                7: 130,
            },
        )
        self.stack = TableOrEmpty(self.table, "Проксі ще немає", "Без проксі акаунти ходять напряму з вашої IP-адреси")
        self.body.addWidget(self.stack)
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for p in self.db.proxies():
            rows.append((p.id, [
                str(p.id), p.label or "—", p.address, p.username or "—", p.last_ip or "—",
                f"{p.latency_ms} мс" if p.latency_ms is not None else "—", state_cell(p.status), p.checked_at or "—",
            ]))
        self.table.set_rows(rows)
        self.stack.update_state()
        working = sum(1 for p in self.db.proxies() if p.status == "Працює")
        self.subtitle.setText(f"Проксі: {len(rows)} · працюють: {working}")

    def add_proxies(self) -> None:
        dialog = ProxyImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        label = dialog.label.text().strip()
        for n, p in enumerate(dialog.parsed, 1):
            name = (f"{label} #{n}" if len(dialog.parsed) > 1 else label) if label else None
            proxy_id = self.db.add_proxy(p.scheme, p.host, p.port, p.username, name)
            if p.username:
                set_secret("proxy", proxy_id, p.password or "")
        self.refresh()

    def check_selected(self) -> None:
        ids = self.table.selected_ids() or self.table.all_ids()
        for proxy_id in ids:
            self.table.set_cell(proxy_id, 6, "Перевірка…")
            self.engine.check_proxy(proxy_id)

    def delete_selected(self) -> None:
        ids = self.table.selected_ids()
        if not ids:
            return
        used = {a.proxy_id for a in self.db.accounts()} & set(ids)
        warning = f"\n\nПроксі {', '.join(map(str, sorted(used)))} прив'язані до акаунтів — вони перейдуть на «без проксі»." if used else ""
        if not confirm(self, "Видалити проксі", f"Видалити проксі: {len(ids)}?{warning}"):
            return
        for proxy_id in ids:
            delete_secret("proxy", proxy_id)
            self.db.delete_proxy(proxy_id)
        self.refresh()
