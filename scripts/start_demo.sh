#!/usr/bin/env bash
# Start the demo robot (supervisor + 6 ROS nodes) in the background.
set -eo pipefail
source "$(dirname "$0")/env.sh"
PIDFILE="$ROBOTOPS_ROOT/logs/supervisor.pid"
mkdir -p "$ROBOTOPS_ROOT/logs"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "demo robot already running (pid $(cat "$PIDFILE"))"; exit 0
fi
nohup /usr/bin/python3 "$ROBOTOPS_ROOT/demo_robot/supervisor.py" > "$ROBOTOPS_ROOT/logs/supervisor.log" 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 30); do
  curl -sf -m 1 http://127.0.0.1:8766/status >/dev/null && { echo "demo robot started (pid $(cat "$PIDFILE"))"; exit 0; }
  sleep 0.3
done
echo "demo robot failed to start; see logs/supervisor.log" >&2; exit 1
