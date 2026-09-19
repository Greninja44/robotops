"""State-changing repair actions. Never exposed to the LLM as a callable tool.

The only entry point is `execute`, which requires a consumed approval and an
allowlisted (action, target). It performs the action and reports what the
process manager said; whether the robot actually recovered is decided
separately by agent/verification.py.
"""
from __future__ import annotations

import time

from backend.safety import policies
from backend.safety.approvals import ApprovalRegistry

from . import supervisor


def execute(approvals: ApprovalRegistry, proposal_id: str, action: str, target: str) -> dict:
    policies.check_action(action, target)                 # allowlist (raises PolicyViolation)
    approvals.consume(proposal_id, action, target)        # approval (raises ApprovalError)
    t0 = time.monotonic()
    if action == "restart_component":
        try:
            res = supervisor.restart(target)
        except Exception as e:  # noqa: BLE001 - supervisor unreachable etc.
            return {"executed": False, "action": action, "target": target, "error": f"{type(e).__name__}: {e}"}
        return {"executed": bool(res.get("ok")), "action": action, "target": target,
                "process_manager_response": res, "duration_ms": int((time.monotonic() - t0) * 1000)}
    raise policies.PolicyViolation(f"no executor for {action}")  # unreachable while ACTIONS has one entry
