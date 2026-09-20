# Status (updated 2026-09-20, hackathon hardening)

## WORKING (verified by running it)
- Full loop on the real ROS 2 system: inject -> real failure -> LLM-chosen read-only tools -> evidence-validated diagnosis -> human approval ->
  allowlisted restart -> independent 24-check verification -> HEALTHY.
- **Latency**: warm diagnosis 92.0 s -> 4-11 s (profile in `docs/PERFORMANCE.md`; cause was ~3,000 tokens of model prose per diagnosis).
- **Hero scenario through the real dashboard**: final acceptance on merged `main` after a cold `run_demo.sh` (model force-unloaded first): **10/10 consecutive runs**, 24/24 verification each,
  0 timeouts, diagnosis median 10.6 s (7.5-11.3), ask -> recovered median 16.4 s (max 17.1) (`benchmarks/hero/hero_acceptance_20260920_0636.json`). Earlier: 10/10, then a 20/20 soak and 5/5 after the last fixes (`benchmarks/hero/`).
- **Random faults through the real dashboard**: 15/15 correct, repaired and verified, 0 inconclusive, 0 timeouts, fault identity audited absent from all
  recorded model inputs (`benchmarks/random/`).
- **Benchmark** (15 runs, 5 faults x 3): 100 % accuracy / repair / verification, median 4.7 s, p95 9.1 s (`benchmarks/results_20260920_052452.*`).
- Readiness: `./run_demo.sh` (cold -> READY in ~42 s, blocking model warm-up), `./demo_preflight.sh` (DEMO READY / NOT READY, exit code), status bar with readiness
  indicators + Start demo gate (also the recovery action for a cold model / lost discovery); model timeout / retry shown in the event stream.
- Dashboard (console layout, see `docs/UI_CLEANUP.md`): System | ROS graph | Investigation event stream, status bar, root cause / proposed action / verification with measured summary, query box at the bottom, collapsible Demo controls. No page scroll at 1366x768 or 1440x900.
- Tests: 142 fast (`pytest -m "not ros"`; 124 behaviour + 18 documentation checks), 19 live-ROS (`-m ros`, all passing on the final code, 3 min 54 s). Also: `scripts/ui_timeout_check.py` drives the real UI against a deliberately stalling fake model server (retry shown, safe stop, nothing repaired).

## RELIABILITY INCIDENTS FOUND BY SOAK TESTING (all with evidence in the repo)
- **ROS client executor crash** (found 06:21): the backend's rclpy executor died with `cannot use Destroyable because destruction was requested`
  (a race between `sample_topic` destroying subscriptions and the executor); every health card went UNKNOWN and START DEMO stayed disabled until a restart.
  Fixed: the spin loop now survives and counts such races (`RosClient.spin_errors`), and reports the client broken only after ~4 s of continuous errors.
  My first version of that fix had its own bug (the loop exited because it was tied to a flag set after the thread started, so the client saw the graph but received no data);
  found by a 40-iteration stress test, fixed, and a regression test now reproduces the real start ordering. The race itself did not re-trigger in the stress test, so the
  survival path is covered by unit tests, not a live reproduction.
- **One unexplained failure**: in one hero run the dashboard's health read FAILED right after RECOVERY VERIFIED (24/24 checks, correct diagnosis). It did not reproduce in 24
  further recoveries (20 UI hero runs + 4 API probes). The dashboard health log (`logs/backend.log`, `[health ...]` lines) and the hero harness now record the component details if it
  recurs. Across ~36 UI hero runs on the final code: 35 fully clean, 1 with that anomaly. The monitor's rate estimator was also made more responsive after restarts.

## PARTIALLY WORKING / KNOWN LIMITS
- Small model (qwen3:4b): can hallucinate a cause; the validator rejects it (no wrong repair) but a run can end inconclusive. Process rules are enforced in code
  because the model otherwise loops. It opens with `get_recent_diagnostics` in nearly every run; sequences are repeatable for a given fault (fixed seed).
- The machine is shared: an unrelated CPU/GPU-heavy evaluation job (2 processes, ~330 % CPU, part of the GPU) ran during the benchmark and most tests. The preflight and the
  benchmark report it; it is never killed. Numbers are therefore pessimistic if anything.
- DDS on WSL2 needs `config/cyclonedds.xml`; discovery can hiccup under heavy load.
- Only `restart_component` is a repair primitive.

## BROKEN
- Nothing known.

## NEXT
- Record a real backup video (`docs/RECORDING.md`).
- Parameter-set repair primitive (allowlisted keys); more models when available.
