"""Кольори, палітра і QSS темної теми. Усі кольори — тут, віджети беруть їх звідси."""
from PySide6.QtGui import QColor, QPalette

from .models import OrderStatus

BG = "#15161a"
SURFACE = "#1b1d22"   # бокова панель і панель деталей
CARD = "#22252b"
HOVER = "#2a2d34"
BORDER = "#2c2f37"
TEXT = "#e7e9ee"
MUTED = "#9096a3"
MUTED_2 = "#6b7180"
ACCENT = "#5b8cff"
GREEN = "#3ecf8e"
RED = "#f06565"
AMBER = "#f5a524"
BLUE = "#4fa3ff"
VIOLET = "#a78bfa"

DARK = dict(BG="#15161a", SURFACE="#1b1d22", CARD="#22252b", HOVER="#2a2d34", BORDER="#2c2f37",
            TEXT="#e7e9ee", MUTED="#9096a3", MUTED_2="#6b7180", ACCENT="#5b8cff", GREEN="#3ecf8e",
            RED="#f06565", AMBER="#f5a524", BLUE="#4fa3ff", VIOLET="#a78bfa")
LIGHT = dict(BG="#f3f5f9", SURFACE="#ffffff", CARD="#eef1f7", HOVER="#e4e9f3", BORDER="#d9deea",
             TEXT="#1b2030", MUTED="#5f667d", MUTED_2="#8890a6", ACCENT="#2f66f0", GREEN="#13996a",
             RED="#d13f3f", AMBER="#b97700", BLUE="#1f7fe0", VIOLET="#7c5ce0")
MODE = "dark"
FONT_PX = 14


def set_mode(mode: str) -> None:
    """Перемикає палітру. Віджети читають theme.X під час малювання/створення, тож решту дороблює refresh."""
    global MODE
    MODE = "light" if mode == "light" else "dark"
    globals().update(LIGHT if MODE == "light" else DARK)
    STATE_COLORS.update(_state_colors())
    STATUS_COLORS.update(_status_colors())


def set_font_px(px: int) -> None:
    global FONT_PX
    FONT_PX = max(12, min(20, int(px)))


def _status_colors() -> dict:
    return {
        OrderStatus.AWAITING_PAYMENT: AMBER,
        OrderStatus.PAID: VIOLET,
        OrderStatus.CONFIRMED: ACCENT,
        OrderStatus.SHIPPED: BLUE,
        OrderStatus.DELIVERING: BLUE,
        OrderStatus.RECEIVED: GREEN,
        OrderStatus.CANCELLED: RED,
    }


STATUS_COLORS = _status_colors()

# Статуси завдань, акаунтів і проксі (рядки з core/entities.py)
def _state_colors() -> dict:
    return {
        "Готово": GREEN, "Активний": GREEN, "Працює": GREEN,
        "Помилка": RED, "Не працює": RED,
        "Запуск": ACCENT, "Вхід": ACCENT, "Моніторинг": BLUE, "Додаємо в кошик": VIOLET, "Оформлення": VIOLET,
        "Зупинено": AMBER, "Очікує": MUTED,
    }


STATE_COLORS = _state_colors()


def rgba(hex_color: str, alpha: float) -> str:
    """'#rrggbb' + прозорість 0..1 → рядок rgba() для QSS (альфа в QSS — 0..255)."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


def build_palette() -> QPalette:
    """Темна палітра — для того, чого не торкається QSS (меню, підказки, діалоги)."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: CARD,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: CARD,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: CARD,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: MUTED_2,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    return p


