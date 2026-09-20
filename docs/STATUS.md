# Status (updated 2026-09-20, hackathon hardening)

## WORKING (verified by running it)
- Full loop on the real ROS 2 system: inject -> real failure -> LLM-chosen read-only tools -> evidence-validated diagnosis -> human approval ->
  allowlisted restart -> independent 24-check verification -> HEALTHY.
- **Latency**: warm diagnosis 92.0 s -> 4-11 s (profile in `docs/PERFORMANCE.md`; cause was ~3,000 tokens of model prose per diagnosis).
- **Hero scenario through the real dashboard**: 10/10 consecutive runs, diagnosis median 10.7 s, ask -> recovered median 16.3 s (`benchmarks/hero/`).
- **Random faults through the real dashboard**: 15/15 correct, repaired and verified, 0 inconclusive, 0 timeouts, fault identity audited absent from all
  recorded model inputs (`benchmarks/random/`).
- **Benchmark** (15 runs, 5 faults x 3): 100 % accuracy / repair / verification, median 4.7 s, p95 9.1 s (`benchmarks/results_20260920_052452.*`).
- Readiness: `./run_demo.sh` (cold -> READY in ~42 s, blocking model warm-up), `./demo_preflight.sh` (DEMO READY / NOT READY, exit code), dashboard READY FOR DEMO
  banner + chips + START DEMO gate; MODEL RESPONSE TIMEOUT / retry shown in the timeline.
- Dashboard: numbered stage timeline with live values, incident summary card (measured values only), affected-region highlight, RECOVERY VERIFIED badge.
- Tests: 117 fast (`pytest -m "not ros"`), 19 live-ROS (`-m ros`, all passing on the final code, 3 min 54 s). Also: `scripts/ui_timeout_check.py` drives the real UI against a deliberately stalling fake model server (retry shown, safe stop, nothing repaired).

## PARTIALLY WORKING / KNOWN LIMITS
- Small model (qwen3:4b): can hallucinate a cause; the validator rejects it (no wrong repair) but a run can end inconclusive. Process rules are enforced in code
  because the model otherwise loops. It opens with `get_recent_diagnostics` in nearly every run; sequences are repeatable for a given fault (fixed seed).
- The machine is shared: an unrelated `tinyrdt` evaluation job (2 processes, ~330 % CPU, part of the GPU) ran during the benchmark and most tests. The preflight and the
  benchmark report it; it is never killed. Numbers are therefore pessimistic if anything.
- DDS on WSL2 needs `config/cyclonedds.xml`; discovery can hiccup under heavy load.
- Only `restart_component` is a repair primitive.

## BROKEN
- Nothing known.

## NEXT
- Record a real backup video (`docs/RECORDING.md`).
- Parameter-set repair primitive (allowlisted keys); more models when available.
