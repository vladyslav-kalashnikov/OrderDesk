"""Розбір рядків проксі та налаштування проксі для Playwright.

HTTP(S) з паролем Playwright вміє сам. SOCKS5 з паролем — ні, тому для нього піднімаємо
локальний HTTP-форвардер pproxy (у тому ж asyncio-циклі, без окремого процесу).
"""
import asyncio
import re
import socket
from dataclasses import dataclass

from .entities import Proxy
from .secrets import get_secret

SCHEMES = ("http", "https", "socks5")
_URL_RE = re.compile(
    r"^(?P<scheme>https?|socks5h?)://(?:(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?@)?"
    r"(?P<host>[^:@/]+):(?P<port>\d{1,5})/?$",
    re.IGNORECASE,
)


@dataclass
class ParsedProxy:
    scheme: str
    host: str
    port: int
    username: str | None
    password: str | None


def parse_proxy_line(line: str, default_scheme: str = "http") -> ParsedProxy:
    """Підтримує `scheme://user:pass@host:port`, `host:port`, `host:port:user:pass`."""
    line = line.strip()
    if m := _URL_RE.match(line):
        scheme = m["scheme"].lower().replace("socks5h", "socks5")
        return ParsedProxy(scheme, m["host"], _port(m["port"]), m["user"], m["password"])
    parts = line.split(":")
    if len(parts) in (2, 4) and parts[1].isdigit():
        user, password = (parts[2], parts[3]) if len(parts) == 4 else (None, None)
        return ParsedProxy(default_scheme, parts[0], _port(parts[1]), user, password)
    raise ValueError(f"Не вдалося розібрати проксі: {line!r}")


def _port(raw: str) -> int:
    port = int(raw)
    if not 0 < port < 65536:
        raise ValueError(f"Невірний порт: {port}")
    return port


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProxyManager:
    """Видає словник `proxy=` для Playwright; живе в asyncio-циклі рушія."""

    def __init__(self, log):
        self._log = log
        self._forwarders: dict[tuple, tuple[asyncio.AbstractServer, int]] = {}
        self._lock = asyncio.Lock()

    async def playwright_settings(self, proxy: Proxy) -> dict:
        password = get_secret("proxy", proxy.id) if proxy.username else None
        if proxy.scheme in ("http", "https"):
            settings = {"server": f"{proxy.scheme}://{proxy.host}:{proxy.port}"}
            if proxy.username:
                settings |= {"username": proxy.username, "password": password or ""}
            return settings
        if proxy.scheme == "socks5":
            if not proxy.username:
                return {"server": f"socks5://{proxy.host}:{proxy.port}"}
            port = await self._forwarder(proxy, password or "")
            return {"server": f"http://127.0.0.1:{port}"}
        raise ValueError(f"Непідтримувана схема проксі: {proxy.scheme}")

    async def _forwarder(self, proxy: Proxy, password: str) -> int:
        # Ключ включає пароль: змінили пароль — підніметься новий форвардер
        key = (proxy.id, proxy.host, proxy.port, proxy.username, password)
        async with self._lock:
            if key in self._forwarders:
                return self._forwarders[key][1]
            import pproxy

            port = _free_port()
            # Слухаємо лише loopback: пароля на вході немає, назовні його відкривати не можна
            listener = pproxy.Server(f"http://127.0.0.1:{port}")
            upstream = pproxy.Connection(f"socks5://{proxy.host}:{proxy.port}#{proxy.username}:{password}")
            server = await listener.start_server({"rserver": [upstream], "verbose": lambda *_: None})
            self._forwarders[key] = (server, port)
            self._log("INFO", f"proxy#{proxy.id}", f"SOCKS5-форвардер 127.0.0.1:{port} → {proxy.host}:{proxy.port}")
            return port

    async def close(self) -> None:
        for server, _ in self._forwarders.values():
            server.close()
        self._forwarders.clear()
