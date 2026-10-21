"""Тести інтеграції з антидетект-браузером AdsPower."""
import tempfile
from pathlib import Path
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from dashboard.core.adspower import AdsPowerClient
from dashboard.core.db import Database
from dashboard.core.entities import Account
from dashboard.core.site_config import SiteConfig


def test_adspower_client_check_status_offline():
    client = AdsPowerClient(base_url="http://127.0.0.1:59999")
    ok, msg = client.check_status(timeout=0.5)
    assert not ok
    assert "не запущено" in msg.lower() or "недоступний" in msg.lower() or "заблоковано" in msg.lower()


def test_adspower_client_start_and_stop():
    client = AdsPowerClient(base_url="http://127.0.0.1:50325")

    # Мокаємо _get_sync відповіді для start_browser
    with patch.object(client, "_get_sync") as mock_get:
        mock_get.return_value = {
            "code": 0,
            "data": {
                "ws": {
                    "puppeteer": "ws://127.0.0.1:50325/devtools/browser/abc-123"
                }
            }
        }
        ws = asyncio.run(client.start_browser("test_profile_1", open_tabs=1, headless=0))
        assert ws == "ws://127.0.0.1:50325/devtools/browser/abc-123"
        mock_get.assert_called_once()
        endpoint = mock_get.call_args[0][0]
        params = mock_get.call_args[0][1]
        assert endpoint == "/api/v1/browser/start"
        assert params["user_id"] == "test_profile_1"

    # Мокаємо serial number
    with patch.object(client, "_get_sync") as mock_get:
        mock_get.return_value = {
            "code": 0,
            "data": {
                "ws": {
                    "puppeteer": "ws://127.0.0.1:50325/devtools/browser/sn-1"
                }
            }
        }
        ws = asyncio.run(client.start_browser("105", open_tabs=1, headless=0))
        assert ws == "ws://127.0.0.1:50325/devtools/browser/sn-1"
        endpoint = mock_get.call_args[0][0]
        params = mock_get.call_args[0][1]
        assert params["serial_number"] == "105"
        assert "user_id" not in params

    # Мокаємо stop_browser
    with patch.object(client, "_get_sync") as mock_get:
        mock_get.return_value = {"code": 0, "msg": "success"}
        stopped = asyncio.run(client.stop_browser("test_profile_1"))
        assert stopped is True


def test_database_adspower_id():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        db = Database(db_path)

        acc_id = db.add_account("user@example.com", None, adspower_id="ads_profile_42")
        acc = db.account(acc_id)
        assert acc is not None
        assert acc.adspower_id == "ads_profile_42"

        db.update_account_settings(acc_id, None, adspower_id="ads_profile_99")
        acc2 = db.account(acc_id)
        assert acc2.adspower_id == "ads_profile_99"

        acc_id2 = db.add_account("default@example.com", None)
        acc_default = db.account(acc_id2)
        assert acc_default.adspower_id is None


def test_site_config_adspower_fields():
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.json"
        cfg = SiteConfig(adspower_api_url="http://127.0.0.1:50325", adspower_api_key="secret123")
        cfg.save(cfg_path)

        loaded = SiteConfig.load(cfg_path)
        assert loaded.adspower_api_url == "http://127.0.0.1:50325"
        assert loaded.adspower_api_key == "secret123"


if __name__ == "__main__":
    test_adspower_client_check_status_offline()
    test_adspower_client_start_and_stop()
    test_database_adspower_id()
    test_site_config_adspower_fields()
    print("ALL ADSPOWER TESTS PASSED!")
