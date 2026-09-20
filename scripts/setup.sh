#!/usr/bin/env bash
# One-time setup: checks the prerequisites RobotOps cannot install for you, creates .venv (which must see the
# system rclpy), installs the Python and frontend dependencies, and pulls the model if Ollama is reachable.
#   ./scripts/setup.sh
set -eo pipefail
cd "$(dirname "$0")/.."
ROS_SETUP="${ROBOTOPS_ROS_SETUP:-/opt/ros/lyrical/setup.bash}"
MODEL="${ROBOTOPS_MODEL:-qwen3:4b}"
OLLAMA_URL="${ROBOTOPS_OLLAMA_URL:-http://127.0.0.1:11434}"
fail=0
say() { printf '%s\n' "$*"; }
need() { say "  MISSING  $1"; fail=1; }

say "[1/4] prerequisites"
[[ -f "$ROS_SETUP" ]] && say "  ok       ROS 2 ($ROS_SETUP)" || need "ROS 2 - $ROS_SETUP not found (install ROS 2 Lyrical with rmw_cyclonedds_cpp, or set ROBOTOPS_ROS_SETUP)"
command -v node >/dev/null && say "  ok       node $(node --version)" || need "Node.js 20+ (for the dashboard build)"
command -v npm  >/dev/null || need "npm"
command -v curl >/dev/null || need "curl"
[[ $fail == 0 ]] || { say "Install the missing prerequisites (see docs/SETUP.md) and re-run."; exit 1; }

say "[2/4] Python environment (.venv, sees the system rclpy)"
set +u; source "$ROS_SETUP"; set -u
PY="$(command -v python3)"
python3 -c "import rclpy" 2>/dev/null || { say "  the ROS python3 ($PY) cannot import rclpy - check the ROS install"; exit 1; }
if command -v uv >/dev/null; then
  [[ -d .venv ]] || uv venv --system-site-packages -p "$PY" .venv
  uv pip install -p .venv/bin/python -r requirements.txt
else
  [[ -d .venv ]] || "$PY" -m venv --system-site-packages .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -c "import rclpy, fastapi, httpx; print('  ok       rclpy + fastapi importable from .venv')"

say "[3/4] frontend dependencies"
(cd frontend && npm install --no-audit --no-fund)

say "[4/4] model ($MODEL)"
if curl -sf -m 3 "$OLLAMA_URL/api/tags" | grep -q "\"$MODEL\""; then say "  ok       $MODEL is available at $OLLAMA_URL"
elif command -v ollama >/dev/null; then say "  pulling $MODEL ..."; ollama pull "$MODEL"
else say "  NOTE     no Ollama reachable at $OLLAMA_URL - install Ollama and run:  ollama pull $MODEL"; fi

say; say "Setup finished. Next:  ./run_demo.sh"
