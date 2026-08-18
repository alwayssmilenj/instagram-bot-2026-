#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"
mkdir -p "$ROOT/logs" "$ROOT/data"

cp "$ROOT/knightbot.service" "$HOME/.config/systemd/user/knightbot.service"

systemctl --user daemon-reload
systemctl --user enable knightbot.service
systemctl --user restart knightbot.service

# Enable lingering so service runs 24/7 even after closing terminal/logging out
loginctl enable-linger "$USER" 2>/dev/null || true

echo "================================================="
echo "✅ KnightBot 24/7 Local Private Host Configured!"
echo "Status: $(systemctl --user is-active knightbot.service)"
echo "View live logs: journalctl --user -u knightbot.service -f"
echo "Or check status: systemctl --user status knightbot.service"
echo "================================================="