# Токени @name підставляються в build_qss(); так QSS не тоне в подвоєних {{ }} f-рядка
_QSS = """
QWidget { color: @text; font-size: @fspx; }

#root, #content { background: @bg; }
#sidebar { background: @surface; border-right: 1px solid @border; }
#details { background: @surface; border-left: 1px solid @border; }
#detailsHeader { border-bottom: 1px solid @border; }
#sidebarFooter { border-top: 1px solid @border; }

#brand { font-size: 18px; font-weight: 700; letter-spacing: 0.3px; }
#logoMark { color: @accent; font-size: 16px; }
#pageTitle { font-size: 26px; font-weight: 700; letter-spacing: -0.3px; }
#detailsTitle { font-size: 17px; font-weight: 700; }
#sectionLabel { color: @muted2; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
#muted { color: @muted; }
#small { color: @muted2; font-size: 11px; }
#kvKey { color: @muted; }
#kvStrong { font-size: 14px; font-weight: 600; }
#emptyTitle { font-size: 18px; font-weight: 600; }

#card, #statCard { background: @card; border: 1px solid @border; border-radius: 14px; }
#statTitle { color: @muted; font-size: 11px; }
#statValue { font-size: 20px; font-weight: 700; }
#statDelta { font-size: 11px; }
#statSince { color: @muted2; font-size: 10px; }

QListWidget#nav { background: transparent; border: none; outline: 0; }
QListWidget#nav::item { color: @muted; padding: 0 12px; margin: 3px 0; border-radius: 10px; border-left: 3px solid transparent; font-size: @fsnav; }
QListWidget#nav::item:hover { background: @hover; color: @text; }
QListWidget#nav::item:selected { background: @accentSoft; color: @text; font-weight: 700; border-left: 3px solid @accent; }

QLineEdit#search {
    background: @surface; border: 1px solid @border; border-radius: 10px;
    padding: 9px 12px; selection-background-color: @accent;
}
QLineEdit#search:focus { border-color: @accent; }

QTabBar#tabs::tab {
    background: transparent; color: @muted; padding: 10px 16px; margin-right: 6px;
    border: none; border-bottom: 3px solid transparent; font-weight: 600; font-size: @fsnav;
}
QTabBar#tabs::tab:hover { color: @text; }
QTabBar#tabs::tab:selected { color: @text; border-bottom-color: @accent; }

QTableWidget {
    background: @surface; border: 1px solid @border; border-radius: 14px; alternate-background-color: @zebra;
    gridline-color: transparent; outline: 0;
    selection-background-color: @accentSoft; selection-color: @text;
}
QTableWidget::item { padding: 0 10px; border-bottom: 1px solid @border; }
QTableWidget::item:hover { background: @hover; }
QTableWidget::item:selected { background: @accentSoft; }
QHeaderView::section {
    background: @card; color: @muted; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;
    border: none; border-bottom: 1px solid @border; padding: 8px 10px;
}
QTableCornerButton::section { background: @surface; border: none; }

QPushButton#ghost {
    background: transparent; border: 1px solid @border; border-radius: 6px;
    padding: 4px 12px; color: @text;
}
QPushButton#ghost:hover { border-color: @accent; color: @accent; }
QPushButton#iconButton {
    background: transparent; border: none; border-radius: 6px;
    color: @muted; font-size: 15px; padding: 2px 8px;
}
QPushButton#iconButton:hover { background: @hover; color: @text; }

QScrollArea { background: transparent; border: none; }
QScrollArea#detailsScroll > QWidget#qt_scrollarea_viewport,
QScrollArea#sidebarScroll > QWidget#qt_scrollarea_viewport { background: transparent; }

QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 0; }
QScrollBar::handle { background: @border; border-radius: 3px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: @muted2; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QScrollArea#sidebarScroll QScrollBar:vertical { width: 4px; }

QSplitter::handle { background: @border; }

QDialog { background: @bg; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: @surface; border: 1px solid @border; border-radius: 9px;
    padding: 8px 10px; selection-background-color: @accent;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: @accent; }
QLineEdit:read-only { color: @muted; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: @card; border: 1px solid @border; selection-background-color: @accentSoft; outline: 0;
}
QPlainTextEdit#logView, QTextBrowser#logView { background: @surface; border: 1px solid @border; border-radius: 10px; padding: 6px; }
QPushButton#chip {
    background: @surface; border: 1px solid @border; border-radius: 14px; padding: 4px 11px; color: @muted; font-weight: 600;
}
QPushButton#chip:hover { color: @text; border-color: @muted2; }
QPushButton#chip:checked { background: @accent; border-color: @accent; color: #ffffff; }
#banner { background: @amberSoft; border: 1px solid @amber; border-radius: 10px; }
#bannerTitle { font-weight: 700; }
#statusBannerError { background: @redSoft; border: 1px solid @red; border-radius: 9px; padding: 10px 14px; }
#statusBannerSuccess { background: @greenSoft; border: 1px solid @green; border-radius: 9px; padding: 10px 14px; }
#statusBannerInfo { background: @accentSoft; border: 1px solid @accent; border-radius: 9px; padding: 10px 14px; }
#updateBanner { background: @accentSoft; border-bottom: 1px solid @accent; }
#updateBannerTitle { font-weight: 600; font-size: 13px; color: @text; }
QPushButton#rowBtn { padding: 2px 8px; border-radius: 6px; min-width: 26px; }
QPushButton#rowStart { color: @green; padding: 2px 8px; border-radius: 6px; min-width: 26px; }
QPushButton#rowStop { color: @red; padding: 2px 8px; border-radius: 6px; min-width: 26px; }
QLineEdit#quickUrl { padding: 9px 12px; border-radius: 8px; }
QCheckBox { spacing: 8px; }

QPushButton {
    background: @card; border: 1px solid @border; border-radius: 9px; padding: 9px 16px; color: @text; font-weight: 600;
}
QPushButton:hover { border-color: @accent; background: @hover; }
QPushButton:pressed { background: @accentSoft; }
QPushButton:disabled { color: @muted2; }
QPushButton#primary { background: @accent; border-color: @accent; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: @accentHover; border-color: @accentHover; }
QPushButton#danger { color: @red; }
QPushButton#danger:hover { border-color: @red; background: @redSoft; }
#formLabel { color: @muted; }
#hint { color: @muted2; font-size: 11px; }
QToolTip { background: @card; color: @text; border: 1px solid @border; padding: 4px 8px; }
"""


def build_qss() -> str:
    tokens = {
        "bg": BG, "surface": SURFACE, "card": CARD, "hover": HOVER, "border": BORDER,
        "text": TEXT, "muted2": MUTED_2, "muted": MUTED, "accentSoft": rgba(ACCENT, 0.16),
        "accent": ACCENT, "accentHover": "#4a7bf0", "red": RED, "green": GREEN,
        "amber": AMBER, "zebra": rgba(TEXT, 0.025), "redSoft": rgba(RED, 0.12),
        "greenSoft": rgba(GREEN, 0.14), "fsnav": f"{FONT_PX + 1}px",
        "amberSoft": rgba(AMBER, 0.14), "fspx": f"{FONT_PX}px",
    }
    qss = _QSS
    # Довші назви першими, щоб @muted не «з'їв» початок @muted2
    for name in sorted(tokens, key=len, reverse=True):
        qss = qss.replace(f"@{name}", tokens[name])
    return qss
