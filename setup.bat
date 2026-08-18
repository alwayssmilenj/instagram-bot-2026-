@echo off
setlocal enabledelayedexpansion

echo ================================================================
echo ⚡ KnightBot Instagram — Windows Native Installer
echo ================================================================

:: Check for Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.10+ from https://python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

:: Create directories
if not exist "data" mkdir data
if not exist "session" mkdir session
if not exist "logs" mkdir logs
if not exist "temp" mkdir temp
if not exist ".browsers" mkdir .browsers

:: Create .env if missing
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [INFO] Created .env template. Please fill in your IG_USERNAME and IG_PASSWORD.
    ) else (
        type nul > .env
    )
)

:: Create venv
if not exist ".venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv .venv
)

:: Install requirements
echo [INFO] Installing Python packages...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

echo ================================================================
echo 🎉 Windows Setup Complete!
echo 1. Edit .env with your credentials
echo 2. Double-click run.bat to start the bot
echo ================================================================
pause
