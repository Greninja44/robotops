#!/usr/bin/env bash
# Start everything RobotOps needs: Ollama, demo robot, backend (+ built dashboard). Then open http://127.0.0.1:8000
set -eo pipefail
cd "$(dirname "$0")"
mkdir -p logs

echo "[1/4] Ollama";       ./scripts/start_ollama.sh || echo "  (continuing without LLM: investigations will report 'LLM unavailable')"
echo "[2/4] Demo robot";   ./scripts/start_demo.sh
echo "[3/4] Dashboard build"
if [[ ! -d frontend/dist ]] || [[ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]]; then
  (cd frontend && { [[ -d node_modules ]] || npm install --no-audit --no-fund; } && npm run build --silent)
else
  echo "  up to date"
fi
echo "[4/4] Backend";      ./scripts/start_backend.sh

# warm the model so the first investigation isn't slow (best-effort, in background)
MODEL="${ROBOTOPS_MODEL:-qwen3:4b}"
(curl -s -m 180 "${ROBOTOPS_OLLAMA_URL:-http://127.0.0.1:11434}/api/generate" -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":\"30m\",\"options\":{\"num_predict\":1}}" >/dev/null 2>&1 &) || true

echo
echo "RobotOps is up:  http://127.0.0.1:8000"
echo "  stop:   ./stop_demo.sh      verify:  ./verify_demo.sh      reset robot:  ./scripts/reset_demo.sh"
