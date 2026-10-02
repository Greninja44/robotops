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

from . import client as ros_client
from . import supervisor
from .common import node_of


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
    if action == "set_parameter":
        fix = policies.PARAMETER_FIX[target]               # already validated by check_action above
        full_name = node_of(target)
        if full_name is None:
            return {"executed": False, "action": action, "target": target,
                    "error": f"no ROS node found for component {target!r}"}
        try:
            res = ros_client.get_client().set_parameters(full_name, dict(fix["params"]))
        except Exception as e:  # noqa: BLE001 - ROS unavailable, service missing, timeout, etc.
            return {"executed": False, "action": action, "target": target, "error": f"{type(e).__name__}: {e}"}
        return {"executed": bool(res.get("all_successful")), "action": action, "target": target,
                "parameter_service_response": res, "duration_ms": int((time.monotonic() - t0) * 1000)}
    raise policies.PolicyViolation(f"no executor for {action}")  # unreachable while every ACTIONS entry has a branch
