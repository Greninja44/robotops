#!/usr/bin/env bash
# Stop the demo robot and all of its nodes.
source "$(dirname "$0")/env.sh"
PIDFILE="$ROBOTOPS_ROOT/logs/supervisor.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")"
  for _ in $(seq 1 30); do kill -0 "$(cat "$PIDFILE")" 2>/dev/null || break; sleep 0.3; done
fi
rm -f "$PIDFILE"
# belt and braces: any orphaned demo nodes
pkill -f "$ROBOTOPS_ROOT/demo_robot/nodes.py" 2>/dev/null || true
echo "demo robot stopped"
