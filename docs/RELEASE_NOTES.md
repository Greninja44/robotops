# RobotOps v1.0.0 (feature-frozen demo)

An AI reliability engineer for ROS 2: it investigates a failing ROS 2 system with read-only tools chosen by a local LLM, grounds its diagnosis in numbered evidence
that code validates, proposes an allowlisted repair, waits for human approval, and independently verifies recovery.

## Included
- Backend (FastAPI): agent state machine, evidence ledger and validator, 13 read-only rclpy tools, approval gate, two guarded repair primitives (`restart_component`, `set_parameter` over two allowlisted parameters), audit log, independent verifier, incident memory (context only, never evidence).
- Demo robot: 7 rclpy nodes (one lifecycle-managed) with 10 injectable faults (controller crash, LiDAR failure, TF failure, topic mismatch, speed limit misconfig, node crash, commander stall, odometry stall, sensor drift, lifecycle stall) and a hidden random fault.
- Dashboard (React): system health, live ROS graph, evidence-first investigation stream, approve/reject, verification summary.
- Tooling: `scripts/setup.sh`, `run_demo.sh`, `demo_preflight.sh`, `scripts/verify_demo.sh`, `scripts/learn_manifest.py`, benchmark runner, UI harnesses (hero, random, timeout, states, recording).
- CI (fast tests and dashboard build), documentation (README, SETUP, DESIGN, PERFORMANCE, RECORDING, SUBMISSION_AUDIT).

## Measured at v1.0.0 submission (demo robot, one laptop, `qwen3:4b`, 5 faults)
- Hero scenario through the dashboard: 10/10 consecutive runs; diagnosis median 10.6 s; question to recovery median 16.4 s; 24/24 checks each; 0 model timeouts.
- Random hidden fault through the dashboard: 15/15 diagnosed, repaired and verified on the five-fault acceptance set.
- Clean-state benchmark (5 faults × 3): 15/15; diagnosis median 4.7 s.

## Since v1.0.0 (2026-09-29, post-submission, feature freeze lifted)
- **Second guarded repair primitive**: `set_parameter` (`base_controller.cmd_vel_topic` only), alongside `restart_component`; same evidence/approval/verification gates. Verified directly against the live ROS node and through the agent pipeline; in real runs the model still defaults to `restart_component` (see README's Fault scenarios).
- **Manifest learning**: `scripts/learn_manifest.py` derives `demo_robot/manifest.json` from a live observation instead of it being entirely hand-written; run for real, it found a real gap (a declared parameter the hand-written version had missed).
- **Two new faults**: `commander_stall`, `odometry_stall` - both diagnosed correctly by the real model on the first real run, using only the existing tools. Not yet part of the acceptance/benchmark numbers above, which predate them.
- **Incident memory**: `backend/agent/memory.py` offers the model a compact summary of what it has diagnosed before, as context only - `diagnosis.validate` has no notion of memory, so a diagnosis still needs its own evidence IDs. Verified with two genuine model runs; the second run's actual system prompt was read back to confirm it names the past component and outcome. `GET /api/incidents` exposes it.
- **Lifecycle-managed node** (2026-09-30): a 7th demo component (`safety_monitor`), a real `rclpy.lifecycle.LifecycleNode` - the same managed-node pattern Nav2's own safety-critical nodes use. New 9th fault (`lifecycle_stall`) and 13th tool (`inspect_lifecycle_state`). See docs/STATUS.md for the real bug found and fixed while adding it (a lifecycle-infrastructure topic was wrongly flagged as an anomaly on a healthy robot). Ollama was unreachable for part of this round (a Windows-side install/location change, unrelated to this session's code); verified directly against live ROS instead of with a real model run - noted honestly, not hidden.
- **Lifecycle-state manifest learning** (2026-10-01): `scripts/learn_manifest.py` now learns the `"lifecycle"` section too (whatever state a lifecycle node is observed in while healthy is the expectation, same reasoning as `min_rate_hz`); run against this repo's robot, it reproduces the hand-written section byte for byte.
- **Ollama fixed, installed natively** (2026-10-01): the Windows-host passthrough had broken (install location moved); installed Ollama natively inside WSL instead (no root available, so the release tarball was extracted into a user directory rather than running the official installer) and pulled `qwen3:4b`, which now runs on the machine's GPU. Verified end to end: `./run_demo.sh` READY and `./scripts/verify_demo.sh --full` 24/24 with a real model-driven diagnosis, approval, repair and verification.
- **Second allowlisted parameter** (2026-10-01): `max_wheel_speed` (declared on `base_controller` since the manifest-learning gap found it, but previously unused - dead config) is now actually wired into wheel-velocity clamping, and a new 10th fault, `speed_limit_misconfig`, misconfigures it (node stays alive and correctly subscribed, but the robot visibly crawls; only `inspect_parameters` catches it, since the mismatch is a value, not a missing endpoint). `set_parameter base_controller` now resets both allowlisted parameters (`cmd_vel_topic`, `max_wheel_speed`) to canonical in one call, since the model names the component, never which parameter drifted.
- Tests: 192 fast (173 behaviour + 19 documentation checks) and 24 live-ROS (1 new this round).

## Known limitations
Demo topology (not hardware); two narrow repair primitives (no arbitrary parameter or hardware fault); small model can be inconclusive; small samples on one machine; five of the ten faults not yet in the measured acceptance/benchmark numbers; incident memory persists across investigations until `logs/incidents.jsonl` is cleared (not reset by `./scripts/reset_demo.sh`); ROS 2 Lyrical on Linux/WSL2 only; no LICENSE file yet.
See the README's Limitations section.

## Publishing this release (owner's decision)
```bash
gh release create v1.0.0 --title "RobotOps v1.0.0" --notes-file docs/RELEASE_NOTES.md docs/media/hero-demo.mp4
```
