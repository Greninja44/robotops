#!/usr/bin/env bash
# Restore every demo component to its healthy, canonical configuration.
source "$(dirname "$0")/env.sh"
curl -sf -m 30 -X POST http://127.0.0.1:8766/reset >/dev/null && echo "demo robot reset" || {
  echo "supervisor not reachable - starting fresh"; "$(dirname "$0")/start_demo.sh"; }
