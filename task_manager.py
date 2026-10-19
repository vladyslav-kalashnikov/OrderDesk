"""Базовий інтерфейс менеджера завдань: таблиця (ID, Статус, Лог) + кнопки Старт/Стоп.

Працює і з PySide6, і з PyQt6 — береться та бібліотека, яка встановлена.
Запуск:  pip install PySide6   (або PyQt6)
         python task_manager.py
"""
import random
import sys

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication, QHBoxLayout, QHeaderView, QMainWindow, QPushButton,
        QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError:
    from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal as Signal
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (
        QApplication, QHBoxLayout, QHeaderView, QMainWindow, QPushButton,
        QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )

COL_ID, COL_STATUS, COL_LOG = range(3)

STATUS_COLORS = {
    "Очікує": "#9e9e9e",
    "Виконується": "#1e88e5",
    "Готово": "#43a047",
    "Зупинено": "#fb8c00",
    "Помилка": "#e53935",
}


class TaskWorker(QObject):
    """Робота одного завдання. Виконується в окремому QThread, щоб не блокувати UI."""

    status_changed = Signal(int, str)  # task_id, статус
    log_message = Signal(int, str)     # task_id, рядок логу
    finished = Signal(int)             # task_id

    def __init__(self, task_id: int):
        super().__init__()
        self.task_id = task_id
        self._stop_requested = False

    def stop(self):
        # Прапорець читається в циклі run(); простий bool достатній для GIL-середовища
        self._stop_requested = True

    def run(self):
        self.status_changed.emit(self.task_id, "Виконується")
        try:
            steps = random.randint(5, 10)
            for step in range(1, steps + 1):
                if self._stop_requested:
                    self.status_changed.emit(self.task_id, "Зупинено")
                    self.log_message.emit(self.task_id, f"Зупинено на кроці {step}/{steps}")
                    return
                # Тут має бути справжня робота; поки що — імітація
                QThread.msleep(random.randint(300, 900))
                self.log_message.emit(self.task_id, f"Крок {step}/{steps} виконано")
            self.status_changed.emit(self.task_id, "Готово")
            self.log_message.emit(self.task_id, "Завершено успішно")
        except Exception as exc:  # noqa: BLE001 — показуємо будь-яку помилку в таблиці
            self.status_changed.emit(self.task_id, "Помилка")
            self.log_message.emit(self.task_id, str(exc))
        finally:
            self.finished.emit(self.task_id)


class MainWindow(QMainWindow):
    def __init__(self, task_count: int = 10):
        super().__init__()
        self.setWindowTitle("Менеджер завдань")
        self.resize(720, 420)

        # task_id -> (QThread, TaskWorker) для запущених завдань
        self._running: dict[int, tuple[QThread, TaskWorker]] = {}
        self._rows: dict[int, int] = {}  # task_id -> номер рядка

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Статус", "Лог"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_LOG, QHeaderView.ResizeMode.Stretch)

        self.btn_start = QPushButton("▶ Старт")
        self.btn_stop = QPushButton("■ Стоп")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_tasks)
        self.btn_stop.clicked.connect(self.stop_tasks)

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_stop)
        buttons.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        for task_id in range(1, task_count + 1):
            self.add_task(task_id)

        self.statusBar().showMessage("Готовий до запуску")

    # --- Таблиця ---

    def add_task(self, task_id: int):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[task_id] = row

        id_item = QTableWidgetItem(str(task_id))
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_ID, id_item)
        self.table.setItem(row, COL_STATUS, QTableWidgetItem())
        self.table.setItem(row, COL_LOG, QTableWidgetItem(""))
        self.set_status(task_id, "Очікує")

    def set_status(self, task_id: int, status: str):
        item = self.table.item(self._rows[task_id], COL_STATUS)
        item.setText(status)
        item.setForeground(QColor(STATUS_COLORS.get(status, "#000000")))

    def set_log(self, task_id: int, text: str):
        item = self.table.item(self._rows[task_id], COL_LOG)
        item.setText(text)
        item.setToolTip(text)

    # --- Керування ---

    def selected_task_ids(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        return [tid for tid, row in self._rows.items() if row in rows]

    def start_tasks(self):
        # Якщо щось виділено — запускаємо лише виділене, інакше всі
        task_ids = self.selected_task_ids() or list(self._rows)
        for task_id in task_ids:
            if task_id in self._running:
                continue
            thread = QThread(self)
            worker = TaskWorker(task_id)
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.status_changed.connect(self.set_status)
            worker.log_message.connect(self.set_log)
            worker.finished.connect(self.on_task_finished)
            worker.finished.connect(thread.quit)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)

            self._running[task_id] = (thread, worker)
            self.set_log(task_id, "Запуск…")
            thread.start()
        self.update_controls()

    def stop_tasks(self):
        task_ids = self.selected_task_ids() or list(self._running)
        for task_id in task_ids:
            if task_id in self._running:
                self._running[task_id][1].stop()
                self.set_log(task_id, "Зупиняється…")

    def on_task_finished(self, task_id: int):
        self._running.pop(task_id, None)
        self.update_controls()

    def update_controls(self):
        running = len(self._running)
        self.btn_stop.setEnabled(running > 0)
        self.statusBar().showMessage(f"Виконується: {running}" if running else "Усі завдання завершено")

    def closeEvent(self, event):
        # Коректно гасимо потоки, інакше Qt впаде з "QThread: Destroyed while thread is still running"
        for thread, worker in list(self._running.values()):
            worker.stop()
            thread.quit()
            thread.wait(3000)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
