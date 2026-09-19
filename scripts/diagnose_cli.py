#!/usr/bin/env python3
"""Run one RobotOps investigation from the terminal (no web UI).

  python scripts/diagnose_cli.py "My robot stopped moving" [--inject controller_crash] [--auto-approve]
"""
import argparse
import asyncio
import json
import sys
import time

from backend.agent.graph import Agent
from backend.agent.state import Investigation
from backend.ros_tools import logs, supervisor
from backend.ros_tools.client import get_client
from backend.safety.approvals import ApprovalRegistry
from backend.safety.audit import AuditLog


def printer(msg):
    ev = msg["event"]
    k = ev["kind"]
    if k == "tool_call":
        print(f"\n[{ev['step']}] -> {ev['tool']}({json.dumps(ev['args'])})" + (f"   # {ev['reason']}" if ev.get("reason") else ""))
    elif k == "tool_result":
        if not ev["success"]:
            print(f"     FAILED: {ev['error']}")
        for e in ev["evidence"]:
            print(f"     {e['id']:>4} {'!!' if e['anomaly'] else '  '} {e['text']}")
    elif k == "phase":
        print(f"\n=== phase: {ev.get('previous')} -> {ev['phase']}" + (f" ({ev['reason']})" if ev.get("reason") else ""))
    elif k == "diagnosis":
        d = ev["diagnosis"]
        print(f"\nROOT CAUSE: {d['root_cause']}\n  component={d['faulty_component']} score={d['confidence']}")
        for e in d["evidence"]:
            print(f"  * {e['id']} ({e['source']}) {e['text']}")
        print("  basis:", "; ".join(d["confidence_basis"]))
    elif k == "diagnosis_rejected":
        print(f"\n   DIAGNOSIS REJECTED: {ev['errors']}  (submitted {ev['submitted']})")
    elif k == "verification_attempt":
        print(f"   verify attempt {ev['attempt']}: {ev['passed']}/{ev['total']} passed {ev['failed'][:4]}")
    elif k in ("note", "warning", "error", "repair_result", "approval"):
        print(f"   [{k}] {({x: y for x, y in ev.items() if x not in ('seq', 'ts', 'kind', 'phase')})}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--inject")
    ap.add_argument("--auto-approve", action="store_true")
    a = ap.parse_args()
    client = get_client()
    client.start()
    await asyncio.sleep(2.5)
    if a.inject:
        print("inject:", supervisor.inject(a.inject))
        await asyncio.sleep(5)
    approvals = ApprovalRegistry()
    agent = Agent(client, approvals, AuditLog(), auto_approve=a.auto_approve)
    inv = Investigation(a.query, listener=printer)
    t0 = time.time()
    task = asyncio.create_task(agent.run(inv))
    while not task.done():
        await asyncio.sleep(0.2)
        if inv.phase.value == "awaiting_approval" and not a.auto_approve and inv.proposal["state"] == "pending":
            p = inv.proposal
            ans = await asyncio.to_thread(input, f"\nAPPROVE {p['action']} {p['target']} (risk {p['risk']})? [y/N] ")
            approvals.decide(p["id"], ans.strip().lower() == "y", by="cli-operator")
    await task
    print(f"\nFINAL: {inv.phase.value}  tools={inv.tool_calls} llm_calls={inv.llm_calls} "
          f"llm_s={inv.llm_seconds:.0f} wall={time.time() - t0:.0f}s")
    client.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
