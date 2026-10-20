@echo off
setlocal
cd /d "%~dp0"

:: Вимикаємо можливі прапорці лінивих імпортів, які можуть конфліктувати з бібліотеками
set PYTHON_LAZY_IMPORTS=
set PYTHONLAZYIMPORTS=
if not defined ORDERDESK_DATA set ORDERDESK_DATA=%~dp0data

:: 1. Первинне налаштування оточення при першому запуску
if not exist "venv\Scripts\python.exe" (
    echo [OrderDesk] First run setup: creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Python 3 not found. Please install Python 3.12 or 3.13 from python.org and add it to PATH.
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip
    if exist "requirements.txt" (
        echo [OrderDesk] Installing dependencies...
        venv\Scripts\python.exe -m pip install -r requirements.txt
        echo [OrderDesk] Installing Playwright Chromium browser...
        venv\Scripts\python.exe -m playwright install chromium
    )
)

:: 2. Автоматична перевірка та завантаження оновлень
if exist ".git" (
    echo [OrderDesk] Checking for updates from git...
    git fetch --quiet origin 2>nul
    git pull --quiet --ff-only 2>nul
) else if exist "venv\Scripts\python.exe" (
    echo [OrderDesk] Checking for updates from GitHub...
    venv\Scripts\python.exe -m dashboard.core.updater
)

:: 3. Запуск інтерфейсу OrderDesk
start "" venv\Scripts\pythonw.exe -m dashboard
exit /b 0
