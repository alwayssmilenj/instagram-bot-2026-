@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run setup.bat first!
    pause
    exit /b 1
)

set PLAYWRIGHT_BROWSERS_PATH=%~dp0.browsers
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1

echo ================================================================
echo ⚡ Starting KnightBot Instagram Bot...
echo ================================================================

.venv\Scripts\python.exe index.py %*

if %errorlevel% neq 0 (
    echo.
    echo Bot stopped with error code %errorlevel%.
    pause
)
