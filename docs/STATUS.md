# Status (updated 2026-09-29: second repair primitive)

## UPDATE 2026-09-29 (post-submission, feature freeze lifted)
- **Second guarded repair primitive**: `set_parameter` (`base_controller.cmd_vel_topic -> /cmd_vel` only, `policies.PARAMETER_FIX`) alongside
  `restart_component`; same evidence gate, approval gate and independent verification. Verified directly against the live ROS node (`ros2 param set`
  equivalent via `RosClient.set_parameters`, real service call, no mock) and through the full agent state machine (`tests/test_agent_flow.py`).
  Honest result from three ad hoc real runs of `topic_misconfig`: `qwen3:4b` chose `restart_component` every time even with `set_parameter` legal and
  described as the less disruptive option - the primitive works, the model does not yet prefer it. Tests 150 fast (was 143; +7 for this feature).
- **Known issue found while validating this change, not caused by it**: `pytest tests -m ros` (the live-ROS suite) reliably fails/hangs in
  `test_ros_integration.py::test_every_tool_returns_valid_structured_result`'s `robot` fixture (`wait_until(healthy, 60, 2)` times out) when run
  through `pytest`, even on an unmodified `main` with a freshly reset, genuinely healthy robot (confirmed with `git stash`). The identical
  reset -> poll-health sequence run as a plain script, outside pytest, succeeds in ~2 s every time. Not yet root-caused; suspected interaction between
  pytest and the auto-loaded ROS ament/launch_testing pytest plugins (`ament_*`, `launch_testing_ros`, all registered via system-site-packages
  entry points) rather than anything in `backend/`. The fast suite (150 tests) and a direct exercise of every tool, including the new
  `set_parameter` repair, against the live robot were used instead to validate this change; the 19 live-ROS tests were not re-verified clean end
  to end this session.
- **Manifest learning**: `scripts/learn_manifest.py` derives `demo_robot/manifest.json` from a live observation of the healthy robot (nodes,
  topics, publishers/subscribers, measured rates, TF edges, declared parameters) instead of it being entirely hand-written. Run in `--merge`
  (default) mode it keeps human-authored prose (`role`, `description`) and prior rate thresholds, and only reports what it cannot determine on its
  own (which node broadcasts which TF edge - tf2 does not expose this reliably; falls back to the prior value if still valid, else `null` + a
  warning, never a guess). Actually run against this repo's demo robot and applied for real: found and added `base_controller.max_wheel_speed`,
  a real declared parameter that the hand-written manifest had missed (harmless gap - it was simply never compared against - now it is). 9 new
  unit tests for the merge/diff logic (`tests/test_learn_manifest.py`); tests 159 fast.
- **Larger fault library**: two new injectable faults, `commander_stall` (`velocity_commander` hangs: `/cmd_vel` silent) and `odometry_stall`
  (`wheel_odometry` hangs: `/odom` silent, `odom->base_link` stale). Both reuse the existing tool/evidence/safety code unchanged - no new tools, no
  policy changes - and are fixed by the existing `restart_component`. Chosen because neither was exercised by any prior fault: `commander_stall`
  makes `base_controller` the node that *complains* (it logs "no velocity commands") while `velocity_commander` is the actual cause, a genuine
  multi-hop trace; `odometry_stall` is the first fault to hit the `odom->base_link` TF edge (`tf_failure` only ever hit `base_link->laser`). Real
  runs with `qwen3:4b` (`scripts/diagnose_cli.py`, not scripted): both correctly diagnosed on the first attempt without ever being told the fault
  set changed, `commander_stall` in 21 s (3 tool calls), `odometry_stall` in 51 s (5 tool calls, one rejected diagnosis needing a second
  corroborating check first) - both repaired and verified 24/24. 2 new live-ROS tests (`test_ros_integration.py`); fault set is now 7 (+ random).
  Not yet part of the measured 15/15 acceptance/benchmark numbers, which predate these two faults.
- **Incident memory**: `backend/agent/memory.py` - a compact, aggregated log of what the agent itself has diagnosed in past investigations
  (component, action, repaired, verified), offered to the model as *context* in the system prompt, never as evidence: `diagnosis.validate` has no
  notion of memory at all, so a diagnosis still needs its own evidence IDs from the current investigation's own ledger. Recording is agent-output
  only - the module never reads the supervisor's fault-scoring file - so it cannot become a side channel for a hidden fault's identity. Found and
  fixed a real bug while adding it: `Agent.run()` calls `memory.record()` unconditionally, so every fast unit test that exercises the agent loop
  was silently appending synthetic incidents to the real `logs/incidents.jsonl` (grew to hundreds of lines from one test run, which is what the
  "compact system prompt" test caught); fixed with an autouse `isolated_incident_memory` fixture (`tests/conftest.py`) so no test can pollute or be
  affected by the real log. Verified for real: two genuine `qwen3:4b` runs (`scripts/diagnose_cli.py`) of `controller_crash`, then read the actual
  system prompt sent for a follow-up investigation and confirmed it names `base_controller` and the past outcome. `GET /api/incidents` exposes the
  raw log and the summary. 9 new tests (`tests/test_memory.py`) plus one full two-investigation integration test; tests 168 fast.


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
- Tests: 168 fast (`pytest -m "not ros"`; 149 behaviour + 19 documentation checks), 21 live-ROS (`-m ros`, 2 new for the fault library, not yet re-verified clean end to end - see the known pytest/ROS-plugin issue above). Also: `scripts/ui_timeout_check.py` drives the real UI against a deliberately stalling fake model server (retry shown, safe stop, nothing repaired).

## RELIABILITY INCIDENTS FOUND BY SOAK TESTING (all with evidence in the repo)
- **ROS client executor crash** (found 06:21): the backend's rclpy executor died with `cannot use Destroyable because destruction was requested`
  (a race between `sample_topic` destroying subscriptions and the executor); every health card went UNKNOWN and START DEMO stayed disabled until a restart.
  Fixed: the spin loop now survives and counts such races (`RosClient.spin_errors`), and reports the client broken only after ~4 s of continuous errors.
  My first version of that fix had its own bug (the loop exited because it was tied to a flag set after the thread started, so the client saw the graph but received no data);
  found by a 40-iteration stress test, fixed, and a regression test now reproduces the real start ordering. The race itself did not re-trigger in the stress test, so the
  survival path is covered by unit tests, not a live reproduction.
- **Graph edges vanished at the proposal stage** (found 09:57 while re-capturing `docs/screenshots/states/`): once the diagnosis marked nodes as involved, the ROS graph lost all its edges
  (DOM: 12 `.react-flow__edge` before, 0 after; reproduced in 2 of 2 captures; it did not occur in the recorded hero run, so it is timing dependent). Cause: the graph nodes are rebuilt when marks change and
  React Flow drops the measured handle positions, so it stops drawing edges. Fixed in `GraphPanel.tsx` by asking React Flow to re-measure after each rebuild; with the fix all 12 edges persist through every state
  (checked in a full state capture) and 2/2 hero runs passed. No automated UI regression test covers this; the check is `scripts/ui_states.py`.
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
