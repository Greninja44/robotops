# Submission audit

A judge-style read of this repository (2026-09-20), written after the project was feature-frozen. There is no live presentation, so the repository has to answer
the judges' questions on its own. This page lists the questions, where the answers are, what was wrong before this audit, and what is still weak.

## 1. The 30-second questions

| Question | Answer | Where |
|---|---|---|
| What problem does it solve? | Diagnosing a failing ROS 2 robot is a manual, sequential investigation across nodes, topics, TF, diagnostics, logs and controllers. | README → [The problem](../README.md#the-problem) |
| What does it do? | Investigates a failure in a live ROS 2 system, gathers evidence, names a likely root cause, proposes a guarded repair, and independently verifies recovery. | README top; [What RobotOps does](../README.md#what-robotops-does) |
| Where is AI essential? | A local LLM (`qwen3:4b`) chooses the next diagnostic tool, decides what to inspect next, forms the hypothesis and writes the diagnosis. There is no fault-to-tool table: tool sequences differ by evidence. | [Why AI?](../README.md#why-ai) |
| Does it touch a real ROS 2 system? | Yes: `rclpy` tools against a running 6-node demo robot with real DDS traffic; faults are real process failures injected by a supervisor. | [Architecture](../README.md#architecture), `backend/ros_tools/`, `demo_robot/` |
| What prevents hallucinated diagnoses? | The LLM cites evidence IDs; code checks that the IDs exist, come from ≥ 2 different tool calls, include an anomaly about the named component, and that the action is allowlisted. Otherwise: inconclusive, no repair. | [Evidence-grounded diagnosis](../README.md#evidence-grounded-diagnosis), `backend/agent/diagnosis.py` |
| Is the repair autonomous? | The investigation and diagnosis are autonomous; the *state change* is deliberately gated by a human approval (single-use, bound to action + target). | [Safety model](../README.md#safety-model) |
| What are the safety mechanisms? | Read-only tools by default, evidence gate, approval gate, one-primitive allowlist, no shell, audit log, step/turn/timeout caps, independent 24-check verification. | [Safety model](../README.md#safety-model), `backend/safety/`, `tests/test_safety.py` |
| Does it work? | A real, uncut recording; hero 10/10 consecutive runs; random hidden fault 15/15 on the five-fault set; 24/24 verification each. | README top, [Results](../README.md#results) |
| How was it tested? | 124 fast tests and 19 live-ROS tests, a fault-identity-leak test plus prompt audit, soak runs that found and fixed real bugs (documented in [STATUS.md](STATUS.md)). | [Testing](../README.md#testing) |
| How do I run it? | `./scripts/setup.sh`, `./run_demo.sh`, open the dashboard; requirements listed first. | [Quick start](../README.md#quick-start), [SETUP.md](SETUP.md) |

## 2. Rubric mapping (with evidence, without unmeasured claims)

| Criterion | Evidence in the repository |
|---|---|
| Problem clarity | One concrete workflow (the manual `ros2 node/topic/hz`/TF/diagnostics investigation) and one concrete failure (a crashed controller that shows up as "the robot doesn't move"). |
| Technical execution | Real rclpy tools, explicit state machine in which `REPAIRING → RESOLVED` is unrepresentable, single-use approvals, independent verification of the whole robot, 143 tests, measured benchmarks, a profiled 92 s → 10.6 s latency reduction with the cause identified (model prose, not ROS). |
| Meaningful AI | The model makes the sequential decisions (next tool, hypothesis, diagnosis); everything that must be true (measurements, evidence, permissions, repair, verification) is code. The split is explicit in the README and enforced at trust boundaries. |
| Innovation | Evidence-ID-grounded diagnosis validated in code; a repair path with no LLM execution capability; recovery defined by independent re-measurement rather than a command's exit code. |
| UX | A single console: system health, live ROS graph, evidence-first investigation stream, approve/reject, verification summary; status bar states what is happening. |
| Demo | A real recording in the README, a scripted 8-step reproduction, and `scripts/ui_hero_demo.py` that performs it through the real dashboard. |

## 3. What was wrong before this audit, and what changed

| Finding | Fix |
|---|---|
| README opened with a slogan and a problem/solution block, then measured claims, before showing a real demo. | Title, one-sentence definition, the real demo GIF and the recovery flow are now the first screen. |
| README used "100 % accuracy" wording. | Replaced by the measured statements ("15/15 on the five-fault acceptance set") plus a methodology and limits paragraph. |
| README's architecture text still described the first version ("Ollama tool-calling loop") and the diagram did not show trust boundaries. | New Mermaid diagram with the untrusted LLM zone and the five trusted gates; text corrected (grammar-constrained decisions). |
| No "Why AI?" answer; no real structured evidence example. | Added, using the real record of the demo run ([`docs/examples/controller_failure_trace.json`](examples/controller_failure_trace.json)). |
| Nine screenshots and a live-presentation script ("3-minute judging sequence"). | Three captioned screenshots; the script became a neutral 8-step reproduction. |
| No dependency manifest or setup script; the ROS path was hard-coded; setup lived in prose. | `requirements.txt`, `scripts/setup.sh`, `ROBOTOPS_ROS_SETUP`, [SETUP.md](SETUP.md) with every environment variable, port and platform note. |
| No real recording. | `docs/media/hero-demo.gif` / `.mp4` recorded by `scripts/ui_hero_demo.py --video` from an actual run; capture procedure in [RECORDING.md](RECORDING.md). |
| Root directory held two helper scripts beside the entry points. | Moved to `scripts/` (`verify_demo.sh`, `stop_all.sh`). |
| The benchmark files and two docs named an unrelated background job of the author's, and one benchmark file contained an absolute home path. | Redacted to placeholders (a note in the benchmark `.md` says so; no measured value changed). |
| `docs/ENVIRONMENT.md` mentioned an unrelated device on the author's network. | Removed. |
| Links and the "no machine paths" property were unchecked. | `tests/test_docs_links.py` validates every relative link, image and anchor in every Markdown file and scans tracked files for home paths and credential patterns. |
| `frontend/README.md` was the untouched Vite template text. | Replaced by a description of this dashboard. |

## 4. Remaining weaknesses (stated, not hidden)

- **No LICENSE file exists in the repository.** None was added, because choosing licence terms is the owner's decision. Without one, the default is "all rights reserved". See [GITHUB_SUBMISSION.md](GITHUB_SUBMISSION.md).
- **The robot is a simulation of failure modes** (real processes and DDS, no hardware/Gazebo). The tool layer is generic; the healthy manifest and dashboard layout are written for the demo robot.
- **Small samples and one machine.** 10–15 runs per measurement; no confidence intervals; not measured on a quiet machine or another GPU.
- **Small model behaviour.** `qwen3:4b` can misread a value; the validator prevents a wrong repair from running but the run can end inconclusive. Some process rules are enforced in code because the model otherwise loops. For a given fault and seed the tool sequence is repeatable (model-driven, but not broadly exploratory).
- **One repair primitive** (`restart_component`).
- **Git history.** The full development history is preserved, including earlier README versions, the removed unrelated process names in older commits' benchmark files, and the failed early benchmark runs. Nothing was rewritten.
- **Repository visibility.** The repository is private; whoever evaluates it needs access (see [GITHUB_SUBMISSION.md](GITHUB_SUBMISSION.md)).

## 5. Final judge test (README only)

Reading only `README.md` from a fresh clone, a judge can: understand the product from the first screen; see where AI is used and where it is not ([Why AI?](../README.md#why-ai));
see why it is safe (diagram + [Safety model](../README.md#safety-model)); find the evidence that it works ([Results](../README.md#results), raw files linked); reproduce the hero demo
([Reproduce the demo](../README.md#reproduce-the-demo)); find the benchmark methodology (the *Methodology* paragraph and [`benchmarks/`](../benchmarks)); and find the limitations ([Limitations](../README.md#limitations)).
The clean-clone run performed during this audit is recorded in [GITHUB_SUBMISSION.md](GITHUB_SUBMISSION.md#clean-clone-check).
