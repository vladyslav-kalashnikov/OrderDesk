"""Колбеки рушія (його потік) → Qt-сигнали (доставляються в UI-потік черговими з'єднаннями)."""
from PySide6.QtCore import QObject, Signal

from .core.engine import EngineEvents


class EngineBridge(QObject, EngineEvents):
    taskChanged = Signal(int, str, str)
    logAdded = Signal(object)
    dataChanged = Signal(str)
    engineState = Signal(bool, str)

    def task_changed(self, task_id: int, status: str, log: str) -> None:
        self.taskChanged.emit(task_id, status, log)

    def log_added(self, entry) -> None:
        self.logAdded.emit(entry)

    def data_changed(self, kind: str) -> None:
        self.dataChanged.emit(kind)

    def engine_state(self, ok: bool, message: str) -> None:
        self.engineState.emit(ok, message)
