#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="knightbot.service"
if systemctl --user cat "knightbot.service" >/dev/null 2>&1; then
  UNIT="knightbot.service"
elif systemctl --user cat "jinshi-mds.service" >/dev/null 2>&1; then
  UNIT="jinshi-mds.service"
fi
PID_FILE="$ROOT/data/bot.pid"
LOG_FILE="$ROOT/logs/bot.log"
mkdir -p "$ROOT/data" "$ROOT/logs"

if systemctl --user cat "$UNIT" >/dev/null 2>&1; then
  case "${1:-status}" in
    start)
      systemctl --user start "$UNIT"
      sleep 2
      systemctl --user --quiet is-active "$UNIT"
      echo "KnightBot started by systemd ($UNIT)."
      ;;
    stop)
      systemctl --user stop "$UNIT"
      echo "KnightBot stopped. Boot enablement remains configured."
      ;;
    restart)
      systemctl --user restart "$UNIT"
      sleep 2
      systemctl --user --quiet is-active "$UNIT"
      echo "KnightBot restarted by systemd ($UNIT)."
      ;;
    status)
      systemctl --user status "$UNIT" --no-pager -n 12
      ;;
    logs)
      journalctl --user -u "$UNIT" -f
      ;;
    *)
      echo "Usage: $0 {start|stop|restart|status|logs}"
      exit 2
      ;;
  esac
  exit 0
fi

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "${1:-status}" in
  start)
    if is_running; then echo "jinshi_mds is already running (PID $(cat "$PID_FILE"))."; exit 0; fi
    rm -f "$PID_FILE"
    setsid "$ROOT/supervisor.sh" >>"$ROOT/logs/console.log" 2>&1 </dev/null &
    echo "$!" >"$PID_FILE"
    sleep 3
    is_running && echo "jinshi_mds started (PID $(cat "$PID_FILE"))."
    ;;
  stop)
    if ! is_running; then rm -f "$PID_FILE"; echo "jinshi_mds is not running."; exit 0; fi
    pid="$(cat "$PID_FILE")"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "jinshi_mds stopped."
    ;;
  restart) "$0" stop; "$0" start ;;
  status) is_running && echo "jinshi_mds is running (PID $(cat "$PID_FILE"))." || { echo "jinshi_mds is not running."; exit 1; } ;;
  logs) tail -f "$LOG_FILE" ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}"; exit 2 ;;
esac
