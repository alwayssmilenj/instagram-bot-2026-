#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHALLENGE_FILE="$ROOT/data/instagram-challenge-required"
failures=0
challenge_announced=0

child_pid=""

_cleanup() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}

trap _cleanup SIGINT SIGTERM SIGHUP

while true; do
  if [[ -f "$CHALLENGE_FILE" ]]; then
    if (( challenge_announced == 0 )); then
      printf '%s supervisor: Instagram verification required; network activity paused\n' "$(date -Is)" >&2
      challenge_announced=1
    fi
    sleep 30
    continue
  fi
  challenge_announced=0

  started=$(date +%s)
  "$ROOT/run.sh" &
  child_pid=$!
  wait "$child_pid" 2>/dev/null
  code=$?
  child_pid=""
  runtime=$(( $(date +%s) - started ))

  # Code 0 (clean user shutdown) or SIGINT/SIGTERM exits (130 / 143)
  if (( code == 0 || code == 130 || code == 143 )); then
    printf '%s supervisor: child exited cleanly (code=%s); shutting down\n' "$(date -Is)" "$code" >&2
    break
  fi

  if (( code == 75 || code == 76 )); then
    failures=0
    sleep 2
    continue
  fi
  if (( code == 78 )); then
    touch "$CHALLENGE_FILE"
    chmod 600 "$CHALLENGE_FILE"
    continue
  fi
  if (( runtime >= 300 )); then
    failures=0
    sleep 5
    continue
  fi

  failures=$((failures + 1))
  case "$failures" in
    1) delay=15 ;;
    2) delay=30 ;;
    3) delay=60 ;;
    *) delay=300 ;;
  esac
  printf '%s supervisor: child exited code=%s runtime=%ss; restart in %ss\n' "$(date -Is)" "$code" "$runtime" "$delay" >&2
  sleep "$delay"
done
