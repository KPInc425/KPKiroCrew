#!/usr/bin/env bash
# Kiro Crew gateway control — start / stop / restart / status / logs.
#
# Usage:
#   ./scripts/kirocrew-ctl.sh status     # is it running? show port + health
#   ./scripts/kirocrew-ctl.sh start      # start the gateway (detached)
#   ./scripts/kirocrew-ctl.sh stop       # stop it
#   ./scripts/kirocrew-ctl.sh restart    # stop then start
#   ./scripts/kirocrew-ctl.sh logs       # tail the gateway log
#   ./scripts/kirocrew-ctl.sh model      # show the active Ollama model
#
# The gateway serves the dashboard at http://127.0.0.1:5476.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"
GATEWAY="$VENV/bin/kirocrew"
LOG=/tmp/kiro-gw.log
PORT=5476
GATEWAY_PATTERN="kirocrew gateway"

health() {
  curl -s --max-time 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || echo ""
}

is_running() {
  ps aux 2>/dev/null | grep "$GATEWAY_PATTERN" | grep -v grep >/dev/null
}

status() {
  if is_running; then
    local pid port
    pid=$(ps aux 2>/dev/null | grep "$GATEWAY_PATTERN" | grep -v grep | awk '{print $2}' | head -1)
    port=$(ss -tlnp 2>/dev/null | grep ":$PORT " | awk '{print $4}' | head -1 || echo "?")
    echo "✅ Kiro Crew gateway is RUNNING (pid $pid) on http://127.0.0.1:$PORT"
    echo "   Health: $(health)"
  else
    echo "❌ Kiro Crew gateway is NOT running."
    echo "   Start it: $0 start"
  fi
}

start() {
  if is_running; then
    echo "Already running. Use: $0 restart"
    return 0
  fi
  echo "Starting Kiro Crew gateway..."
  setsid nohup "$GATEWAY" gateway --no-open >"$LOG" 2>&1 < /dev/null &
  echo "Waiting for it to come up on :$PORT ..."
  for _ in $(seq 1 20); do
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      echo "✅ Up. Dashboard: http://127.0.0.1:$PORT"
      echo "   Logs: $LOG"
      return 0
    fi
    sleep 1
  done
  echo "⚠️  Not responding yet — check logs: tail $LOG"
  return 1
}

stop() {
  if ! is_running; then
    echo "Not running."
    return 0
  fi
  echo "Stopping Kiro Crew gateway..."
  pkill -f "$GATEWAY_PATTERN" 2>/dev/null || true
  sleep 2
  if is_running; then
    echo "  Still running, sending SIGKILL..."
    pkill -9 -f "$GATEWAY_PATTERN" 2>/dev/null || true
    sleep 1
  fi
  echo "✅ Stopped."
}

restart() {
  stop
  start
}

model() {
  python3 -c "import json;c=json.load(open('$HOME/.kiro/crew/config.json'))['agent']['openai_compatible'];print('Ollama provider enabled:', c['enabled']);print('Model:', c['model'])"
}

case "${1:-status}" in
  status) status ;;
  start)  start ;;
  stop)   stop ;;
  restart) restart ;;
  logs)   tail -f "$LOG" ;;
  model)  model ;;
  *)
    echo "Unknown command: $1"
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | grep -E '^\s*\./' || true
    echo "Commands: status | start | stop | restart | logs | model"
    exit 1
    ;;
esac
