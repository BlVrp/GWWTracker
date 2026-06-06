#!/usr/bin/env bash
# Launch the Window Tracker UI: start the server if needed, then open the dashboard.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT="${WINDOW_TRACKER_UI_PORT:-8765}"
HOST="127.0.0.1"
URL="http://${HOST}:${PORT}/"
SCRIPT="${SCRIPT_DIR}/window-tracker-ui.py"
LOG="${TMPDIR:-/tmp}/window-tracker-ui.log"
PYTHON="$(command -v python3)"

is_up() {
    curl -fsS -o /dev/null --max-time 1 "$URL"
}

if ! is_up; then
    nohup "$PYTHON" "$SCRIPT" --port "$PORT" --host "$HOST" \
        >> "$LOG" 2>&1 < /dev/null &
    disown || true

    for _ in $(seq 1 30); do
        if is_up; then break; fi
        sleep 0.2
    done
fi

# Open in the default browser if a graphical opener is available.
if command -v xdg-open >/dev/null 2>&1; then
    exec xdg-open "$URL"
else
    echo "Window Tracker UI running at $URL"
fi
