#!/usr/bin/env bash
# Start everything RobotOps needs and only report READY when the demo can start immediately:
#   Ollama -> qwen3:4b loaded + warm-up generation -> demo robot -> dashboard build -> backend -> full preflight.
# Then open http://127.0.0.1:8000 and press START DEMO.
set -eo pipefail
cd "$(dirname "$0")"
mkdir -p logs
source scripts/env.sh

echo "[1/6] Ollama";           ./scripts/start_ollama.sh || { echo "Ollama is required (real LLM). Aborting."; exit 1; }
echo "[2/6] Model load + warm-up generation (qwen3:4b, production options)"
.venv/bin/python - <<'PY' 2>&1 | grep -v ddsi_udp_conn_write
import asyncio, sys
from backend.agent import llm
r = asyncio.run(llm.warmup())
print(f"  warm-up: {'OK' if r['ok'] else 'FAILED'} in {r['seconds']} s" + ("" if r["ok"] else f" - {r.get('error')}"))
sys.exit(0 if r["ok"] else 1)
PY
echo "[3/6] Demo robot";       ./scripts/start_demo.sh
echo "[4/6] Dashboard build"
if [[ ! -d frontend/dist ]] || [[ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]]; then
  (cd frontend && { [[ -d node_modules ]] || npm install --no-audit --no-fund; } && npm run build --silent)
else
  echo "  up to date"
fi
echo "[5/6] Backend";          ./scripts/start_backend.sh
echo "[6/6] Waiting for the backend to finish preparing (model resident, ROS graph discovered)"
for _ in $(seq 1 60); do
  R=$(curl -sf -m 3 http://127.0.0.1:8000/api/readiness || echo '{}')
  if [[ "$(echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print(bool(d.get('infra_ready')) and not d.get('preparing'))" 2>/dev/null)" == "True" ]]; then break; fi
  sleep 1.5
done
echo
if ./demo_preflight.sh; then
  echo
  echo "Open  http://127.0.0.1:8000   and press START DEMO."
  echo "  stop: ./scripts/stop_all.sh    re-check: ./demo_preflight.sh    verify: ./scripts/verify_demo.sh [--full]"
else
  echo
  echo "RobotOps started but is NOT ready - fix the reason above, then re-run ./demo_preflight.sh"
  exit 1
fi
