#!/usr/bin/env bash
# ==============================================================================
#  👑 KnightBot Instagram — All-In-One Universal Installer (Linux & WSL)
# ==============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"

echo "====================================================================="
echo "👑  KNIGHTBOT INSTAGRAM — ALL-IN-ONE SYSTEM SETUP"
echo "📂  Location: $ROOT"
echo "====================================================================="

# 1. Environment & Architecture Detection
IS_WSL=0
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=1
    echo "🐧 Environment: Windows Subsystem for Linux (WSL)"
else
    echo "🐧 Environment: Native Linux ($(uname -s) $(uname -m))"
fi

# 2. Python 3.10+ Binary Discovery
PYTHON_BIN=""
for candidate in python3 python python3.12 python3.11 python3.10 python3.14; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "0.0")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_BIN="$candidate"
            echo "✅ Python Interpreter: $PYTHON_BIN (v$ver)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: Python 3.10 or higher is required."
    echo "   Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo "   Fedora/RHEL:   sudo dnf install -y python3 python3-pip"
    exit 1
fi

# 3. Create Runtime Folders with Secure Permissions
echo "📁 Initializing project directory structure..."
mkdir -p "$ROOT/data" "$ROOT/session" "$ROOT/logs" "$ROOT/temp" "$ROOT/.browsers" "$ROOT/data/anime-stickers"
chmod 700 "$ROOT/session" "$ROOT/data" 2>/dev/null || true

# 4. Virtual Environment Creation
if [ ! -d "$ROOT/.venv" ] || [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "📦 Building isolated Python virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

# 5. Core Python Dependencies & Package Manager Upgrade
echo "📥 Installing required Python dependencies..."
"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install --quiet --requirement "$ROOT/requirements.txt"
"$ROOT/.venv/bin/python" -m pip install --quiet playwright

# 6. Chromium Engine & System Shared Libraries Installation
echo "🌐 Installing Chromium browser engine & system dependencies..."
if ! "$ROOT/.venv/bin/python" -m playwright install chromium 2>/dev/null; then
    echo "📥 Downloading Chromium with OS dependencies..."
    "$ROOT/.venv/bin/python" -m playwright install --with-deps chromium 2>/dev/null || \
    "$ROOT/.venv/bin/python" -m playwright install chromium
fi

# 7. Media Audio Codec Detection (FFmpeg)
if command -v ffmpeg >/dev/null 2>&1; then
    echo "🎵 FFmpeg Audio DSP: Ready"
else
    echo "⚠️  Note: FFmpeg not found. For voice note audio conversions:"
    echo "   Debian/Ubuntu/WSL: sudo apt install -y ffmpeg"
    echo "   Fedora:            sudo dnf install -y ffmpeg"
fi

# 8. Safe .env Configuration Setup
if [ ! -f "$ROOT/.env" ]; then
    if [ -f "$ROOT/.env.example" ]; then
        cp "$ROOT/.env.example" "$ROOT/.env"
    else
        touch "$ROOT/.env"
    fi
    chmod 600 "$ROOT/.env" 2>/dev/null || true
    echo "📝 Created initial .env configuration."
fi

# Check if credentials are placeholders
NEED_CONFIG=0
if grep -q "your_instagram_username" "$ROOT/.env" 2>/dev/null || ! grep -q "IG_USERNAME=" "$ROOT/.env" 2>/dev/null; then
    NEED_CONFIG=1
fi

if [ "$NEED_CONFIG" -eq 1 ] && [ -t 0 ]; then
    echo ""
    echo "---------------------------------------------------------------------"
    echo "🔑 INSTAGRAM CREDENTIALS SETUP (Press ENTER to skip and edit manually)"
    echo "---------------------------------------------------------------------"
    read -r -p "Enter Instagram Bot Username: " input_user || true
    if [ -n "${input_user:-}" ]; then
        read -r -s -p "Enter Instagram Bot Password: " input_pass || true
        echo ""
        read -r -p "Enter Owner Username (e.g. jinshi): " input_owner || true
        
        sed -i "s/^IG_USERNAME=.*/IG_USERNAME=$input_user/" "$ROOT/.env" 2>/dev/null || echo "IG_USERNAME=$input_user" >> "$ROOT/.env"
        sed -i "s/^IG_PASSWORD=.*/IG_PASSWORD=$input_pass/" "$ROOT/.env" 2>/dev/null || echo "IG_PASSWORD=$input_pass" >> "$ROOT/.env"
        if [ -n "${input_owner:-}" ]; then
            sed -i "s/^OWNER_USERNAME=.*/OWNER_USERNAME=$input_owner/" "$ROOT/.env" 2>/dev/null || echo "OWNER_USERNAME=$input_owner" >> "$ROOT/.env"
            sed -i "s/^OWNER_USERNAMES=.*/OWNER_USERNAMES=$input_owner/" "$ROOT/.env" 2>/dev/null || echo "OWNER_USERNAMES=$input_owner" >> "$ROOT/.env"
        fi
        echo "✅ Credentials saved securely into .env"
    fi
fi

# 9. Database Pre-initialization & Schema Setup
echo "🗄️ Initializing SQLite database schema..."
"$ROOT/.venv/bin/python" -c "from lib.database import Database; db = Database(); print('✅ Database tables & settings initialized successfully.')" 2>/dev/null || true

# 10. Offline Integrity Sanity Check
echo "🔍 Running offline bot self-check..."
"$ROOT/.venv/bin/python" "$ROOT/index.py" --check

echo ""
echo "====================================================================="
echo "🎉 KNIGHTBOT IS 100% READY TO USE!"
echo "====================================================================="
echo "👉 Launch options:"
echo "   • Start interactively in console: ./run.sh"
echo "   • Start 24/7 background host:     ./host_locally.sh"
echo "   • Check live status:              ./botctl.sh status"
echo "   • View real-time logs:            ./botctl.sh logs"
echo "====================================================================="
