#!/usr/bin/env bash
# Demo preflight: measures the real system and prints DEMO READY or DEMO NOT READY (exit 0 / 1).
#   ROS env, CycloneDDS config, DDS discovery, expected nodes/topics/TF, backend, frontend, Ollama, model warm-up,
#   GPU/VRAM, system load, background processes, fault injector, repair service, verification subsystem.
# Usage: ./demo_preflight.sh          (backend must be running: ./run_demo.sh)
cd "$(dirname "$0")"
source scripts/env.sh
.venv/bin/python -m backend.readiness "$@" 2>&1 | grep -v "ddsi_udp_conn_write"
exit "${PIPESTATUS[0]}"
