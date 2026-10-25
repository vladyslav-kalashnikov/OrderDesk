"""Тести вбудованого антидетект-профілю (fingerprint.py)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk-fp-")
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402
from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.fingerprint import (  # noqa: E402
    CHROME_STEALTH_ARGS,
    build_init_script,
    delete_fingerprint,
    fingerprint_path,
    load_or_create_fingerprint,
)
from dashboard.pages.accounts import AccountsPage  # noqa: E402


class FakeEngine:
    def state_path(self, account_id: int) -> Path:
        return paths.sessions_dir() / f"account_{account_id}.json"

    def running_task_ids(self):
        return set()

    def log(self, *args, **kwargs):
        pass


def main() -> int:
    paths.ensure_dirs()

    fp1 = load_or_create_fingerprint(1, seed_extra="acc1@example.com")
    fp2 = load_or_create_fingerprint(2, seed_extra="acc2@example.com")

    assert fingerprint_path(1).exists(), "Файл відбитка acc#1 має зберігатися"
    assert oct(fingerprint_path(1).stat().st_mode & 0o777) == "0o600", "Права доступу мають бути 0600"
    assert fp1.canvas_seed != fp2.canvas_seed, "Різні акаунти мають різні Canvas seeds"
    assert fp1.audio_seed != fp2.audio_seed, "Різні акаунти мають різні Audio seeds"
    assert fp1.rects_seed != fp2.rects_seed, "Різні акаунти мають різні ClientRects seeds"

    # Повторне завантаження повертає ідентичний відбиток
    fp1_reload = load_or_create_fingerprint(1, seed_extra="changed@example.com")
    assert fp1_reload == fp1, "Збережений відбиток має залишатися стабільним між запусками"

    # Перевірка JS-скрипта та параметрів контексту
    kwargs = fp1.to_context_kwargs()
    assert kwargs["locale"] == "uk-UA"
    assert kwargs["timezone_id"] == "Europe/Kyiv"
    assert kwargs["viewport"]["width"] == fp1.viewport_width

    script = build_init_script(fp1, proxy_ip="92.113.167.40")
    assert "92.113.167.40" in script
    assert fp1.webgl_renderer in script
    assert "disable_non_proxied_udp" in " ".join(CHROME_STEALTH_ARGS)

    # Перевірка авторозподілу проксі та відображення «🛡 Антидетект» у вкладці «Акаунти»
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QApplication.instance() or QApplication([])
    db = Database(paths.db_path())
    p1 = db.add_proxy("http", "92.113.167.40", 47289, "user1", "Proxy 1")
    p2 = db.add_proxy("http", "92.113.167.41", 47289, "user2", "Proxy 2")
    a1 = db.add_account("u1@example.com", None)
    a2 = db.add_account("u2@example.com", None)

    page = AccountsPage(db, FakeEngine())
    assert page.table.item(0, 2).text() == "🛡 Антидетект"
    page._drop_session(1)
    assert not fingerprint_path(1).exists(), "Скидання сесії має скидати й збережений відбиток"

    # Тестуємо авторозподіл проксі без модального вікна info
    from unittest.mock import patch
    with patch("dashboard.pages.accounts.info"):
        page.auto_assign_proxies()

    assert db.account(a1).proxy_id == p1
    assert db.account(a2).proxy_id == p2

    delete_fingerprint(2)
    assert not fingerprint_path(2).exists()
    print("✓ УСІ ТЕСТИ АНТИДЕТЕКТУ ТА АВТОРОЗПОДІЛУ ПРОКСІ ПРОЙДЕНО!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
