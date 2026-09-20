# Setup and reproducibility

Everything needed to run RobotOps from a fresh clone. Nothing here depends on the author's machine.

## What you need

| Requirement | Tested with | Why / notes |
|---|---|---|
| Linux (native or WSL2) | Ubuntu 26.04 on WSL2 | RobotOps is not tested on macOS or Windows-native |
| **ROS 2 Lyrical** with `rclpy`, `tf2_ros`, `rmw_cyclonedds_cpp` | `/opt/ros/lyrical` | the demo robot and the backend's ROS client are real rclpy processes. Other distros were not tested; set `ROBOTOPS_ROS_SETUP` to try one |
| Python | 3.14 (the interpreter your ROS install was built for) | the venv is created with `--system-site-packages` so it sees `rclpy` |
| Node.js 20+ and npm | Node 22 | builds the dashboard (`frontend/dist`); the browser needs no Node |
| [Ollama](https://ollama.com) with **`qwen3:4b`** | Ollama 0.34, RTX 4050 6 GB | `ollama pull qwen3:4b` (~2.5 GB). CPU-only works but each model call is several times slower |
| A modern browser | Chromium/Chrome | dashboard at `http://127.0.0.1:8000` |

No Docker, database, cloud service or API key is used. The LLM runs locally; no data leaves the machine.

## Install

```bash
git clone https://github.com/Greninja44/robotops.git   # or the URL you were given
cd robotops
./scripts/setup.sh        # checks prerequisites, creates .venv, installs requirements.txt, npm install, pulls the model if Ollama is local
./run_demo.sh             # starts Ollama (if needed), warms the model, starts robot, builds the dashboard, starts the backend, runs the preflight
./demo_preflight.sh       # prints "ROBOTOPS DEMO READY" (exit 0) or "DEMO NOT READY" plus the reason (exit 1)
```

Manual equivalent of `setup.sh`:

```bash
source /opt/ros/lyrical/setup.bash
uv venv --system-site-packages -p "$(command -v python3)" .venv     # or: python3 -m venv --system-site-packages .venv
uv pip install -p .venv/bin/python -r requirements.txt              # or: .venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm install)
ollama pull qwen3:4b
```

## What the scripts set up

`scripts/env.sh` is sourced by every RobotOps process:

| Variable | Value | Reason |
|---|---|---|
| `ROS_DOMAIN_ID` | `73` (override with `ROBOTOPS_DOMAIN_ID`) | keeps RobotOps away from other ROS systems on the network |
| `ROS_AUTOMATIC_DISCOVERY_RANGE` | `LOCALHOST` | same |
| `RMW_IMPLEMENTATION` | `rmw_cyclonedds_cpp` | the tested RMW |
| `CYCLONEDDS_URI` | `file://<repo>/config/cyclonedds.xml` | see the WSL2 note below |
| `PYTHONPATH` | repo root | `backend`, `demo_robot` are imported as packages |

## Configuration (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `ROBOTOPS_ROS_SETUP` | `/opt/ros/lyrical/setup.bash` | ROS 2 setup script to source |
| `ROBOTOPS_MODEL` | `qwen3:4b` | Ollama model used by the agent |
| `ROBOTOPS_OLLAMA_URL` | `http://127.0.0.1:11434` | where Ollama listens |
| `ROBOTOPS_LLM_TIMEOUT` | `60` | seconds per model request (one retry, then a safe stop) |
| `ROBOTOPS_NUM_CTX` | `6144` | context window requested from Ollama |
| `ROBOTOPS_MAX_TOKENS` | `320` | cap on tokens per decision |
| `ROBOTOPS_KEEP_ALIVE` | `60m` | how long Ollama keeps the model resident |
| `ROBOTOPS_PROTOCOL` | `json` | `json` = grammar-constrained decisions (default, fast); `tools` = Ollama native tool-calling (the first, slower version) |
| `ROBOTOPS_THINK` | `0` | `1` lets qwen3 emit its (discarded) thinking; slower, same diagnoses in our comparison |
| `ROBOTOPS_SUPERVISOR_PORT` / `ROBOTOPS_SUPERVISOR_URL` | `8766` / `http://127.0.0.1:8766` | demo-robot process supervisor (restart / fault injection) |
| `ROBOTOPS_API` | `http://127.0.0.1:8000` | used by scripts and tests to reach the backend |
| `ROBOTOPS_AUDIT_PROMPTS` | unset | `1` records every model input in the investigation log, used by the "fault identity never reaches the model" audit |

Ports used (all `127.0.0.1`): 8000 backend + dashboard, 8766 demo supervisor, 11434 Ollama.

## Platform notes

* **WSL2 and DDS.** With WSL2 mirrored networking, IP-fragmented UDP datagrams are dropped, so any DDS sample larger than ~1.4 KB
  (a `LaserScan`, for example) never arrives between processes. `config/cyclonedds.xml` makes Cyclone fragment at the DDSI layer
  (`MaxMessageSize 1400B`, `FragmentSize 1200B`) and shortens the participant lease to 3 s so a crashed node leaves the graph quickly.
  On native Linux the file is harmless.
* **Ollama on the Windows host.** If there is no `ollama` binary inside WSL, `scripts/start_ollama.sh` looks for
  `ollama.exe` under `/mnt/c/Users/*/AppData/Local/Programs/Ollama/` and starts it through WSL interop; mirrored networking exposes it on
  `localhost:11434`. If Ollama is already reachable it does nothing. Point `ROBOTOPS_OLLAMA_URL` at any other Ollama server.
* **Model availability.** `./run_demo.sh` aborts with a clear message if Ollama or the model is missing; the backend itself still
  starts without the model and reports `LLM unavailable` instead of crashing.
* **Shared GPU / busy machine.** A competing GPU or CPU job makes model calls slower. The preflight and the benchmark report competing load.

## Verify the installation

```bash
./demo_preflight.sh                                   # environment + services + model + ROS graph
.venv/bin/python -m pytest tests -m "not ros"         # 124 fast tests, about 10 seconds, no ROS or model needed
./scripts/start_demo.sh && .venv/bin/python -m pytest tests -m ros    # 19 live tests against the running demo robot (~4 min)
./scripts/verify_demo.sh --full                       # end-to-end check incl. a complete LLM diagnose -> approve -> verify loop
(cd frontend && npx tsc -b && npm run lint && npm run build)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DEMO NOT READY` with a red line | the line names the failing check; `./demo_preflight.sh` is safe to re-run |
| Dashboard status bar says the model is cold | press **Demo controls → Start demo**: it resets the robot, waits for discovery and warms the model |
| `import rclpy` fails inside `.venv` | the venv was created without `--system-site-packages`, or with a different Python than ROS was built for; delete `.venv` and re-run `./scripts/setup.sh` |
| Nodes visible in one terminal but not in the backend | `ROS_DOMAIN_ID`/`CYCLONEDDS_URI` differ; source `scripts/env.sh` in that terminal |
| Stop everything | `./scripts/stop_all.sh` (Ollama is left running) |
