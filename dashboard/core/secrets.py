"""Паролі акаунтів, проксі й токен Telegram — у системному сховищі (Keychain на macOS), не в базі.

ORDERDESK_SECRETS=memory — сховище в пам'яті (для тестів, нічого не пише в Keychain).
"""
import os

SERVICE = "OrderDesk"
_memory: dict[str, str] = {}


def _use_memory() -> bool:
    return os.environ.get("ORDERDESK_SECRETS") == "memory"


def _key(kind: str, name: str | int) -> str:
    return f"{kind}:{name}"


def set_secret(kind: str, name: str | int, value: str) -> None:
    if _use_memory():
        _memory[_key(kind, name)] = value
        return
    import keyring
    keyring.set_password(SERVICE, _key(kind, name), value)


def get_secret(kind: str, name: str | int) -> str | None:
    if _use_memory():
        return _memory.get(_key(kind, name))
    import keyring
    return keyring.get_password(SERVICE, _key(kind, name))


def delete_secret(kind: str, name: str | int) -> None:
    if _use_memory():
        _memory.pop(_key(kind, name), None)
        return
    import keyring
    from keyring.errors import PasswordDeleteError
    try:
        keyring.delete_password(SERVICE, _key(kind, name))
    except PasswordDeleteError:
        pass  # і так немає
