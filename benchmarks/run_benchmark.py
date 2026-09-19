#!/usr/bin/env python3
"""Automated RobotOps benchmark.

For each fault: reset -> verify healthy baseline -> inject -> ask RobotOps to
"Diagnose the robot." (same query for every fault, no hint) -> score the
diagnosis against the injected fault -> auto-approved repair (benchmark mode,
recorded as such in the audit log) -> independent verification -> timings.

Every number in results.json / results.md is measured by this script.

  python benchmarks/run_benchmark.py [--faults a,b] [--repeat N] [--tag name]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

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


async def wait_healthy(client, timeout=40.0) -> tuple[bool, list]:
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
    rec = {"fault": fault, "expected_component": EXPECTED_COMPONENT[fault]}
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
    rec.update(
        final_phase=s["phase"],
        diagnosed_component=diag.get("faulty_component"),
        root_cause=diag.get("root_cause"),
        evidence_score=diag.get("confidence"),
        cited_evidence=[f"{e['id']} ({e['source']}): {e['text']}" for e in diag.get("evidence", [])],
        diagnosis_success=diag.get("faulty_component") == EXPECTED_COMPONENT[fault],
        repair_executed=bool((s["repair"] or {}).get("executed")),
        verification_success=bool((s["verification"] or {}).get("verified")),
        rounds=s["round"],
        diagnosis_time_s=s["diagnosis_seconds"],
        total_time_s=round(time.monotonic() - t0, 1),
        tool_calls=s["tool_calls"],
        tools_used=[e["tool"] for e in s["events"] if e["kind"] == "tool_call"],
        llm_calls=s["llm_calls"],
        llm_seconds=s["llm_seconds"],
        rejected_diagnoses=sum(1 for e in s["events"] if e["kind"] == "diagnosis_rejected"),
        error=s["error"],
        investigation_id=s["id"],
    )
    return rec


def summarize(results: list[dict]) -> dict:
    ran = [r for r in results if r.get("baseline_healthy")]
    n = len(ran)

    def rate(key):
        return round(sum(1 for r in ran if r.get(key)) / n, 3) if n else None

    def med(key):
        vals = [r[key] for r in ran if r.get(key) is not None]
        return round(statistics.median(vals), 1) if vals else None
    return {"runs": len(results), "valid_runs": n,
            "diagnosis_success_rate": rate("diagnosis_success"),
            "repair_success_rate": rate("repair_executed"),
            "verification_success_rate": rate("verification_success"),
            "median_diagnosis_time_s": med("diagnosis_time_s"),
            "median_total_time_s": med("total_time_s"),
            "median_tool_calls": med("tool_calls")}


def write_markdown(meta: dict, summary: dict, results: list[dict], path: Path):
    lines = [f"# RobotOps benchmark results", "",
             f"Run: {meta['started']} · model `{meta['model']}` (thinking {'on' if meta['think'] else 'off'}) · "
             f"{meta['platform']}", "",
             f"Query given to the agent for every fault: *\"{QUERY}\"* (no hint about the fault).", "",
             "| metric | value |", "|---|---|"]
    for k, v in summary.items():
        lines.append(f"| {k.replace('_', ' ')} | {v} |")
    lines += ["", "| fault | diagnosed component | diagnosis | repair | verified | diag time (s) | total (s) | "
              "tool calls | tools used |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.get("baseline_healthy"):
            lines.append(f"| {r['fault']} | - | baseline unhealthy | | | | | | |")
            continue
        yn = lambda b: "✅" if b else "❌"  # noqa: E731
        lines.append(f"| {r['fault']} | {r['diagnosed_component'] or '-'} | {yn(r['diagnosis_success'])} | "
                     f"{yn(r['repair_executed'])} | {yn(r['verification_success'])} | {r['diagnosis_time_s']} | "
                     f"{r['total_time_s']} | {r['tool_calls']} | {' → '.join(r['tools_used'])} |")
    lines += ["", "Diagnosis = the component named by the agent matches the injected fault. "
              "Repair = the approved (benchmark auto-approve) action was executed. "
              "Verified = independent post-repair re-measurement of the whole robot passed.", ""]
    path.write_text("\n".join(lines))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", default=",".join(EXPECTED_COMPONENT))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    faults = [f for f in a.faults.split(",") if f]
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "model": llm.MODEL, "think": llm.THINK,
            "platform": f"{platform.system()} {platform.release()}, ROS 2 {os.environ.get('ROS_DISTRO')}",
            "query": QUERY}
    llm_status = await llm.status()
    if not llm_status["model_available"]:
        sys.exit(f"LLM not available: {llm_status}")
    client = get_client()
    client.start()
    await asyncio.sleep(2)
    results = []
    for rep in range(a.repeat):
        for f in faults:
            print(f"--- [{rep + 1}/{a.repeat}] {f}", flush=True)
            r = await run_one(client, f)
            r["repeat"] = rep + 1
            results.append(r)
            print(f"    diagnosed={r.get('diagnosed_component')} ok={r.get('diagnosis_success')} "
                  f"verified={r.get('verification_success')} phase={r.get('final_phase')} "
                  f"t={r.get('total_time_s')}s tools={r.get('tools_used')} err={r.get('error')}", flush=True)
    await asyncio.to_thread(supervisor.reset)
    summary = summarize(results)
    suffix = f"_{a.tag}" if a.tag else ""
    (OUT / f"results{suffix}.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results},
                                                          indent=2))
    write_markdown(meta, summary, results, OUT / f"results{suffix}.md")
    print(json.dumps(summary, indent=2))
    client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
