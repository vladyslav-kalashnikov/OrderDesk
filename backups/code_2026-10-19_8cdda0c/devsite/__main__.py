"""python -m devsite [--port 8765] [--restock-after 30]"""
import argparse
import errno

from .server import USERS, serve

parser = argparse.ArgumentParser(description="Локальний демо-магазин для OrderDesk")
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--restock-after", type=float, default=30, help="через скільки секунд кросівки з'являться")
args = parser.parse_args()

try:
    server = serve(port=args.port, restock_after=args.restock_after)
except OSError as exc:
    if exc.errno == errno.EADDRINUSE:
        raise SystemExit(f"Порт {args.port} зайнятий — схоже, DevShop уже запущено в іншій вкладці "
                         f"(перевірте http://127.0.0.1:{args.port}). Інший порт: --port 8766")
    raise
print(f"DevShop: http://127.0.0.1:{args.port}")
print("Акаунти:", ", ".join(f"{email} / {password}" for email, password in USERS.items()))
print(f"Худі — завжди в наявності; кросівки — через {args.restock_after:.0f} с; годинник — /admin/restock/watch")
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
