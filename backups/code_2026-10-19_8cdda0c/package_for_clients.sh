#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

ZIP_NAME="OrderDesk-Client.zip"
TMP_DIR="$DIR/OrderDesk-Client"

echo "=== Створення чистого архіву для клієнтів ==="
rm -rf "$TMP_DIR" "$ZIP_NAME"
mkdir -p "$TMP_DIR"

# 1. Копіюємо додаток та лаунчери
echo "Копіюємо лаунчери..."
cp -R OrderDesk.app "$TMP_DIR/"
cp OrderDesk.command "$TMP_DIR/"
cp OrderDesk.bat "$TMP_DIR/"
cp OrderDesk.vbs "$TMP_DIR/"
cp requirements.txt "$TMP_DIR/"

# 2. Копіюємо код
echo "Копіюємо модулі dashboard..."
cp -R dashboard "$TMP_DIR/"

# 3. Створюємо чисту папку data з налаштуваннями сайту та хмари
echo "Підготовка чистих конфігурацій..."
mkdir -p "$TMP_DIR/data"
if [ -f "data/site.json" ]; then
    cp data/site.json "$TMP_DIR/data/"
fi
if [ -f "data/auth_config.json" ]; then
    cp data/auth_config.json "$TMP_DIR/data/"
fi

# 4. Видаляємо тимчасові файли кешу та бекапів
find "$TMP_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$TMP_DIR" -name "*.pyc" -delete 2>/dev/null || true
find "$TMP_DIR" -name "*.orig" -delete 2>/dev/null || true
find "$TMP_DIR" -name "*.v2" -delete 2>/dev/null || true
find "$TMP_DIR" -name ".DS_Store" -delete 2>/dev/null || true

# 5. Пакуємо в ZIP
echo "Створюємо $ZIP_NAME..."
zip -r -q "$ZIP_NAME" OrderDesk-Client/

rm -rf "$TMP_DIR"

echo "=== Готово! Файл створено: $ZIP_NAME ==="
ls -lh "$ZIP_NAME"
