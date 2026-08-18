#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "$$" > "$ROOT/data/bot.pid"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"
exec "$ROOT/supervisor.sh"
