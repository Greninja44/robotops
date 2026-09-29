"""Incident memory: a small persisted log of past investigations, offered to the agent as CONTEXT on a new
one - never as evidence. A diagnosis still needs its own evidence IDs from the current investigation's own
ledger to be accepted (`diagnosis.validate` has no notion of memory at all); memory can only ever nudge which
tool the model reaches for first, exactly like a human engineer's recollection of past incidents would.

Recording is agent-output only: what THIS agent itself concluded and whether the repair verified, never the
supervisor's own scoring record of which fault was actually injected (this module reads nothing from
demo_robot/state/ at all). That keeps it consistent with the rest of the safety model - it cannot become a
side channel for a hidden fault's identity, because it only ever contains what the agent already said out
loud in a past investigation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("ROBOTOPS_INCIDENT_LOG", str(Path(__file__).resolve().parents[2] / "logs" / "incidents.jsonl")))
MAX_COMPONENTS_IN_SUMMARY = 5


def record(inv) -> None:
    """Append one line once an investigation reaches a terminal phase. Called from Agent.run(). Skips
    investigations where the agent never settled on a specific component (inconclusive, error, healthy) -
    there is nothing to remember about those."""
    diag = inv.diagnosis or {}
    component = diag.get("faulty_component")
    if not component or component == "none":
        return
    entry = {
        "id": inv.id, "ts": inv.finished_at or inv.created_at, "phase": inv.phase.value, "component": component,
        "root_cause": diag.get("root_cause"), "action": (inv.repair or {}).get("action"),
        "repaired": bool((inv.repair or {}).get("executed")), "verified": bool((inv.verification or {}).get("verified")),
    }
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # memory is an aid, never a reason to fail an investigation


def all_incidents() -> list[dict]:
    if not PATH.exists():
        return []
    out = []
    for line in PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summary(limit_components: int = MAX_COMPONENTS_IN_SUMMARY) -> str:
    """Compact, aggregated text for the system prompt: counts and the most recent outcome per component, for
    the N most-recently-seen components. Never a raw dump, so the prompt stays small no matter how large the
    log grows (see docs/PERFORMANCE.md - a large context was the original latency problem)."""
    incidents = all_incidents()
    if not incidents:
        return ""
    by_component: dict[str, list[dict]] = {}
    for e in incidents:
        if e.get("component"):
            by_component.setdefault(e["component"], []).append(e)
    if not by_component:
        return ""
    order = sorted(by_component, key=lambda c: by_component[c][-1].get("ts") or 0, reverse=True)[:limit_components]
    lines = []
    for c in order:
        entries = by_component[c]
        last = entries[-1]
        if last.get("verified"):
            outcome = "verified"
        elif last.get("repaired"):
            outcome = "repaired, not verified"
        else:
            outcome = "not repaired"
        lines.append(f"- {c}: {len(entries)} past incident(s); most recently {last.get('action') or 'diagnosed only'} ({outcome})")
    return "\n".join(lines)
