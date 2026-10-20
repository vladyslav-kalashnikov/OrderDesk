"""Модуль автооновлення: перевірка та завантаження оновлень коду з GitHub."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile
from typing import Any

import httpx

from . import paths

GITHUB_API_URL = "https://api.github.com/repos"
DEFAULT_REPO = "vladyslav-kalashnikov/OrderDesk"


class UpdateManager:
    def __init__(self, repo_name: str = "") -> None:
        """repo_name у форматі 'owner/repo', наприклад 'vladyslav-kalashnikov/OrderDesk'."""
        if not repo_name:
            try:
                cfg_path = paths.data_dir() / "auth_config.json"
                if cfg_path.exists():
                    data = json.loads(cfg_path.read_text(encoding="utf-8"))
                    repo_name = data.get("github_repo", "")
            except Exception:
                pass
        if not repo_name:
            repo_name = DEFAULT_REPO
        self.repo_name = repo_name.strip()

    def get_local_commit(self) -> str:
        commit_file = paths.data_dir() / "version_commit.txt"
        if commit_file.exists():
            return commit_file.read_text(encoding="utf-8").strip()
        # Якщо є .git, пробуємо прочитати з git
        git_head = paths.PROJECT_DIR / ".git" / "refs" / "heads" / "main"
        if git_head.exists():
            return git_head.read_text(encoding="utf-8").strip()
        return ""

    def save_local_commit(self, sha: str) -> None:
        commit_file = paths.data_dir() / "version_commit.txt"
        paths.ensure_dirs()
        commit_file.write_text(sha.strip(), encoding="utf-8")

    def check_update(self) -> tuple[bool, str, str, str]:
        """Повертає (є_оновлення, sha, повідомлення_коміту, zip_url)."""
        if not self.repo_name or "/" not in self.repo_name:
            return False, "", "", ""

        url = f"{GITHUB_API_URL}/{self.repo_name}/commits/main"
        headers = {"User-Agent": "OrderDesk-Updater"}
        try:
            with httpx.Client(timeout=6.0) as client:
                res = client.get(url, headers=headers)
                if res.status_code != 200:
                    return False, "", "", ""
                data: dict[str, Any] = res.json()
        except Exception:
            return False, "", "", ""

        remote_sha = data.get("sha", "")
        commit_msg = data.get("commit", {}).get("message", "Нове оновлення коду").split("\n")[0]
        local_sha = self.get_local_commit()

        if not remote_sha:
            return False, "", "", ""

        # Якщо локального коміту ще немає або він співпадає з віддаленим
        if local_sha and local_sha == remote_sha:
            return False, remote_sha, commit_msg, ""

        zip_url = f"https://github.com/{self.repo_name}/archive/refs/heads/main.zip"
        return True, remote_sha, commit_msg, zip_url

    def apply_update(self, zip_url: str, remote_sha: str = "") -> tuple[bool, str]:
        """Завантажує та розпаковує свіжий код проекту, не чіпаючи папку data/."""
        if not zip_url:
            return False, "Не вказано адресу завантаження оновлення."

        headers = {"User-Agent": "OrderDesk-Updater"}
        try:
            with httpx.Client(timeout=45.0, follow_redirects=True) as client:
                res = client.get(zip_url, headers=headers)
                if res.status_code != 200:
                    return False, f"Помилка завантаження архіву (код {res.status_code})"
                zip_bytes = res.content
        except Exception as exc:
            return False, f"Не вдалося завантажити оновлення: {exc}"

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                names = z.namelist()
                if not names:
                    return False, "Архів оновлення порожній."
                prefix = names[0].split("/")[0] + "/"

                project_root = paths.PROJECT_DIR

                for member in z.infolist():
                    rel_path = member.filename[len(prefix):]
                    # Захист: ніколи не перезаписуємо дані клієнта, базу даних або venv!
                    if (
                        not rel_path
                        or rel_path.startswith("data/")
                        or rel_path.startswith("venv/")
                        or rel_path.startswith(".git/")
                        or rel_path.endswith(".db")
                        or rel_path.endswith(".log")
                    ):
                        continue

                    target_file = project_root / rel_path
                    if member.is_dir():
                        target_file.mkdir(parents=True, exist_ok=True)
                    else:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as src, open(target_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            if remote_sha:
                self.save_local_commit(remote_sha)

            return True, "Оновлення успішно встановлено!"
        except Exception as exc:
            return False, f"Помилка встановлення оновлення: {exc}"


def restart_application() -> None:
    """Перезапускає поточний додаток після оновлення."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    QProcess.startDetached(sys.executable, sys.argv)
    app = QApplication.instance()
    if app:
        app.quit()
    else:
        sys.exit(0)


def run_cli_update() -> int:
    """Викликається з OrderDesk.bat або командного рядка: python -m dashboard.core.updater"""
    mgr = UpdateManager()
    try:
        has_update, sha, msg, zip_url = mgr.check_update()
        if not has_update:
            print("[OrderDesk] Найновіша версія програми вже встановлена.")
            return 0

        print(f"[OrderDesk] Знайдено оновлення: {msg}")
        print("[OrderDesk] Завантаження та встановлення оновлень...")
        ok, res_msg = mgr.apply_update(zip_url, sha)
        print(f"[OrderDesk] {res_msg}")
        return 0 if ok else 1
    except Exception as exc:
        print(f"[OrderDesk] Перевірка оновлень пропущена: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(run_cli_update())
