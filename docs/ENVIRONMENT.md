# Environment (inspected 2026-09-19)

| Item | Found | Notes |
|---|---|---|
| OS | Ubuntu 26.04.1 LTS on WSL2 (kernel 6.18, mirrored networking) | 12 cores, 12 GB RAM |
| ROS 2 | **lyrical** at `/opt/ros/lyrical` | RMW: `rmw_cyclonedds_cpp` (only RMW installed) |
| Python | 3.14.4 (`/usr/bin/python3`) | ROS python libs are built for 3.14 |
| rclpy / tf2_ros | both import fine | also `diagnostic_msgs`, `sensor_msgs`, `nav_msgs`, `lifecycle_msgs` |
| Node / npm | v22.23.2 / 10.9.8 | |
| Docker | **not installed** | not needed — everything runs natively |
| Ollama | **not in WSL**; installed on Windows host (v0.34.2) | started via WSL interop (`ollama.exe serve`); mirrored networking makes it reachable at `localhost:11434` |
| Models | `qwen3:4b` (tools + thinking), `llama3.2:3b` (tools) | **qwen3:4b** chosen: correct tool calls, ~50 tok/s warm, ~7 s per agent step on RTX 4050 6 GB |
| GPU | RTX 4050 Laptop, 6 GB | used by Ollama on the Windows side |
| Existing repo | none — `~/robotops` created fresh, `git init` | |

## Consequences for the architecture

* **No Docker** → demo robot runs as plain rclpy processes under a small Python supervisor.
* **Ollama is a Windows process** → `scripts/start_ollama.sh` launches `ollama.exe serve` if `localhost:11434` is not answering.
  If Ollama is unreachable the backend still runs; investigations report `LLM unavailable` instead of crashing.
* **Small model (4B)** → the agent loop keeps tool schemas small, validates every tool call, caps steps, and
  validates the final diagnosis against the evidence ledger in code (the model cannot cite evidence that does not exist).
* **LangGraph not installed** → the agent is a small explicit state machine (`backend/agent/graph.py`), no extra dependency.
* **Python venv**: `.venv` created with `uv venv --system-site-packages -p /usr/bin/python3` so it sees the system `rclpy`;
  only `fastapi`, `uvicorn`, `httpx`, `pydantic` are added.
* **ROS isolation**: all RobotOps/demo processes use `ROS_DOMAIN_ID=73` and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`
  so they never collide with other ROS systems on the network (the machine talks to a Raspberry Pi over DDS).
