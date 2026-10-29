"""Тест нових сторінок «Моніторинг» і «Логи» без вікна (Qt offscreen).

Запуск:  QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_ui_pages.py
Перевіряє: швидке додавання завдання, фільтри-«чіпи» та їхні лічильники, пошук, смугу «Потрібна дія»,
пояснення в логах, фільтри логів і кнопку «Для підтримки».
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk-uitest-")
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dashboard import theme  # noqa: E402
from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.entities import TaskStatus  # noqa: E402
from dashboard.humanize import explain  # noqa: E402
from dashboard.pages.logs import LogsPage  # noqa: E402
from dashboard.pages.monitoring import ATTENTION, MonitoringPage  # noqa: E402

FAILED: list[str] = []


def check(name: str, condition: bool) -> None:
    print(f"   {'✓' if condition else '✗'} {name}")
    if not condition:
        FAILED.append(name)


class FakeEngine:
    """Мінімум API рушія, потрібний сторінкам."""

    def __init__(self):
        self.running: set[int] = set()
        self.started: list[int] = []

    def running_task_ids(self):
        return set(self.running)

    def start_tasks(self, ids):
        self.started += list(ids)
        self.running |= set(ids)

    def stop_tasks(self, ids):
        self.running -= set(ids)


def visible_rows(page: MonitoringPage) -> int:
    t = page.table
    return sum(1 for r in range(t.rowCount()) if not t.isRowHidden(r))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.build_qss())
    paths.ensure_dirs()
    db = Database(paths.db_path())
    engine = FakeEngine()
    acc = db.add_account("tester@example.com", None)

    print("\n== Моніторинг")
    page = MonitoringPage(db, engine)
    check("без завдань показано заглушку", page.table.rowCount() == 0)

    page.quick_url.setText("https://shop.example.com/p-1.html")
    page.quick_add()
    check("швидке додавання створило завдання", len(db.tasks()) == 1)
    check("поле посилання очищено", page.quick_url.text() == "")
    check("швидке додавання НЕ запускає завдання саме", engine.started == [])

    t1 = db.tasks()[0].id
    t2 = db.add_task(acc, "https://shop.example.com/p-2.html", "monitor", 30)
    t3 = db.add_task(acc, "https://shop.example.com/p-3.html", "cart", 30)
    db.update_task(t1, TaskStatus.DONE, "Додано в кошик")
    db.update_task(t3, TaskStatus.ERROR, "Помилка")
    engine.running = {t2}
    db.update_task(t2, TaskStatus.MONITORING, "Спроба #1")
    page.refresh()

    check("у таблиці 3 рядки", visible_rows(page) == 3)
    check("чіп «Готово» має лічильник 1", page.chips["Готово"].text().endswith("1"))
    check("чіп «Помилки» має лічильник 1", page.chips["Помилки"].text().endswith("1"))
    check("чіп «Працюють» має лічильник 1", page.chips["Працюють"].text().endswith("1"))

    page.chips["Помилки"].click()
    check("фільтр «Помилки» лишає 1 рядок", visible_rows(page) == 1)
    page.chips["Усі"].click()
    check("фільтр «Усі» повертає 3 рядки", visible_rows(page) == 3)

    page.search.setText("p-2")
    check("пошук знаходить лише p-2", visible_rows(page) == 1)
    page.search.setText("")
    check("порожній пошук показує все", visible_rows(page) == 3)

    # кнопки в рядках: ▶ для зупинених, ■ для запущеного
    from dashboard.pages.monitoring import COL_ACTIONS
    widgets = [page.table.cellWidget(r, COL_ACTIONS) for r in range(page.table.rowCount())]
    check("у кожного рядка є кнопка дії", all(w is not None for w in widgets))

    print("\n== «Потрібна дія»")
    check("смуга спершу схована", page.banner.isHidden())
    page.show_attention(acc, "Пройдіть перевірку у вікні браузера")
    check("смуга з'явилась для запущеного завдання", not page.banner.isHidden() and t2 in page.attention)
    check("чіп «Потрібна дія» має лічильник 1", page.chips[ATTENTION].text().endswith("1"))
    page.clear_attention()
    check("смуга зникає після «Я пройшов перевірку»", page.banner.isHidden() and not page.attention)

    print("\n== Логи")
    explain_title, _, action = explain("WARN", "Потрібно пройти перевірку у вікні браузера. Після проходження…")
    check("перевірка безпеки → потрібна дія", action and "перевірк" in explain_title.lower())
    _, _, action = explain("INFO", "Додано в кошик: товар (підтверджено сайтом)")
    check("успіх не вимагає дії", not action)
    title, _, _ = explain("ERROR", "щось зовсім невідоме")
    check("невідома помилка отримує загальний заголовок", title == "Сталася помилка")

    db.add_log("INFO", f"account#{acc}", "Додано в кошик: монета (підтверджено сайтом)")
    db.add_log("WARN", f"account#{acc}", "Потрібно пройти перевірку у вікні браузера.")
    db.add_log("ERROR", "task#3", "кошик не оновився після кліку")
    logs = LogsPage(db)
    check("завантажено 3 записи", len(logs._entries) == 3)
    text_all = logs.view.toPlainText()
    check("логи містять зрозумілий заголовок", "Товар додано в кошик" in text_all)
    logs.level_buttons["Лише помилки"].click()
    only_err = logs.view.toPlainText()
    check("фільтр «Лише помилки» ховає успіх", "Товар додано в кошик" not in only_err and "кошик" in only_err)
    logs.level_buttons["Усе"].click()
    logs.search.setText("перевірк")
    check("пошук у логах працює", "Товар додано в кошик" not in logs.view.toPlainText())
    logs.search.setText("")
    logs.tech.setChecked(False)
    check("можна сховати технічні деталі", "account#" not in logs.view.toPlainText())
    logs.tech.setChecked(True)
    logs.copy_for_support()
    check("«Для підтримки» кладе текст у буфер", "кошик" in app.clipboard().text())

    print("\n== Тема")
    theme.set_mode("light")
    check("світла тема змінює колір фону", theme.BG == theme.LIGHT["BG"])
    qss_light = theme.build_qss()
    theme.set_mode("dark")
    check("темна тема повертається", theme.BG == theme.DARK["BG"] and theme.build_qss() != qss_light)
    theme.set_font_px(16)
    check("розмір шрифту потрапляє в стилі", "16px" in theme.build_qss())
    theme.set_font_px(14)

    print("\nУСЕ ПРОЙДЕНО" if not FAILED else f"\nПРОВАЛИ: {FAILED}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
