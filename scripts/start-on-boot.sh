#!/usr/bin/env bash
# Boot launcher for the meenow Reachy Mini app: activates the venv, starts the
# daemon (unless one is already running), waits for it, then runs the app.
#
# Intended for crontab:
#   @reboot /home/pi/reachy-mini-meenow/scripts/start-on-boot.sh
#
# Assumes the repo was set up with `python -m venv .venv && pip install -e .`
# and a `.env` file in the repo root (loaded by the app itself). Override
# locations via MEENOW_HOME / MEENOW_VENV / MEENOW_DAEMON_ARGS.

set -u

MEENOW_HOME="${MEENOW_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
MEENOW_VENV="${MEENOW_VENV:-$MEENOW_HOME/.venv}"
DAEMON_URL="${MEENOW_DAEMON_URL:-http://localhost:8000/}"
LOG_DIR="$MEENOW_HOME/logs"
mkdir -p "$LOG_DIR"

exec >>"$LOG_DIR/start-on-boot.log" 2>&1
echo "=== $(date -Is) start-on-boot (home=$MEENOW_HOME) ==="

# shellcheck disable=SC1091
source "$MEENOW_VENV/bin/activate"
cd "$MEENOW_HOME"  # so the app's load_dotenv() finds ./.env

daemon_up() { curl -s -o /dev/null --max-time 2 "$DAEMON_URL"; }

DAEMON_PID=""
if daemon_up; then
    echo "Daemon already running at $DAEMON_URL; not starting another."
else
    echo "Starting reachy-mini-daemon..."
    # shellcheck disable=SC2086
    reachy-mini-daemon ${MEENOW_DAEMON_ARGS:-} >>"$LOG_DIR/daemon.log" 2>&1 &
    DAEMON_PID=$!
fi

# Only stop the daemon if this script started it.
cleanup() {
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        echo "Stopping daemon (pid $DAEMON_PID)."
        kill "$DAEMON_PID"
        wait "$DAEMON_PID" 2>/dev/null
    fi
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 60); do
    daemon_up && break
    if [ -n "$DAEMON_PID" ] && ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        echo "Daemon exited during startup; see $LOG_DIR/daemon.log"
        exit 1
    fi
    sleep 1
done
if ! daemon_up; then
    echo "Daemon did not become reachable at $DAEMON_URL within 60s."
    exit 1
fi
echo "Daemon is up; starting the meenow app."

reachy-mini-meenow >>"$LOG_DIR/app.log" 2>&1
echo "App exited with status $? at $(date -Is)."
