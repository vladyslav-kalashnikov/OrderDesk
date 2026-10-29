"""Де лежать дані програми. Перевизначається змінною ORDERDESK_DATA (тести, кілька профілів)."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("ORDERDESK_DATA", PROJECT_DIR / "data"))


def db_path() -> Path:
    return data_dir() / "orderdesk.db"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def site_config_path() -> Path:
    return data_dir() / "site.json"


def auth_config_path() -> Path:
    return data_dir() / "auth_config.json"


def auth_session_path() -> Path:
    return data_dir() / "auth_session.json"


def local_users_path() -> Path:
    return data_dir() / "local_users.json"


def ensure_dirs() -> None:
    # Сесії = фактично паролі, тож папки доступні лише власнику
    for path in (data_dir(), sessions_dir()):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
