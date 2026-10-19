"""Модуль авторизації: робота з Supabase Auth API, сесіями та локальним сховищем."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from . import paths


@dataclass
class AuthConfig:
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    admin_passcode: str = "admin777"
    allow_offline: bool = True
    github_repo: str = ""

    @property
    def is_supabase_configured(self) -> bool:
        url = (self.supabase_url or "").strip()
        key = (self.supabase_anon_key or "").strip()
        return bool(url and key and "supabase.co" in url and not url.startswith("https://your-project"))


@dataclass
class UserSession:
    user_id: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: int
    role: str = "authenticated"
    is_offline: bool = False

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - buffer_seconds)


def load_auth_config() -> AuthConfig:
    cfg_path = paths.auth_config_path()
    defaults = AuthConfig(
        supabase_url=os.environ.get("SUPABASE_URL", "https://wyivqkgeqrjnbcqdlurl.supabase.co"),
        supabase_anon_key=os.environ.get(
            "SUPABASE_ANON_KEY",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind5aXZxa2dlcXJqbmJjcWRsdXJsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI0MjIyOTMsImV4cCI6MjEwNzk5ODI5M30.1Wm8PT564P0HGdiowblAHfz1j_3A_O7sXa_pntUH2ws"
        ),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        admin_passcode=os.environ.get("ADMIN_PASSCODE", "admin777"),
        allow_offline=True,
        github_repo=os.environ.get("GITHUB_REPO", "vladyslav-kalashnikov/OrderDesk"),
    )
    if not cfg_path.exists():
        save_auth_config(defaults)
        return defaults
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return AuthConfig(
            supabase_url=data.get("supabase_url") or defaults.supabase_url,
            supabase_anon_key=data.get("supabase_anon_key") or defaults.supabase_anon_key,
            supabase_service_role_key=data.get("supabase_service_role_key") or defaults.supabase_service_role_key,
            admin_passcode=data.get("admin_passcode") or defaults.admin_passcode,
            allow_offline=data.get("allow_offline", True),
            github_repo=data.get("github_repo") or defaults.github_repo,
        )
    except Exception:
        return defaults


def save_auth_config(cfg: AuthConfig) -> None:
    cfg_path = paths.auth_config_path()
    paths.ensure_dirs()
    data = {
        "supabase_url": cfg.supabase_url.strip(),
        "supabase_anon_key": cfg.supabase_anon_key.strip(),
        "supabase_service_role_key": cfg.supabase_service_role_key.strip(),
        "admin_passcode": cfg.admin_passcode.strip(),
        "allow_offline": cfg.allow_offline,
        "github_repo": cfg.github_repo.strip(),
    }
    cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class AuthManager:
    def __init__(self, config: AuthConfig | None = None) -> None:
        self.config = config or load_auth_config()
        self._current_session: UserSession | None = None
        self._http_timeout = 10.0

    def reload_config(self) -> None:
        self.config = load_auth_config()

    def update_config(
        self,
        supabase_url: str,
        anon_key: str,
        service_key: str = "",
        admin_code: str = "",
        github_repo: str = "",
    ) -> None:
        self.config.supabase_url = supabase_url.strip()
        self.config.supabase_anon_key = anon_key.strip()
        if service_key is not None:
            self.config.supabase_service_role_key = service_key.strip()
        if admin_code:
            self.config.admin_passcode = admin_code.strip()
        if github_repo is not None:
            self.config.github_repo = github_repo.strip()
        save_auth_config(self.config)

    @property
    def is_configured(self) -> bool:
        return self.config.is_supabase_configured

    def current_user(self) -> UserSession | None:
        return self._current_session

    # -------------------------------------------------------------------------
    # Вхід (Login)
    # -------------------------------------------------------------------------
    def login(self, email: str, password: str, remember: bool = True) -> tuple[bool, str, UserSession | None]:
        email = email.strip().lower()
        password = password.strip()
        if not email or not password:
            return False, "Будь ласка, введіть email та пароль.", None

        if self.is_configured:
            ok, msg, session = self._supabase_login(email, password)
        else:
            if not self.config.allow_offline:
                return False, "Хмара Supabase не налаштована, а офлайн-режим вимкнено.", None
            ok, msg, session = self._offline_login(email, password)

        if ok and session:
            self._current_session = session
            if remember:
                self._save_session_file(session)
            else:
                self._remove_session_file()
        return ok, msg, session

    def _supabase_login(self, email: str, password: str) -> tuple[bool, str, UserSession | None]:
        url = f"{self.config.supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
        headers = {
            "apikey": self.config.supabase_anon_key,
            "Content-Type": "application/json",
        }
        payload = {"email": email, "password": password}
        try:
            with httpx.Client(timeout=self._http_timeout) as client:
                res = client.post(url, headers=headers, json=payload)
                data = res.json()
        except httpx.RequestError as exc:
            return False, f"Помилка з'єднання з сервером авторизації: {exc}", None
        except Exception as exc:
            return False, f"Непередбачена помилка: {exc}", None

        if res.status_code == 200:
            user_data = data.get("user", {})
            expires_in = int(data.get("expires_in", 3600))
            session = UserSession(
                user_id=user_data.get("id", ""),
                email=user_data.get("email", email),
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires_at=int(time.time() + expires_in),
                role=user_data.get("role", "authenticated"),
                is_offline=False,
            )
            return True, "Авторизація успішна!", session

        # Обробка типових помилок Supabase
        err_msg = data.get("msg") or data.get("error_description") or data.get("message") or ""
        if "Invalid login credentials" in err_msg:
            return False, "Невірний email або пароль.", None
        if "Email not confirmed" in err_msg:
            return False, "Email не підтверджено. Підтвердіть пошту або вимкніть 'Confirm email' у Supabase.", None
        return False, err_msg or f"Помилка авторизації (код {res.status_code}).", None

    # -------------------------------------------------------------------------
    # Реєстрація / Створення акаунта (Sign Up)
    # -------------------------------------------------------------------------
    def signup(self, email: str, password: str, admin_passcode: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        email = email.strip().lower()
        password = password.strip()
        if not email or not password:
            return False, "Введіть email та пароль.", None
        if len(password) < 6:
            return False, "Пароль повинен містити щонайменше 6 символів.", None

        # Перевірка секретного коду адміністратора (щоб сторонні не реєструвалися)
        expected_code = (self.config.admin_passcode or "").strip()
        if expected_code and admin_passcode.strip() != expected_code:
            return False, "Невірний секретний код адміністратора для створення акаунта.", None

        if self.is_configured:
            return self._supabase_signup(email, password)
        else:
            if not self.config.allow_offline:
                return False, "Хмара Supabase не налаштована.", None
            return self._offline_signup(email, password)

    def _supabase_signup(self, email: str, password: str) -> tuple[bool, str, dict[str, Any] | None]:
        base_url = self.config.supabase_url.rstrip("/")

        # Якщо вказано service_role_key, використовуємо admin API для миттєвого створення підтвердженого акаунта
        if self.config.supabase_service_role_key:
            url = f"{base_url}/auth/v1/admin/users"
            headers = {
                "apikey": self.config.supabase_service_role_key,
                "Authorization": f"Bearer {self.config.supabase_service_role_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "email": email,
                "password": password,
                "email_confirm": True,
            }
        else:
            url = f"{base_url}/auth/v1/signup"
            headers = {
                "apikey": self.config.supabase_anon_key,
                "Content-Type": "application/json",
            }
            payload = {"email": email, "password": password}

        try:
            with httpx.Client(timeout=self._http_timeout) as client:
                res = client.post(url, headers=headers, json=payload)
                data = res.json()
        except httpx.RequestError as exc:
            return False, f"Помилка зв'язку з сервером: {exc}", None
        except Exception as exc:
            return False, f"Помилка створення акаунта: {exc}", None

        if res.status_code in (200, 201):
            return True, "Акаунт успішно створено! Тепер користувач може увійти.", data

        err_msg = data.get("msg") or data.get("error_description") or data.get("message") or ""
        if "User already registered" in err_msg or "already exists" in err_msg:
            return False, "Користувач із таким email вже зареєстрований.", None
        return False, err_msg or f"Не вдалося створити акаунт (код {res.status_code}).", None

    # -------------------------------------------------------------------------
    # Перевірка і поновлення сесії (Verify / Refresh Session)
    # -------------------------------------------------------------------------
    def verify_session(self) -> tuple[bool, UserSession | None]:
        session = self._load_session_file()
        if not session:
            return False, None

        if session.is_offline:
            # Офлайн перевірка
            users = self._load_offline_users()
            if session.email in users:
                self._current_session = session
                return True, session
            self._remove_session_file()
            return False, None

        if not self.is_configured:
            # Якщо сесія онлайн, але конфіг хмари зник — скидаємо
            self._remove_session_file()
            return False, None

        # Якщо токен ще дійсний
        if not session.is_expired():
            # Швидка валідація через Supabase /user
            ok, user_data = self._supabase_get_user(session.access_token)
            if ok:
                self._current_session = session
                return True, session

        # Якщо токен прострочений або /user відхилив — пробуємо refresh_token
        if session.refresh_token:
            refreshed_ok, new_session = self._supabase_refresh(session.refresh_token)
            if refreshed_ok and new_session:
                self._current_session = new_session
                self._save_session_file(new_session)
                return True, new_session

        self._remove_session_file()
        return False, None

    def _supabase_get_user(self, access_token: str) -> tuple[bool, dict[str, Any] | None]:
        url = f"{self.config.supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "apikey": self.config.supabase_anon_key,
            "Authorization": f"Bearer {access_token}",
        }
        try:
            with httpx.Client(timeout=self._http_timeout) as client:
                res = client.get(url, headers=headers)
                if res.status_code == 200:
                    return True, res.json()
        except httpx.RequestError:
            # Якщо інтернет тимчасово зник, але токен не прострочений — не блокуємо
            return True, None
        except Exception:
            pass
        return False, None

    def _supabase_refresh(self, refresh_token: str) -> tuple[bool, UserSession | None]:
        url = f"{self.config.supabase_url.rstrip('/')}/auth/v1/token?grant_type=refresh_token"
        headers = {
            "apikey": self.config.supabase_anon_key,
            "Content-Type": "application/json",
        }
        payload = {"refresh_token": refresh_token}
        try:
            with httpx.Client(timeout=self._http_timeout) as client:
                res = client.post(url, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    user_data = data.get("user", {})
                    expires_in = int(data.get("expires_in", 3600))
                    session = UserSession(
                        user_id=user_data.get("id", ""),
                        email=user_data.get("email", ""),
                        access_token=data.get("access_token", ""),
                        refresh_token=data.get("refresh_token", ""),
                        expires_at=int(time.time() + expires_in),
                        role=user_data.get("role", "authenticated"),
                        is_offline=False,
                    )
                    return True, session
        except Exception:
            pass
        return False, None

    def logout(self) -> None:
        if self._current_session and not self._current_session.is_offline and self.is_configured:
            try:
                url = f"{self.config.supabase_url.rstrip('/')}/auth/v1/logout"
                headers = {
                    "apikey": self.config.supabase_anon_key,
                    "Authorization": f"Bearer {self._current_session.access_token}",
                }
                with httpx.Client(timeout=4.0) as client:
                    client.post(url, headers=headers)
            except Exception:
                pass
        self._current_session = None
        self._remove_session_file()

    # -------------------------------------------------------------------------
    # Тест з'єднання з Supabase
    # -------------------------------------------------------------------------
    def test_connection(self, url: str, anon_key: str) -> tuple[bool, str]:
        clean_url = url.strip().rstrip("/")
        clean_key = anon_key.strip()
        if not clean_url or not clean_key:
            return False, "Вкажіть URL проекту та Anon Key."
        test_url = f"{clean_url}/auth/v1/settings"
        headers = {"apikey": clean_key}
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(test_url, headers=headers)
                if res.status_code == 200:
                    return True, "З'єднання з сервером Supabase успішне!"
                return False, f"Сервер повернув код {res.status_code} ({res.text[:100]})."
        except Exception as exc:
            return False, f"Не вдалося з'єднатися: {exc}"

    # -------------------------------------------------------------------------
    # Робота з файлом збереженої сесії
    # -------------------------------------------------------------------------
    def _save_session_file(self, session: UserSession) -> None:
        paths.ensure_dirs()
        path = paths.auth_session_path()
        data = asdict(session)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass

    def _load_session_file(self) -> UserSession | None:
        path = paths.auth_session_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return UserSession(
                user_id=data.get("user_id", ""),
                email=data.get("email", ""),
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires_at=int(data.get("expires_at", 0)),
                role=data.get("role", "authenticated"),
                is_offline=bool(data.get("is_offline", False)),
            )
        except Exception:
            return None

    def _remove_session_file(self) -> None:
        path = paths.auth_session_path()
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Локальний / Офлайн режим (Fallback)
    # -------------------------------------------------------------------------
    def _hash_password(self, password: str, salt: str | None = None) -> tuple[str, str]:
        if salt is None:
            salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        return pwd_hash, salt

    def _load_offline_users(self) -> dict[str, dict[str, Any]]:
        path = paths.local_users_path()
        if not path.exists():
            # Початковий обліковий запис адміністратора за замовчуванням
            pwd_hash, salt = self._hash_password("admin123")
            initial = {
                "admin@orderdesk.com": {
                    "id": "admin-local-1",
                    "hash": pwd_hash,
                    "salt": salt,
                    "role": "admin",
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            }
            path.write_text(json.dumps(initial, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                path.chmod(0o600)
            except Exception:
                pass
            return initial
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_offline_users(self, users: dict[str, dict[str, Any]]) -> None:
        path = paths.local_users_path()
        paths.ensure_dirs()
        path.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass

    def _offline_login(self, email: str, password: str) -> tuple[bool, str, UserSession | None]:
        users = self._load_offline_users()
        user = users.get(email)
        if not user:
            return False, "Користувача з таким email не знайдено в локальній базі.", None

        salt = user.get("salt", "")
        expected_hash = user.get("hash", "")
        test_hash, _ = self._hash_password(password, salt)
        if test_hash != expected_hash:
            return False, "Невірний пароль.", None

        session = UserSession(
            user_id=user.get("id", f"local-{secrets.token_hex(4)}"),
            email=email,
            access_token=f"local-token-{secrets.token_hex(16)}",
            refresh_token=f"local-refresh-{secrets.token_hex(16)}",
            expires_at=int(time.time() + 86400 * 30),  # 30 днів
            role=user.get("role", "authenticated"),
            is_offline=True,
        )
        return True, "Успішний офлайн-вхід!", session

    def _offline_signup(self, email: str, password: str) -> tuple[bool, str, dict[str, Any] | None]:
        users = self._load_offline_users()
        if email in users:
            return False, "Користувач із таким email вже існує.", None

        pwd_hash, salt = self._hash_password(password)
        uid = f"user-{secrets.token_hex(6)}"
        users[email] = {
            "id": uid,
            "hash": pwd_hash,
            "salt": salt,
            "role": "authenticated",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_offline_users(users)
        return True, "Акаунт успішно створено в локальній базі!", {"id": uid, "email": email}
