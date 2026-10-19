@echo off
setlocal
cd /d "%~dp0"

if exist ".git" (
    echo [OrderDesk] Checking for updates from git...
    git fetch --quiet origin 2>nul
    git pull --quiet --ff-only 2>nul
)

if not exist "venv\Scripts\python.exe" (
    echo [OrderDesk] First run setup: creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Python 3 not found. Please install Python from python.org and add it to PATH.
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip
    if exist "requirements.txt" (
        echo [OrderDesk] Installing dependencies...
        venv\Scripts\python.exe -m pip install -r requirements.txt
    )
)

start "" venv\Scripts\pythonw.exe -m dashboard
exit /b 0
