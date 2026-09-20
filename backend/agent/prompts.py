"""Prompts for the investigation agent (compact, JSON-decision protocol).

The system prompt contains ARCHITECTURE knowledge only (what a healthy robot looks like, what the tools do).
It never contains fault information: which fault is active is discovered from tool evidence.
"""
from __future__ import annotations

from backend.ros_tools import registry
from backend.ros_tools.common import manifest


def architecture_summary() -> str:
    """Compact topology derived from demo_robot/manifest.json (static architecture, not fault identity)."""
    m = manifest()
    lines = []
    for node, meta in m["nodes"].items():
        consumes = [t for t, v in m["topics"].items() if node in v["subscribers"]]
        produces = [t for t, v in m["topics"].items() if node in v["publishers"]]
        produces += [f"TF {e['parent']}->{e['child']}" for e in m["tf"] if e["broadcaster"] == node]
        lines.append(f"{node} ({meta['component']}): {meta['role']}; consumes {', '.join(consumes) or '-'}; "
                     f"produces {', '.join(produces) or '-'}")
    rates = ", ".join(f"{t} >= {v['min_rate_hz']:g} Hz" for t, v in m["topics"].items())
    return "\n".join(lines) + f"\nHealthy rates: {rates}"


def tool_summary() -> str:
    lines = []
    for name, (_, desc, props, _req) in registry.READ_ONLY_TOOLS.items():
        lines.append(f"- {name}({', '.join(props)}): {desc}")
    return "\n".join(lines)


SYSTEM = """You are RobotOps, a ROS 2 reliability engineer. Diagnose ONLY from tool evidence.

Normal architecture:
{arch}

Tools (read-only):
{tools}

Reply with ONE JSON object and nothing else.
Investigate: {{"reason_summary":"<one short sentence>","action":"tool","tool":"<name>","arguments":{{...}}}}
Conclude:    {{"reason_summary":"<one short sentence>","action":"diagnose","root_cause":"<what failed and how>","faulty_component":"<component name or none>","evidence_ids":["E2","E5"],"recommended_action":"restart_component"|"none"}}

Rules:
- Findings carry IDs (E4). [ANOMALY] means deviation from the normal architecture above.
- Trace symptoms upstream to where the problem ORIGINATES, not to the node that complains.
- A node can be running but broken (stalled, hung, misconfigured): check rates, TF freshness, parameters.
- Do not repeat a call. You have at most {max_steps} tool calls; usually 2-4 are enough.
- Conclude only when at least two DIFFERENT tool calls support the same component; cite their evidence IDs.
- restart_component fixes crashed, stalled, hung and misconfigured components.
- Never invent evidence."""


def system_prompt(max_steps: int, think: bool = True) -> str:  # `think` kept for call-site compatibility
    return SYSTEM.format(arch=architecture_summary(), tools=tool_summary(), max_steps=max_steps)


def user_prompt(query: str, initial_observation: str) -> str:
    return (f"Operator report: \"{query}\"\n\n"
            f"Initial observation (get_ros_health, run automatically):\n{initial_observation}")


NUDGE_NO_TOOL = 'Reply with one JSON object: {"action":"tool",...} to investigate or {"action":"diagnose",...} to conclude.'
FORCE_DIAGNOSIS = ('Investigation budget exhausted. Reply now with {"action":"diagnose",...} using only evidence IDs '
                   'you have already received.')
