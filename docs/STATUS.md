# Status (updated 2026-09-19)

## WORKING (verified by running it, not just by reading code)
- **Full loop on the real ROS 2 system**: inject → real component failure → agent investigates with LLM-chosen tools →
  evidence-validated diagnosis → proposal → human approval → allowlisted repair → independent verification → HEALTHY.
  Exercised through the CLI, the REST API (random hidden fault, wrong/duplicate approvals refused) and the test-suite.
- ROS tool layer (11 read-only tools via rclpy: graph, nodes, topics, publishers/subscribers, measured rates, TF freshness,
  parameters, /diagnostics, /rosout, process status) — structured results, input sanitising, timeouts.
- Evidence ledger + diagnosis validator (cited IDs must exist, ≥2 pieces, ≥1 anomaly about the target, allowlisted action);
  heuristic evidence score derived in code with a visible breakdown.
- Approval registry (one-shot, bound to exact action+target), repair allowlist, JSONL audit log (`logs/audit.jsonl`).
- Post-repair verification (24 checks: nodes, subscriptions, topic rates, TF freshness, diagnostics, odometry actually moving).
- 5 fault types + random (identity hidden from the agent and from the API response).
- FastAPI + WebSocket backend; React/Vite/React Flow dashboard (health bar, live graph, timeline, root cause, approve/reject,
  verification, fault panel, query box); auto-reconnect on backend restart.
- `run_demo.sh`, `stop_demo.sh`, `verify_demo.sh [--full]`, `scripts/{start,stop,reset}_demo.sh`.
- Benchmark runner (`benchmarks/run_benchmark.py`) — see `benchmarks/results*.md` for measured numbers.

## PARTIALLY WORKING / KNOWN LIMITS
- **Model quality**: `qwen3:4b` is the only installed model that completes investigations. `llama3.2:3b` never submitted a
  valid diagnosis in the benchmark (5/5 inconclusive — the safety gate held and no repair was attempted).
  See benchmark files for both.
- **Latency**: ~7–50 s per LLM step on the 6 GB laptop GPU shared with Windows; a full diagnosis takes ~45–170 s.
- **DDS on WSL2**: cross-process messages >1.4 KB are dropped unless Cyclone fragments below the MTU
  (`config/cyclonedds.xml`, applied automatically by `scripts/env.sh`). Under heavy host load or a Wi-Fi flap the
  observer can transiently lose discovery (seen once during a benchmark run: all nodes vanished from the graph mid-investigation).
  Loopback-only Cyclone configs were tried and did not work in this WSL setup.
- Repairs: only `restart_component` on the 6 demo components (by design). `topic_misconfig` is fixed by restarting the
  controller with its canonical configuration, not by a live parameter change.

## BROKEN
- Nothing known at time of writing.

## NEXT
- Second repair primitive (parameter set with allowlisted keys) so `topic_misconfig` can be repaired without a restart.
- Run the benchmark with more repeats and additional models once available.
- Gazebo/MuJoCo visualisation (P3, intentionally not started).
