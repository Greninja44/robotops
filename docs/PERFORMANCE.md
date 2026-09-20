# Performance: where the diagnosis time went, and what fixed it

Machine: RTX 4050 Laptop (6 GB, shared with the Windows desktop), WSL2, Ollama 0.34.2 on Windows, `qwen3:4b` (Q4_K_M).
Everything below is measured by `scripts/profile_diagnosis.py` (direct agent runs), `scripts/ui_hero_demo.py` /
`scripts/ui_random_demo.py` (real dashboard, real clicks) and `scripts/latency_breakdown.py`. Raw data:
`benchmarks/profiles/`, `benchmarks/hero/`, `benchmarks/random/`.

## 1. The question: why did a diagnosis take 1-3 minutes?

Profile of one complete controller-crash diagnosis **before any change** (warm model, n=3; `benchmarks/profiles/baseline_warm.json`):

| component | run 1 | run 2 | run 3 | share |
|---|---|---|---|---|
| **model token generation** | 83.9 s | 94.2 s | 91.8 s | **99.8 %** |
| model prompt processing | 0.4 s | 0.4 s | 0.1 s | 0.3 % |
| model load | 0.02 s | 0.13 s | 0.02 s | ~0 |
| ROS tool execution (2 tools) | 0.0 s | 0.0 s | 0.0 s | 0 % |
| agent orchestration | 0.0 s | 0.0 s | 0.0 s | 0 % |
| **diagnosis time** | **84.4 s** | **94.8 s** | **92.0 s** | median **92.0 s** |

Other measured facts about the baseline:

* 2 model calls per diagnosis, producing **2,693 / 3,085 / 3,085 output tokens** (about 1,300-1,550 per call) at ~33 tokens/s.
  Each call needed ~40 tokens of actual decision.
* Cold start (model forcibly unloaded first, n=1): 73.7 s = 7.8 s model load + 65.3 s generation + 0.9 s prompt processing.
* Prompt per call: ~1,900 tokens (system prompt 645 + tool schemas 1,039 + conversation). Prompt processing is **cheap**
  (0.1-0.9 s), so shrinking the prompt was never the lever.
* Inspecting the raw reply: with `think:false`, qwen3:4b still wrote ~3,900 characters of visible reasoning
  ("Okay, let's tackle this problem step by step...") into `content` *before* the tool call. That prose is the entire cost.
* `ollama ps` showed the model at **12 % CPU / 88 % GPU** (4.7 GB) with the 12,288-token context - part of the model spilled out
  of the 6 GB card, which slows every generated token.
* Not the cause: ROS tools (ms), our orchestration, API overhead, prompt size, model load (once).

## 2. What changed (in order of impact)

