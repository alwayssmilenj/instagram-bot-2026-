@echo off
setlocal enabledelayedexpansion

title KnightBot Instagram — All-In-One Windows Installer

echo =====================================================================
echo 👑  KNIGHTBOT INSTAGRAM — ALL-IN-ONE WINDOWS INSTALLER
echo =====================================================================

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.10+ is not detected in your PATH!
    echo Please download and install Python from https://www.python.org/downloads/
    echo Make sure to check the box "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Create Directory Structure
echo [1/6] Creating project folders...
if not exist "data" mkdir data
if not exist "session" mkdir session
if not exist "logs" mkdir logs
if not exist "temp" mkdir temp
if not exist ".browsers" mkdir .browsers
if not exist "data\anime-stickers" mkdir data\anime-stickers

:: 3. Configure .env file
echo [2/6] Configuring environment settings...
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
    ) else (
        type nul > .env
    )
    echo Created .env configuration from template.
)

:: 4. Build Virtual Environment
echo [3/6] Building Python virtual environment in .venv...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

:: 5. Install Dependencies & Chromium
echo [4/6] Installing Python packages and browser dependencies...
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip setuptools wheel
pip install --quiet -r requirements.txt
pip install --quiet playwright
playwright install chromium

:: 6. Initialize Database
echo [5/6] Initializing SQLite database schema...
python -c "from lib.database import Database; db = Database(); print('Database tables and settings initialized.')" 2>nul

:: 7. Run Bot Self-Check
echo [6/6] Running offline self-check...
python index.py --check

echo.
echo =====================================================================
echo 🎉 KNIGHTBOT IS 100%% READY TO USE ON WINDOWS!
echo =====================================================================
echo.
echo 👉 Next Steps:
echo    1. Open .env in Notepad to verify your IG_USERNAME and IG_PASSWORD
echo    2. Double-click run.bat to start the bot anytime!
echo.
echo =====================================================================
pause
