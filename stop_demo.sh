#!/usr/bin/env bash
# Stop backend and demo robot (leaves Ollama running).
cd "$(dirname "$0")"
if [[ -f logs/backend.pid ]]; then kill "$(cat logs/backend.pid)" 2>/dev/null || true; rm -f logs/backend.pid; echo "backend stopped"; fi
./scripts/stop_demo.sh
