#!/usr/bin/env bash
# ==============================================================================
#  👑 KnightBot Instagram — Universal Linux & WSL Setup Installer
# ==============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"

echo "================================================================"
echo "⚡ Starting KnightBot Instagram Environment Setup"
echo "📂 Target Directory: $ROOT"
echo "================================================================"

# 1. Detect System Environment (Native Linux vs WSL)
if grep -qi microsoft /proc/version 2>/dev/null; then
    echo "🐧 Detected Environment: Windows Subsystem for Linux (WSL)"
else
    echo "🐧 Detected Environment: Native Linux ($(uname -s) $(uname -m))"
fi

# 2. Check Python Version (Requires Python 3.10+)
PYTHON_BIN=""
for cmd in python3 python python3.12 python3.11 python3.10; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_BIN="$cmd"
            echo "✅ Found suitable Python: $PYTHON_BIN (v$ver)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: Python 3.10 or higher is required. Please install python3."
    exit 1
fi

# 3. Create required runtime directories
mkdir -p "$ROOT/data" "$ROOT/session" "$ROOT/logs" "$ROOT/temp" "$ROOT/.browsers"

# 4. Handle .env configuration safely
if [ ! -f "$ROOT/.env" ]; then
    if [ -f "$ROOT/.env.example" ]; then
        cp "$ROOT/.env.example" "$ROOT/.env"
        echo "📝 Created new .env from template (.env.example)"
    else
        touch "$ROOT/.env"
    fi
fi
chmod 600 "$ROOT/.env" 2>/dev/null || true

# 5. Initialize Python Virtual Environment
if [ ! -d "$ROOT/.venv" ]; then
    echo "📦 Creating isolated Python virtual environment in .venv..."
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

# 6. Upgrade Pip and Install Dependencies
echo "📥 Installing required Python dependencies..."
"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --quiet --requirement "$ROOT/requirements.txt"

# 7. Install Playwright Chromium headless engine
echo "🌐 Installing Playwright Chromium browser engine..."
"$ROOT/.venv/bin/python" -m playwright install chromium || "$ROOT/.venv/bin/python" -m playwright install --with-deps chromium

# 8. Check FFmpeg availability
if command -v ffmpeg >/dev/null 2>&1; then
    echo "✅ FFmpeg audio/video encoder detected."
else
    echo "⚠️  Note: FFmpeg not detected in PATH. Audio/voice features may need ffmpeg (sudo apt install ffmpeg / sudo dnf install ffmpeg)."
fi

# 9. Validate codebase integrity
echo "🔍 Running offline configuration sanity check..."
"$ROOT/.venv/bin/python" "$ROOT/index.py" --check

echo ""
echo "================================================================"
echo "🎉 Setup Complete! KnightBot is ready for action."
echo "================================================================"
echo "👉 Next steps:"
echo "   1. Edit your credentials: nano .env"
echo "   2. Start the bot manually: ./run.sh"
echo "   3. Or host 24/7 in background: ./host_locally.sh"
echo "================================================================"
