#!/usr/bin/env bash
# Start the RobotOps backend (FastAPI) on :8000; serves the built frontend if frontend/dist exists.
set -eo pipefail
source "$(dirname "$0")/env.sh"
PIDFILE="$ROBOTOPS_ROOT/logs/backend.pid"
mkdir -p "$ROBOTOPS_ROOT/logs"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "backend already running"; exit 0; fi
cd "$ROBOTOPS_ROOT"
nohup .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/backend.log 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 40); do curl -sf -m 1 http://127.0.0.1:8000/api/status >/dev/null && { echo "backend started (pid $(cat "$PIDFILE"))"; exit 0; }; sleep 0.5; done
echo "backend failed to start; see logs/backend.log" >&2; exit 1
