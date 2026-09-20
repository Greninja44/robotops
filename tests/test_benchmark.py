"""Benchmark maths (pure functions): rates, percentiles, timestamped non-overwriting output."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_benchmark", Path(__file__).resolve().parents[1] / "benchmarks" / "run_benchmark.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def run(ok=True, phase="resolved", t=10.0, timeouts=0, inconclusive=False, healthy=True, verified=True):
    if not healthy:
        return {"baseline_healthy": False}
    return {"baseline_healthy": True, "diagnosis_success": ok, "final_phase": phase, "diagnosis_time_s": t, "total_time_s": t + 6,
            "model_timeouts": timeouts, "inconclusive": inconclusive, "repair_executed": ok, "verification_success": verified and ok,
            "tool_calls": 4, "llm_calls": 5}


def test_nearest_rank_percentile():
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert bench.percentile([5.0], 95) == 5.0
    assert bench.percentile([], 95) is None
    assert bench.percentile(list(range(1, 21)), 50) == 10


def test_summary_reports_every_requested_metric_and_excludes_invalid_runs():
    rs = [run(t=8.0), run(t=10.0), run(t=12.0), run(ok=False, phase="inconclusive", inconclusive=True, t=20.0),
          run(ok=False, phase="error", timeouts=1, t=30.0), run(healthy=False)]
    s = bench.summarize(rs)
    assert s["runs"] == 6 and s["valid_runs"] == 5 and s["invalid_runs_baseline_unhealthy"] == 1
    assert s["diagnosis_accuracy"] == 0.6 and s["inconclusive_rate"] == 0.2 and s["timeout_rate"] == 0.2 and s["error_rate"] == 0.2
    assert s["repair_success_rate"] == 0.6 and s["verification_success_rate"] == 0.6
    assert s["median_diagnosis_time_s"] == 12.0 and s["p95_diagnosis_time_s"] == 30.0 and s["max_diagnosis_time_s"] == 30.0
    assert s["median_tool_calls"] == 4


def test_summary_of_nothing_is_safe():
    s = bench.summarize([run(healthy=False)])
    assert s["valid_runs"] == 0 and s["diagnosis_accuracy"] is None and s["median_diagnosis_time_s"] is None


def test_markdown_states_sample_size_and_load_warning(tmp_path):
    meta = {"started": "t", "model": "m", "protocol": "json", "platform": "p", "machine": {"warnings": ["System load: high"]}}
    rs = [dict(run(), fault="lidar_failure", repeat=1, diagnosed_component="lidar_driver", tools_used=["get_ros_health", "check_tf"],
               host={"loadavg_1m": 5.0, "gpu_util_pct": 60})]
    p = tmp_path / "r.md"
    bench.write_markdown(meta, bench.summarize(rs), rs, p)
    text = p.read_text()
    assert "Sample size: 1 runs (1 valid)" in text and "under load" in text and "lidar_driver" in text
