@echo off
setlocal
cd /d "%~dp0"

:: Вимикаємо можливі прапорці лінивих імпортів, які можуть конфліктувати з бібліотеками
set PYTHON_LAZY_IMPORTS=
set PYTHONLAZYIMPORTS=
if not defined ORDERDESK_DATA set ORDERDESK_DATA=%~dp0data

:: 1. Закриваємо підвислі фонові процеси, якщо вони тримають файли або базу даних
taskkill /F /IM pythonw.exe >nul 2>&1

:: 2. Первинне налаштування оточення при першому запуску
if not exist "venv\Scripts\python.exe" (
    echo [OrderDesk] First run setup: creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Python 3 not found. Please install Python 3.12 or 3.13 from python.org and add it to PATH.
        pause
        exit /b 1
    )
)

:: 3. Перевірка та автоматичне встановлення відсутніх бібліотек (httpx, PySide6 тощо)
venv\Scripts\python.exe -c "import PySide6, httpx, playwright, keyring" >nul 2>&1
if errorlevel 1 (
    echo [OrderDesk] Installing / updating required libraries...
    venv\Scripts\python.exe -m pip install --upgrade pip
    if exist "requirements.txt" (
        venv\Scripts\python.exe -m pip install -r requirements.txt
        echo [OrderDesk] Installing Playwright Chromium browser...
        venv\Scripts\python.exe -m playwright install chromium
    )
)

:: 4. Автоматична перевірка та завантаження оновлень
if exist ".git" (
    echo [OrderDesk] Checking for updates from git...
    git fetch --quiet origin 2>nul
    git pull --quiet --ff-only 2>nul
) else if exist "venv\Scripts\python.exe" (
    echo [OrderDesk] Checking for updates from GitHub...
    venv\Scripts\python.exe -m dashboard.core.updater
)

:: 5. Запуск програми (з виводом помилки, якщо вона станеться)
echo [OrderDesk] Starting OrderDesk...
venv\Scripts\python.exe -m dashboard
if errorlevel 1 (
    echo.
    echo ========================================================
    echo [ERROR] Program crashed with error code %errorlevel%.
    echo Check logs in data\error.log or data\app_launcher.log
    echo ========================================================
    echo.
    pause
)
