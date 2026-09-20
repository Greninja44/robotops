# RobotOps v1.0.0 (feature-frozen demo)

An AI reliability engineer for ROS 2: it investigates a failing ROS 2 system with read-only tools chosen by a local LLM, grounds its diagnosis in numbered evidence
that code validates, proposes an allowlisted repair, waits for human approval, and independently verifies recovery.

## Included
- Backend (FastAPI): agent state machine, evidence ledger and validator, 11 read-only rclpy tools, approval gate, `restart_component` allowlist, audit log, independent 24-check verifier.
- Demo robot: 6 rclpy nodes with 5 injectable faults (controller crash, LiDAR failure, TF failure, topic mismatch, node crash) and a hidden random fault.
- Dashboard (React): system health, live ROS graph, evidence-first investigation stream, approve/reject, verification summary.
- Tooling: `scripts/setup.sh`, `run_demo.sh`, `demo_preflight.sh`, `scripts/verify_demo.sh`, benchmark runner, UI harnesses (hero, random, timeout, states, recording).
- CI (fast tests and dashboard build), documentation (README, SETUP, DESIGN, PERFORMANCE, RECORDING, SUBMISSION_AUDIT).

## Measured (demo robot, one laptop, `qwen3:4b`)
- Hero scenario through the dashboard: 10/10 consecutive runs; diagnosis median 10.6 s; question to recovery median 16.4 s; 24/24 checks each; 0 model timeouts.
- Random hidden fault through the dashboard: 15/15 diagnosed, repaired and verified on the five-fault acceptance set.
- Clean-state benchmark (5 faults × 3): 15/15; diagnosis median 4.7 s.
- Tests: 143 fast (124 behaviour + 19 documentation checks) and 19 live-ROS.

## Known limitations
Demo topology (not hardware); one repair primitive; small model can be inconclusive; measured on five fault types only; small samples on one machine; ROS 2 Lyrical on Linux/WSL2 only; no LICENSE file yet.
See the README's Limitations section.

## Publishing this release (owner's decision)
```bash
gh release create v1.0.0 --title "RobotOps v1.0.0" --notes-file docs/RELEASE_NOTES.md docs/media/hero-demo.mp4
```
