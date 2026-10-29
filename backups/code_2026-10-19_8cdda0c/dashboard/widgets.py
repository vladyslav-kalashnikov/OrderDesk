"""Перевикористовувані віджети: бейдж статусу, картки, кільце прогресу, таймлайн."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme
from .models import HistoryEvent, OrderStatus


def mono_font() -> QFont:
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


def clear_layout(layout) -> None:
    while (item := layout.takeAt(0)) is not None:
        if (widget := item.widget()) is not None:
            # Відв'язуємо одразу: deleteLater спрацює лише в наступному циклі подій,
            # і до того старий віджет малювався б поверх нового вмісту
            widget.setParent(None)
            widget.deleteLater()
        elif (child := item.layout()) is not None:
            clear_layout(child)


def _flags(*flags) -> int:
    # QPainter.drawText приймає int; Alignment і TextFlag — різні enum-и, тож складаємо значення
    result = 0
    for f in flags:
        result |= f.value
    return result


class StatusBadge(QLabel):
    """Кольорова «пігулка» зі статусом замовлення."""

    def __init__(self, status: OrderStatus | None = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if status is not None:
            self.set_status(status)

    def set_status(self, status: OrderStatus) -> None:
        color = theme.STATUS_COLORS.get(status, theme.MUTED)
        self.setText(status.value)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background: {theme.rgba(color, 0.14)};"
            f" border-radius: 10px; padding: 3px 10px; font-size: 12px; font-weight: 600; }}"
        )


class StatusDot(QLabel):
    def __init__(self, color: str, size: int = 8, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(f"background: {color}; border-radius: {self._size // 2}px;")


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")


class SectionCard(QFrame):
    """Картка-блок панелі деталей: заголовок + рядки «ключ — значення»."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 14)
        self._layout.setSpacing(8)
        self._layout.addWidget(SectionLabel(title))

    def add_text(self, text: str, object_name: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._layout.addWidget(label)
        return label

    def add_row(self, key: str, value: str, *, mono: bool = False) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        key_label = QLabel(key)
        key_label.setObjectName("kvKey")
        value_label = QLabel(value)
        value_label.setWordWrap(True)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if mono:
            value_label.setFont(mono_font())
        row.addWidget(key_label, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(value_label, 1)
        self._layout.addLayout(row)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def add_separator(self) -> None:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        self._layout.addWidget(line)


class StatCard(QFrame):
    """Плашка-лічильник із зміною «від вчора»."""

    def __init__(self, title: str, higher_is_better: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self._higher_is_better = higher_is_better
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(1)

        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        self._value = QLabel("0")
        self._value.setObjectName("statValue")
        self._delta = QLabel()
        self._delta.setObjectName("statDelta")
        since_label = QLabel("від вчора")
        since_label.setObjectName("statSince")

        layout.addWidget(title_label)
        layout.addWidget(self._value)
        layout.addWidget(self._delta)
        layout.addWidget(since_label)
        self.set_values(0, 0)

    def set_values(self, value: int, delta: int) -> None:
        self._value.setText(str(value))
        if delta == 0:
            arrow, color = "•", theme.MUTED
        else:
            arrow = "▲" if delta > 0 else "▼"
            good = (delta > 0) == self._higher_is_better
            color = theme.GREEN if good else theme.RED
        self._delta.setText(f"{arrow} {abs(delta)}")
        self._delta.setStyleSheet(f"color: {color}; font-weight: 600;")


class RingProgress(QWidget):
    """Кругове кільце прогресу з відсотком і підписом усередині (малюється QPainter)."""

    THICKNESS = 10

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._caption = caption
        self.setMinimumSize(150, 150)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, value))
        self.update()

    def _color(self) -> str:
        if self._value >= 70:
            return theme.GREEN
        if self._value >= 40:
            return theme.AMBER
        return theme.RED

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height()) - self.THICKNESS - 4
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

        pen = QPen(QColor(theme.HOVER), self.THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        if self._value > 0:
            pen.setColor(QColor(self._color()))
            painter.setPen(pen)
            # Від 12 години за годинниковою стрілкою (від'ємний кут)
            painter.drawArc(rect, 90 * 16, -round(self._value / 100 * 360 * 16))

        value_font = QFont(self.font())
        value_font.setPixelSize(int(side * 0.22))
        value_font.setWeight(QFont.Weight.Bold)
        painter.setFont(value_font)
        painter.setPen(QColor(theme.TEXT))
        value_rect = QRectF(rect.left(), rect.top() + side * 0.24, side, side * 0.28)
        painter.drawText(value_rect, _flags(Qt.AlignmentFlag.AlignCenter), f"{self._value:.0f}%")

        caption_font = QFont(self.font())
        caption_font.setPixelSize(11)
        painter.setFont(caption_font)
        painter.setPen(QColor(theme.MUTED))
        inner = side - 2 * self.THICKNESS - 24
        caption_rect = QRectF(self.width() / 2 - inner / 2, value_rect.bottom() + 2, inner, side * 0.3)
        painter.drawText(
            caption_rect,
            _flags(Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignTop, Qt.TextFlag.TextWordWrap),
            self._caption,
        )


class Timeline(QWidget):
    """Вертикальний таймлайн історії замовлення: крапки, з'єднувальна лінія, назва і час."""

    ROW = 50
    LAST_ROW = 38
    X = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[HistoryEvent] = []

    def set_events(self, events: list[HistoryEvent]) -> None:
        self._events = list(events)
        self.setFixedHeight(max(0, len(self._events) - 1) * self.ROW + self.LAST_ROW)
        self.update()

    def paintEvent(self, event):
        if not self._events:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        last = len(self._events) - 1
        text_x = self.X + 20
        text_w = self.width() - text_x

        painter.setPen(QPen(QColor(theme.BORDER), 2))
        for i in range(last):
            cy = 10 + i * self.ROW
            painter.drawLine(QPointF(self.X, cy), QPointF(self.X, cy + self.ROW))

        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        time_font = QFont(self.font())
        time_font.setPixelSize(11)

        for i, ev in enumerate(self._events):
            cy = 10 + i * self.ROW
            color = QColor(theme.STATUS_COLORS.get(ev.status, theme.MUTED))
            painter.setPen(Qt.PenStyle.NoPen)
            if i == last:
                halo = QColor(color)
                halo.setAlpha(55)
                painter.setBrush(halo)
                painter.drawEllipse(QPointF(self.X, cy), 8, 8)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(self.X, cy), 4.5, 4.5)

            title_font.setWeight(QFont.Weight.DemiBold if i == last else QFont.Weight.Normal)
            painter.setFont(title_font)
            painter.setPen(QColor(theme.TEXT))
            title = QFontMetrics(title_font).elidedText(ev.status.value, Qt.TextElideMode.ElideRight, int(text_w))
            painter.drawText(QRectF(text_x, cy - 10, text_w, 20), _flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter), title)

            painter.setFont(time_font)
            painter.setPen(QColor(theme.MUTED_2))
            painter.drawText(
                QRectF(text_x, cy + 9, text_w, 16),
                _flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter),
                ev.at.strftime("%d.%m.%Y, %H:%M"),
            )


class EmptyState(QFrame):
    """Заглушка для порожніх вкладок і розділів у розробці."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("emptyTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("muted")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(sub)
