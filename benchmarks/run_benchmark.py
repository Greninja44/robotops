#!/usr/bin/env python3
"""Automated RobotOps benchmark (real ROS, real model; nothing mocked).

Before running: a machine check (CPU load, RAM, GPU/VRAM, Ollama, model warm, ROS health) that WARNS when the machine is
busy (use --require-quiet to abort instead). Then, for each fault and repeat:

  reset -> verify healthy baseline -> inject -> "Diagnose the robot." (same query, no hint) -> score against the injected
  fault -> auto-approved repair (benchmark mode, logged as such) -> independent verification -> timings

Results are written to a NEW timestamped file each time (benchmarks/results_<YYYYmmdd_HHMMSS>.json/.md); earlier results are
never overwritten. Every number is measured by this script.

  python benchmarks/run_benchmark.py [--faults a,b] [--repeat 3] [--require-quiet]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
from pathlib import Path

from backend import readiness
from backend.agent import llm, verification
from backend.agent.graph import Agent
from backend.agent.state import Investigation
from backend.ros_tools import logs, supervisor
from backend.ros_tools.client import get_client
from backend.safety.approvals import ApprovalRegistry
from backend.safety.audit import AuditLog

OUT = Path(__file__).resolve().parent
# Ground truth used ONLY for scoring, after the investigation has finished.
EXPECTED_COMPONENT = {
    "controller_crash": "base_controller",
    "lidar_failure": "lidar_driver",
    "tf_failure": "tf_broadcaster",
    "topic_misconfig": "base_controller",
    "node_crash": "obstacle_monitor",
}
QUERY = "Diagnose the robot."


def host_snapshot() -> dict:
    """Host state at the start of a run, so runs disturbed by other workloads are visible in the results."""
    try:
        h = readiness.host_load()
        g = readiness.gpu_info() or {}
        return {"loadavg_1m": h["load1"], "cpus": h["cpus"], "mem_available_mb": h["mem_available_mb"],
                "gpu_util_pct": g.get("util_pct"), "vram_used_mb": g.get("used_mb")}
    except (OSError, KeyError, ValueError):
        return {}


def percentile(vals: list[float], p: float) -> float | None:
    """Nearest-rank percentile (honest for small n: with n=15, p95 is the 15th value)."""
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


async def machine_check(client, require_quiet: bool) -> dict:
    """Print the state of the machine and warn (or abort) if it is under significant load."""
    res = await readiness.run_preflight(client, warm=True, api_url=None)
    print("== machine check")
    for c in res["checks"]:
        if c["id"] in ("ros_client", "discovery", "verification", "ollama", "warmup", "model", "gpu", "load", "background"):
            print(f"   [{c['status']:4s}] {c['label']:<42} {c['detail']}")
    snap = host_snapshot()
    busy = [c for c in res["checks"] if c["id"] in ("load", "background", "gpu") and c["status"] != "pass"]
    if busy:
        print("\n   WARNING: the machine is under load - latency and reliability numbers will be pessimistic:")
        for c in busy:
            print(f"     - {c['label']}: {c['detail']}")
        if require_quiet:
            sys.exit("aborting because --require-quiet was given")
    if not res["infra_ready"]:
        sys.exit(f"tooling not ready, not benchmarking: {res['infra_reason']}")
    return {"warnings": [f"{c['label']}: {c['detail']}" for c in busy], "snapshot": snap, "checks": res["checks"]}


async def wait_healthy(client, timeout=90.0) -> tuple[bool, list]:
    end = time.monotonic() + timeout
    failed = []
    while time.monotonic() < end:
        checks = await asyncio.to_thread(verification.run_checks, client, None)
        failed = [f"{c['check']}: {c['detail']}" for c in checks if not c["passed"]]
        if not failed:
            return True, []
        await asyncio.sleep(2)
    return False, failed


async def run_one(client, fault: str) -> dict:
    rec = {"fault": fault, "expected_component": EXPECTED_COMPONENT[fault], "host": host_snapshot()}
    await asyncio.to_thread(supervisor.reset)
    logs.state["since"] = time.time()
    await asyncio.sleep(3)
    ok, failed = await wait_healthy(client)
    rec["baseline_healthy"] = ok
    if not ok:
        rec["error"] = f"baseline not healthy: {failed[:5]}"
        return rec
    await asyncio.to_thread(supervisor.inject, fault)
    await asyncio.sleep(5)  # let symptoms develop (DDS lease 3 s, diagnostics 1 Hz)

    inv = Investigation(QUERY)
    agent = Agent(client, ApprovalRegistry(), AuditLog(), auto_approve=True)
    t0 = time.monotonic()
    await agent.run(inv)
    s = inv.summary()
    diag = s["diagnosis"] or {}
    timeouts = sum(1 for e in s["events"] if e["kind"] == "model_timeout")
    rec.update(
        final_phase=s["phase"],
        diagnosed_component=diag.get("faulty_component"),
        root_cause=diag.get("root_cause"),
        evidence_score=diag.get("confidence"),
        cited_evidence=[f"{e['id']} ({e['source']}): {e['text']}" for e in diag.get("evidence", [])],
        diagnosis_success=diag.get("faulty_component") == EXPECTED_COMPONENT[fault],
        inconclusive=s["phase"] == "inconclusive",
        repair_executed=bool((s["repair"] or {}).get("executed")),
        verification_success=bool((s["verification"] or {}).get("verified")),
        rounds=s["round"],
        diagnosis_time_s=s["diagnosis_seconds"],
        total_time_s=round(time.monotonic() - t0, 1),
        tool_calls=s["tool_calls"],
        tools_used=[e["tool"] for e in s["events"] if e["kind"] == "tool_call"],
        llm_calls=s["llm_calls"],
        llm_seconds=s["llm_seconds"],
        model_timeouts=timeouts,
        rejected_diagnoses=sum(1 for e in s["events"] if e["kind"] == "diagnosis_rejected"),
        error=s["error"],
        investigation_id=s["id"],
    )
    return rec


def summarize(results: list[dict]) -> dict:
    ran = [r for r in results if r.get("baseline_healthy")]
    n = len(ran)

    def rate(pred):
        return round(sum(1 for r in ran if pred(r)) / n, 3) if n else None

    diag_t = [r["diagnosis_time_s"] for r in ran if r.get("diagnosis_time_s") is not None]
    rnd = lambda x: None if x is None else round(x, 1)   # noqa: E731
    return {
        "runs": len(results), "valid_runs": n, "invalid_runs_baseline_unhealthy": len(results) - n,
        "diagnosis_accuracy": rate(lambda r: r.get("diagnosis_success")),
        "inconclusive_rate": rate(lambda r: r.get("inconclusive")),
        "error_rate": rate(lambda r: r.get("final_phase") == "error"),
        "timeout_rate": rate(lambda r: r.get("model_timeouts", 0) > 0),
        "repair_success_rate": rate(lambda r: r.get("repair_executed")),
        "verification_success_rate": rate(lambda r: r.get("verification_success")),
        "median_diagnosis_time_s": rnd(statistics.median(diag_t)) if diag_t else None,
        "p95_diagnosis_time_s": rnd(percentile(diag_t, 95)),
        "max_diagnosis_time_s": rnd(max(diag_t)) if diag_t else None,
        "median_total_time_s": rnd(statistics.median([r["total_time_s"] for r in ran if r.get("total_time_s")])) if ran else None,
        "median_tool_calls": statistics.median([r["tool_calls"] for r in ran if r.get("tool_calls") is not None]) if ran else None,
        "median_model_calls": statistics.median([r["llm_calls"] for r in ran if r.get("llm_calls") is not None]) if ran else None,
    }


def write_markdown(meta: dict, summary: dict, results: list[dict], path: Path):
    n = summary["valid_runs"]
    lines = ["# RobotOps benchmark results", "",
             f"Run: {meta['started']} · model `{meta['model']}` ({meta['protocol']} protocol) · {meta['platform']}", "",
             f"Query given to the agent for every fault: *\"{QUERY}\"* (no hint about the fault). "
             f"**Sample size: {summary['runs']} runs ({n} valid)**; p95 with n={n} is the {n - int(0.05 * n)}th-fastest value, "
             "so treat it as a rough bound.", ""]
    if meta["machine"]["warnings"]:
        lines += ["> **Machine was under load during this run** (numbers are pessimistic): " +
                  "; ".join(meta["machine"]["warnings"]), ""]
    lines += ["| metric | value |", "|---|---|"]
    for k, v in summary.items():
        lines.append(f"| {k.replace('_', ' ')} | {v} |")
    lines += ["", "| fault | rep | diagnosed component | correct | repaired | verified | diag time (s) | total (s) | tool calls | model calls | tools used | load | GPU % |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        h = r.get("host", {})
        tail = f"{h.get('loadavg_1m', '')} | {h.get('gpu_util_pct', '')}"
        if not r.get("baseline_healthy"):
            lines.append(f"| {r['fault']} | {r.get('repeat', '')} | - | baseline unhealthy (run invalid) | | | | | | | | {tail} |")
            continue
        yn = lambda b: "✅" if b else "❌"  # noqa: E731
        outcome = (r["diagnosed_component"] or ("inconclusive" if r["inconclusive"] else r["final_phase"]))
        lines.append(f"| {r['fault']} | {r.get('repeat', '')} | {outcome} | {yn(r['diagnosis_success'])} | {yn(r['repair_executed'])} | "
                     f"{yn(r['verification_success'])} | {r['diagnosis_time_s']} | {r['total_time_s']} | {r['tool_calls']} | "
                     f"{r['llm_calls']} | {' → '.join(t for t in r['tools_used'] if t != 'get_ros_health')} | {tail} |")
    lines += ["", "Correct = the component named by the agent matches the injected fault. Repaired = the approved (benchmark auto-approve) "
              "action ran. Verified = independent post-repair re-measurement of the whole robot passed. Timeout rate = runs with at least one "
              "model request that exceeded the request timeout (one retry is attempted). Diagnosis time is measured from the start of the "
              "investigation to the accepted diagnosis.", ""]
    path.write_text("\n".join(lines))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", default=",".join(EXPECTED_COMPONENT))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--tag", default="")
    ap.add_argument("--require-quiet", action="store_true", help="abort instead of warning when the machine is busy")
    a = ap.parse_args()
    faults = [f for f in a.faults.split(",") if f]
    llm_status = await llm.status()
    if not llm_status["model_available"]:
        sys.exit(f"LLM not available: {llm_status}")
    client = get_client()
    client.start()
    await asyncio.sleep(3)
    machine = await machine_check(client, a.require_quiet)
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "model": llm.MODEL, "protocol": llm.PROTOCOL,
            "num_ctx": llm.NUM_CTX, "platform": f"{platform.system()} {platform.release()}, ROS 2 {os.environ.get('ROS_DISTRO')}",
            "query": QUERY, "machine": machine}
    results = []
    for rep in range(a.repeat):
        for f in faults:
            print(f"--- [{rep + 1}/{a.repeat}] {f}", flush=True)
            r = await run_one(client, f)
            r["repeat"] = rep + 1
            results.append(r)
            print(f"    diagnosed={r.get('diagnosed_component')} correct={r.get('diagnosis_success')} "
                  f"verified={r.get('verification_success')} phase={r.get('final_phase')} diag={r.get('diagnosis_time_s')}s "
                  f"tools={r.get('tools_used')} err={r.get('error')}", flush=True)
    await asyncio.to_thread(supervisor.reset)
    summary = summarize(results)
    stamp = time.strftime("%Y%m%d_%H%M%S") + (f"_{a.tag}" if a.tag else "")
    (OUT / f"results_{stamp}.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results}, indent=2, default=str))
    write_markdown(meta, summary, results, OUT / f"results_{stamp}.md")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote benchmarks/results_{stamp}.json / .md")
    client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
