#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --requirement "$ROOT/requirements.txt"
"$ROOT/.venv/bin/python" -m playwright install chromium
chmod 600 "$ROOT/.env"
"$ROOT/.venv/bin/python" "$ROOT/index.py" --check

echo
echo "Setup complete. Start with: $ROOT/run.sh"
