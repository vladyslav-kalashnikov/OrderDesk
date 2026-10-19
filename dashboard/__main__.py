"""Точка входу:  python -m dashboard [--demo]"""
import argparse
import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .bridge import EngineBridge
from .core import paths
from .core.auth import AuthManager
from .core.db import Database
from .core.engine import Engine
from .demo_data import build_demo_data
from .main_window import MainWindow
from .theme import build_palette, build_qss


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME}: моніторинг наявності, кошик, замовлення")
    parser.add_argument("--demo", action="store_true", help="додати демо-замовлення у вкладку «Замовлення»")
    args, qt_args = parser.parse_known_args()

    paths.ensure_dirs()
    db = Database(paths.db_path())
    if args.demo:
        for order in build_demo_data().orders:
            db.upsert_order(order)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    # Fusion однаково виглядає на всіх ОС і коректно слухається QSS
    app.setStyle("Fusion")
    from PySide6.QtCore import QSettings
    from . import theme
    saved = QSettings(APP_NAME, APP_NAME)
    theme.set_mode(str(saved.value("theme", "dark")))
    theme.set_font_px(int(saved.value("font_px", 14)))
    app.setPalette(build_palette())
    app.setStyleSheet(build_qss())

    auth = AuthManager()

    while True:
        valid, _ = auth.verify_session()
        if not valid:
            from .login_window import LoginDialog
            login_dialog = LoginDialog(auth)
            if login_dialog.exec() != LoginDialog.DialogCode.Accepted:
                db.close()
                return 0

        bridge = EngineBridge()
        engine = Engine(db, bridge)
        engine.start()

        window = MainWindow(db, engine, bridge, auth)
        screen = app.primaryScreen().availableGeometry()
        window.resize(min(1600, int(screen.width() * 0.92)), min(920, int(screen.height() * 0.9)))
        window.show()
        code = app.exec()
        engine.shutdown()

        if getattr(window, "relogin_requested", False):
            continue
        break

    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
