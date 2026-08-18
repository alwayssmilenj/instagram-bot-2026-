#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Run $ROOT/setup.sh first."
  exit 1
fi

exec "$ROOT/.venv/bin/python" "$ROOT/index.py" "$@"
