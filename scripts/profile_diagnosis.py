#!/usr/bin/env python3
"""Latency profiler: run real diagnoses and break the time down.

  python scripts/profile_diagnosis.py [--runs 3] [--fault controller_crash] [--cold] [--out benchmarks/profiles/x.json]

Each run: reset robot -> inject fault -> full investigation with auto-approve (same code path as the UI) ->
per-step breakdown from the investigation's own event stream (tool execution, model call wall time and the
Ollama-reported load / prompt-eval / generation split, token counts) plus orchestration = everything else.
"""
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

from backend.agent import llm
from backend.agent.graph import Agent
from backend.agent.state import Investigation
from backend.ros_tools import logs, supervisor
from backend.ros_tools.client import get_client
from backend.safety.approvals import ApprovalRegistry
from backend.safety.audit import AuditLog


async def unload_model():
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post(f"{llm.OLLAMA_URL}/api/generate", json={"model": llm.MODEL, "keep_alive": 0})
    await asyncio.sleep(2)


def breakdown(inv: Investigation, t_start: float) -> dict:
    ev = inv.events
    steps, llm_calls = [], []
    pending = {}
    for e in ev:
        if e["kind"] == "tool_call":
            pending[e["step"]] = e
        elif e["kind"] == "tool_result":
            c = pending.get(e["step"])
            steps.append({"step": e["step"], "tool": e["tool"], "args": e.get("args"),
                          "tool_ms": e["duration_ms"], "started": c["ts"] - t_start if c else None})
        elif e["kind"] == "llm_call":
            llm_calls.append({k: e.get(k) for k in ("n", "tools", "wall_s", "load_s", "prompt_tokens", "prompt_eval_s",
                                                     "output_tokens", "generation_s", "tokens_per_s", "attempts")}
                             | {"t": e["ts"] - t_start})
    phase_t = {}
    for e in ev:
        if e["kind"] == "phase":
            phase_t.setdefault(e["phase"], e["ts"] - t_start)
    diag_t = inv.diagnosed_at - t_start if inv.diagnosed_at else None
    fin = (inv.finished_at or time.time()) - t_start
    pre_diag_llm = [c for c in llm_calls if diag_t is None or c["t"] <= diag_t + 0.01]
    pre_diag_tools = [s for s in steps if diag_t is None or (s["started"] or 0) <= diag_t]
    llm_wall = sum(c["wall_s"] for c in pre_diag_llm)
    tool_s = sum(s["tool_ms"] for s in pre_diag_tools) / 1000
    out = {
        "phase": inv.phase.value, "diagnosis_s": round(diag_t, 2) if diag_t else None, "total_s": round(fin, 2),
        "approval_to_verified_s": round(fin - phase_t["repairing"], 2) if "repairing" in phase_t else None,
        "repair_to_verified_s": round(fin - phase_t["verifying"], 2) if "verifying" in phase_t else None,
        "model_calls": len(llm_calls), "tool_calls": len(steps),
        "diag_model_wall_s": round(llm_wall, 2), "diag_tools_s": round(tool_s, 2),
        "diag_overhead_s": round(diag_t - llm_wall - tool_s, 2) if diag_t else None,
        "diag_model_load_s": round(sum(c["load_s"] for c in pre_diag_llm), 2),
        "diag_model_prompt_eval_s": round(sum(c["prompt_eval_s"] for c in pre_diag_llm), 2),
        "diag_model_generation_s": round(sum(c["generation_s"] for c in pre_diag_llm), 2),
        "diag_prompt_tokens": sum(c["prompt_tokens"] for c in pre_diag_llm),
        "diag_output_tokens": sum(c["output_tokens"] for c in pre_diag_llm),
        "steps": steps, "llm_calls": llm_calls,
        "faulty_component": (inv.diagnosis or {}).get("faulty_component"),
    }
    return out


async def one_run(client, fault: str, cold: bool) -> dict:
    await asyncio.to_thread(supervisor.reset)
    logs.state["since"] = time.time()
    await asyncio.sleep(4)
    if cold:
        await unload_model()
    await asyncio.to_thread(supervisor.inject, fault)
    await asyncio.sleep(5)
    inv = Investigation("Robot stopped moving. Diagnose it.")
    agent = Agent(client, ApprovalRegistry(), AuditLog(), auto_approve=True)
    t0 = time.time()
    inv.created_at = t0
    await agent.run(inv)
    return breakdown(inv, t0)


async def prompt_anatomy() -> dict:
    """Prompt token cost of each context component, measured by Ollama (prompt_eval_count)."""
    from backend.agent import graph, prompts
    sysm = prompts.system_prompt(10, llm.THINK)
    tools = graph._tool_specs()
    usr = prompts.user_prompt("Robot stopped moving. Diagnose it.", "Findings (cite these IDs):\nE1 [ok] x")

    async def count(messages, tl):
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{llm.OLLAMA_URL}/api/chat", json={
                "model": llm.MODEL, "messages": messages, "tools": tl, "stream": False, "think": False,
                "options": {"num_predict": 1, "temperature": 0}})
        return r.json().get("prompt_eval_count", 0)
    base = await count([{"role": "user", "content": "x"}], [])
    with_tools = await count([{"role": "user", "content": "x"}], tools)
    with_sys = await count([{"role": "system", "content": sysm}, {"role": "user", "content": "x"}], [])
    return {"empty_prompt_tokens": base, "tool_schemas_tokens": with_tools - base,
            "system_prompt_tokens": with_sys - base, "system_prompt_chars": len(sysm),
            "user_prompt_tokens_est": len(usr) // 4}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--fault", default="controller_crash")
    ap.add_argument("--cold", action="store_true", help="unload the model before each run")
    ap.add_argument("--anatomy", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    st = await llm.status()
    if not st["model_available"]:
        raise SystemExit(f"LLM unavailable: {st}")
    client = get_client()
    client.start()
    await asyncio.sleep(2.5)
    res = {"model": llm.MODEL, "think": llm.THINK, "fault": a.fault, "cold": a.cold, "runs": []}
    if a.anatomy:
        res["prompt_anatomy"] = await prompt_anatomy()
        print("prompt anatomy:", res["prompt_anatomy"])
    for i in range(a.runs):
        r = await one_run(client, a.fault, a.cold)
        res["runs"].append(r)
        print(f"run {i + 1}: phase={r['phase']} diagnosis={r['diagnosis_s']}s (model wall {r['diag_model_wall_s']}s "
              f"[load {r['diag_model_load_s']} + prompt {r['diag_model_prompt_eval_s']} + gen {r['diag_model_generation_s']}], "
              f"tools {r['diag_tools_s']}s, overhead {r['diag_overhead_s']}s) model_calls={r['model_calls']} "
              f"tools={[s['tool'] for s in r['steps']]} repair->verified={r['repair_to_verified_s']}s total={r['total_s']}s "
              f"tokens in/out={r['diag_prompt_tokens']}/{r['diag_output_tokens']}", flush=True)
    d = [r["diagnosis_s"] for r in res["runs"] if r["diagnosis_s"]]
    if d:
        res["median_diagnosis_s"] = round(statistics.median(d), 1)
        print("median diagnosis:", res["median_diagnosis_s"], "s")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, default=str))
    await asyncio.to_thread(supervisor.reset)
    client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
