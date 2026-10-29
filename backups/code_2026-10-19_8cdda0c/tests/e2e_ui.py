"""Перевірка UI без вікна: devsite + рушій + кнопки сторінок, скриншоти в папку з аргументу.

Запуск:  QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/e2e_ui.py /tmp/shots
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["ORDERDESK_DATA"] = tempfile.mkdtemp(prefix="orderdesk-ui-")
os.environ["ORDERDESK_SECRETS"] = "memory"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dashboard.bridge import EngineBridge  # noqa: E402
from dashboard.core import paths  # noqa: E402
from dashboard.core.db import Database  # noqa: E402
from dashboard.core.engine import Engine  # noqa: E402
from dashboard.core.proxy import _free_port, parse_proxy_line  # noqa: E402
from dashboard.core.secrets import set_secret  # noqa: E402
from dashboard.core.site_config import SiteConfig  # noqa: E402
from dashboard.demo_data import build_demo_data  # noqa: E402
from dashboard.main_window import MainWindow  # noqa: E402
from dashboard.theme import build_palette, build_qss  # noqa: E402
from devsite.server import serve  # noqa: E402

shots = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
shots.mkdir(parents=True, exist_ok=True)
failures = []


def check(cond, message):
    print(("  ✓ " if cond else "  ✗ ") + message)
    if not cond:
        failures.append(message)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


print("Розбір рядків проксі:")
p = parse_proxy_line("socks5://u:p@1.2.3.4:1080")
check((p.scheme, p.host, p.port, p.username, p.password) == ("socks5", "1.2.3.4", 1080, "u", "p"), "socks5://u:p@host:port")
p = parse_proxy_line("1.2.3.4:8080:user:pass")
check((p.scheme, p.username, p.password) == ("http", "user", "pass"), "host:port:user:pass")
try:
    parse_proxy_line("сміття")
    check(False, "сміття відхилено")
except ValueError:
    check(True, "сміття відхилено")

paths.ensure_dirs()
port = _free_port()
shop = serve(port=port, restock_after=3)
threading.Thread(target=shop.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
SiteConfig(base_url=base, login_url=f"{base}/login", account_check_url=f"{base}/account",
           cart_url=f"{base}/cart", ip_check_url=f"{base}/ip", orders_url=f"{base}/cabinet/orders").save()

db = Database(paths.db_path())
for order in build_demo_data().orders:
    db.upsert_order(order)
proxy_id = db.add_proxy("http", "127.0.0.1", 9, None, "Мертвий проксі")  # порт 9 — нікого немає
acc = db.add_account("demo@example.com", None)
set_secret("account", "demo@example.com", "demo12345")
db.add_task(acc, f"{base}/product/sneakers", "cart", 1)
db.add_task(acc, f"{base}/product/hoodie", "monitor", 1)
db.add_task(acc, f"{base}/product/watch", "monitor", 2)

app = QApplication([])
app.setStyle("Fusion")
app.setPalette(build_palette())
app.setStyleSheet(build_qss())
bridge = EngineBridge()
engine = Engine(db, bridge)
engine.start()
w = MainWindow(db, engine, bridge)
w.resize(1600, 920)
w.show()
pump(app, 0.3)

print("Моніторинг:")
w.sidebar.nav.setCurrentRow(2)
pump(app, 0.2)
w.monitoring.start_selected()  # нічого не виділено → стартують усі
pump(app, 12)
statuses = [w.monitoring.table.item(r, 5).text() for r in range(3)]
check(statuses[0] == "Готово" and statuses[1] == "Готово", f"кросівки і худі — «Готово»: {statuses}")
check(statuses[2] == "Моніторинг", "годинник моніториться")
w.monitoring.table.selectRow(2)
w.grab().save(str(shots / "1_monitoring.png"))
w.monitoring.stop_selected()
pump(app, 4)
check(w.monitoring.table.item(2, 5).text() == "Зупинено", "«Стоп» зупинив виділене завдання")

print("Проксі:")
w.sidebar.nav.setCurrentRow(3)
w.proxies.check_selected()
pump(app, 6)
check(db.proxy(proxy_id).status == "Не працює", "мертвий проксі позначено «Не працює»")
w.grab().save(str(shots / "4_proxies.png"))

print("Акаунти / кабінет / логи / налаштування:")
w.sidebar.nav.setCurrentRow(1)
pump(app, 0.3)
check(w.accounts.table.item(0, 4).text() == "Активний", "акаунт активний, сесія збережена")
w.grab().save(str(shots / "2_accounts.png"))

w.sidebar.nav.setCurrentRow(0)
pump(app, 0.3)
tabs = [w.cabinet.tabs.tabText(i) for i in range(3)]
check(tabs == ["Бажане (3)", "Кошик (1)", "Замовлення (4)"], f"вкладки кабінету: {tabs}")
w.cabinet.tabs.setCurrentIndex(1)
pump(app, 0.2)
w.grab().save(str(shots / "0_cabinet_cart.png"))
w.cabinet.tabs.setCurrentIndex(2)
w.cabinet.table.selectRow(1)
pump(app, 0.3)
check(w.details.isVisible(), "деталі замовлення відкриваються")
w.grab().save(str(shots / "0_cabinet_orders.png"))

w.sidebar.nav.setCurrentRow(4)
pump(app, 0.2)
check("Додано в кошик" in w.logs.view.toPlainText(), "у логах є запис про кошик")
w.grab().save(str(shots / "5_logs.png"))
w.sidebar.nav.setCurrentRow(5)
pump(app, 0.2)
w.grab().save(str(shots / "6_settings.png"))

print("Кнопка «Оновити замовлення»:")
w.sidebar.nav.setCurrentRow(0)
w.cabinet.tabs.setCurrentIndex(2)
w.cabinet.sync_button.click()
check(not w.cabinet.sync_button.isEnabled(), "кнопка блокується на час збору")
pump(app, 10)
check(w.cabinet.sync_button.isEnabled(), "після збору кнопка знову активна")
check(w.cabinet.tabs.tabText(2) == "Замовлення (7)", f"4 демо + 3 з кабінету devsite: {w.cabinet.tabs.tabText(2)}")
w.grab().save(str(shots / "0_cabinet_synced.png"))

stats = db.daily_stats()
check((stats.runs, stats.success) == (3, 2), f"статистика сайдбару: {stats.runs} запусків, {stats.success} успішних")
w.close()
pump(app, 0.5)
shop.shutdown()
print("\nРЕЗУЛЬТАТ:", "усе пройшло" if not failures else f"{len(failures)} провалів")
sys.exit(1 if failures else 0)
