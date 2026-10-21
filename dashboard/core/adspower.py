"""Клієнт локального API AdsPower для керування антидетект-профілями браузера."""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("adspower")


class AdsPowerError(Exception):
    """Помилка взаємодії з AdsPower API."""
    pass


class AdsPowerClient:
    def __init__(self, base_url: str = "http://local.adspower.net:50325", api_key: str = ""):
        self.base_url = (base_url or "http://local.adspower.net:50325").rstrip("/")
        self.api_key = (api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "OrderDesk-AdsPower"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get_sync(self, path: str, params: dict[str, Any] | None = None, timeout: float = 12.0) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.URLError as exc:
            raise AdsPowerError(f"AdsPower недоступний ({self.base_url}): {exc.reason}") from exc
        except Exception as exc:
            raise AdsPowerError(f"Помилка зв'язку з AdsPower ({path}): {exc}") from exc

    async def get(self, path: str, params: dict[str, Any] | None = None, timeout: float = 12.0) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_sync, path, params, timeout)

    def check_status(self, timeout: float = 4.0) -> tuple[bool, str]:
        """Перевіряє доступність запущеного клієнта AdsPower."""
        try:
            res = self._get_sync("/status", timeout=timeout)
            if res.get("code") == 0 or "msg" in res:
                return True, "AdsPower підключено (OK)"
            return False, f"AdsPower повернув помилку: {res.get('msg')}"
        except Exception as exc:
            return False, f"AdsPower не запущено або заблоковано порт: {exc}"

    async def check_status_async(self, timeout: float = 4.0) -> tuple[bool, str]:
        return await asyncio.to_thread(self.check_status, timeout)

    async def is_active(self, profile_id_or_sn: str) -> bool:
        """Перевіряє, чи активний зараз процес браузера для профілю."""
        p = profile_id_or_sn.strip()
        if not p:
            return False
        params = {"serial_number": p} if p.isdigit() else {"user_id": p}
        try:
            res = await self.get("/api/v1/browser/active", params=params, timeout=5.0)
            if res.get("code") == 0:
                data = res.get("data", {})
                status = data.get("status")
                return status in ("Active", "active", True, 1)
        except Exception:
            pass
        return False

    async def start_browser(self, profile_id_or_sn: str, open_tabs: int = 1, headless: int = 0) -> str:
        """
        Запускає профіль AdsPower і повертає WebSocket CDP адресу для підключення через Playwright.
        """
        p = profile_id_or_sn.strip()
        if not p:
            raise AdsPowerError("Не вказано ID або номер профілю AdsPower")

        params: dict[str, Any] = {
            "open_tabs": open_tabs,
            "ip_tab": 0,
        }
        if headless:
            params["headless"] = 1
        if p.isdigit():
            params["serial_number"] = p
        else:
            params["user_id"] = p

        res = await self.get("/api/v1/browser/start", params=params, timeout=30.0)
        code = res.get("code")
        if code != 0:
            msg = res.get("msg", "Невідома помилка AdsPower")
            raise AdsPowerError(f"AdsPower не зміг запустити профіль {p}: {msg}")

        data = res.get("data", {})
        ws = data.get("ws", {})
        puppeteer_ws = ws.get("puppeteer")
        if not puppeteer_ws:
            # Fallback to debug port
            debug_port = data.get("debug_port")
            if debug_port:
                puppeteer_ws = f"http://127.0.0.1:{debug_port}"

        if not puppeteer_ws:
            raise AdsPowerError(f"AdsPower не повернув адресу підключення (відповідь: {data})")

        return puppeteer_ws

    async def stop_browser(self, profile_id_or_sn: str) -> bool:
        """Зупиняє профіль браузера AdsPower."""
        p = profile_id_or_sn.strip()
        if not p:
            return False
        params = {"serial_number": p} if p.isdigit() else {"user_id": p}
        try:
            res = await self.get("/api/v1/browser/stop", params=params, timeout=10.0)
            return res.get("code") == 0
        except Exception:
            return False

    async def list_profiles(self, page_size: int = 100) -> list[dict[str, Any]]:
        """Отримує список профілів з AdsPower."""
        try:
            res = await self.get("/api/v1/user/list", params={"page_size": page_size}, timeout=8.0)
            if res.get("code") == 0:
                data = res.get("data", {})
                return data.get("list", [])
        except Exception:
            pass
        return []