| change | effect |
|---|---|
| **Grammar-constrained JSON decisions** (Ollama `format` schema): `{"reason_summary","action":"tool"/"diagnose","tool","arguments",...}` - the model still chooses every tool and the diagnosis, but cannot write prose | output tokens per diagnosis 3,085 -> 182 (17x fewer) |
| **Context 12,288 -> 6,144** so the model is 100 % on the GPU (3.2 GB) | tokens/s 33 -> 53 (1.6x) |
| **Compact architecture prompt** generated from the robot manifest (topology: what each node consumes/produces, healthy rates) instead of paragraphs + JSON tool schemas | prompt 1,900 -> ~730 tokens |
| Model kept resident (`keep_alive` 60 min) + warm-up gate in `run_demo.sh`/START DEMO | no cold start during a demo (was 7-8 s load, or 100 s+ after Ollama's 5-minute idle unload) |
| Rate-measurement window capped at 2.5 s (the model always asked for 5 s; measured rates are within 0.2 % at 2 s) | -1.5 s per rate check |
| Same tool not offered twice in a row; shorter `reason_summary`/`root_cause` limits | fewer wasted model calls |

Bare-agent profile after the first three changes (`optimized_v3_warm.json`, warm, n=3): **4.5 / 4.0 / 3.9 s, median 4.0 s** with 3 model calls,
182 output tokens (a **23x** speed-up over 92.0 s). Cold: 12.5 s (6.7 s of it one-time model load).

## 3. The cost of doing the job properly

Making the investigation *reliable* (repeated real runs exposed a small model that loops, repeats itself and hallucinates) added
process rules that cost extra model calls, deliberately:

* at least 2 checks of the model's own (beyond the automatic baseline) before a diagnosis is admitted,
* cited evidence must come from >= 2 different tool calls,
* after a rejected diagnosis the next turn must be a new check.

Resulting latency through the **real dashboard** on the final code (`ui_hero_demo.py`, controller crash, **10 consecutive runs, 10/10 succeeded**,
`benchmarks/hero/hero_final_20260920_0537.json`): diagnosis **median 10.7 s (min 7.7, max 11.0)**, ask -> recovered **median 16.3 s (13.4-16.9)**,
approve -> recovered 5.4 s, START DEMO preparation 8.7 s, every run 24/24 verification checks, 0 model timeouts.
The UI + API overhead between "diagnosis accepted" and "proposal visible" is ~0.2-0.6 s.

Final acceptance on merged `main` after a cold start (`benchmarks/hero/hero_acceptance_20260920_0636.json`, 10 consecutive runs, 10/10): diagnosis **median 10.6 s (7.5-11.3)**,
ask -> recovered median 16.4 s (max 17.1), approve -> recovered 5.4 s, 24/24 checks each, 0 timeouts. This is the figure quoted in the README; the run above is an earlier one of the same kind.

Clean-state benchmark on the generic query *"Diagnose the robot."* (`benchmarks/results_20260920_052452.json`, 15 runs, 5 faults x 3, direct agent,
run while an unrelated job loaded the machine): **15/15 correct, 15/15 repaired and verified, 0 inconclusive, 0 timeouts; diagnosis median 4.7 s,
p95 9.1 s, max 9.1 s**, median 3 tool calls / 3 model calls. It is faster than the hero path because that query leads the model to a 2-check route
(`list_nodes -> list_topics` etc.) instead of the rate measurement the hero query triggers.

Random-fault batch (`benchmarks/random/random_20260920_0454.json`, 15 runs, before the last two trims, 4 s rate window):
diagnosis median **11.0 s** (min 4.5, max 16.0), where the time went (`latency_breakdown.py`, mean per diagnosis):

| | time | share |
|---|---|---|
| model (4.1 calls, 5,411 prompt tokens, 270 output tokens) | 6.4 s | 63 % |
| ROS tools (all of it `measure_topic_rate`, 4.0 s per call at that time) | 3.7 s | 37 % |
| orchestration | 0.0 s | 0 % |

Recovery after approval is dominated by verification: 24 live checks = a 3 s settle + 2 s sampling window = **~5.0 s**.
Shortening the settle time would save ~1.5 s but risks a failed first verification pass (a retry costs 5 s), so it was left alone.

## 4. Goal check

| goal | result |
|---|---|
| median diagnosis < 30 s | **met**: 10.7 s hero path through the UI (10 runs); 11.0 s over 15 random faults (UI); 4.7 s over the 15-run benchmark |
| stretch < 15 s | **met on the measured paths** (hero max 11.0 s; benchmark max 9.1 s; random batch max 16.0 s, i.e. 1 of 15 above 15 s, measured before the last trims) |
| no cold-start latency in a demo | `run_demo.sh` cold-to-READY in 42 s; the query box and fault controls stay locked until the model is warm; if it has gone cold (Ollama unloads after 60 min idle), Start demo warms it (about 8 s) |

## 5. What limits it now, honestly

* ~1.3-2.1 s per model call at ~50 tokens/s, 4-5 calls: the floor for this model on this GPU. A larger model would be slower.
* The GPU and CPU are shared with the Windows desktop and with an unrelated CPU/GPU-heavy evaluation job (about 330 % CPU and a large share of GPU
  utilisation) that was running during most of these measurements, including the 15-run benchmark (its machine check and per-run load are
  recorded in the result file). The numbers quoted here were therefore taken on a *busy* machine; a quiet one should be equal or better, but that was not measured.
* The model has habits: it opens with `get_recent_diagnostics` in 14 of 15 random runs, and with a fixed seed the same fault gives
  the same tool sequence. Tool choice is still model-driven and differs by evidence (e.g. `tf_failure` ends after `check_tf`,
  `topic_misconfig` ends on `inspect_node`), but it is not broadly exploratory.

## Reproduce

```bash
./run_demo.sh
python scripts/profile_diagnosis.py --runs 3 [--cold] --out benchmarks/profiles/my_run.json   # direct agent profile
python scripts/ui_hero_demo.py --runs 10 --stop-on-fail --out benchmarks/hero/my_run.json    # real dashboard, hero path
python scripts/ui_random_demo.py --runs 15 --out benchmarks/random/my_run.json               # random faults
python scripts/latency_breakdown.py benchmarks/random/my_run.json                            # where the time went
```
