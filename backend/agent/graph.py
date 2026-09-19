"""The RobotOps agent: an explicit state machine around an LLM tool-calling loop.

OBSERVE -> INVESTIGATE (LLM chooses read-only tools; each result becomes evidence)
        -> DIAGNOSE (LLM submits diagnosis; code validates it against the ledger)
        -> AWAITING_APPROVAL (human) -> REPAIR (allowlisted executor)
        -> VERIFY (independent re-measurement) -> RESOLVED | back to INVESTIGATE | REPAIR_FAILED

The LLM decides *which* tools to call and *what* the root cause is. Code decides
whether the evidence is real and sufficient, what may be executed, and whether
the robot actually recovered.
"""
from __future__ import annotations

import asyncio
import json
import time

from backend.ros_tools import registry, repair
from backend.ros_tools.common import ToolResult
from backend.safety import policies
from backend.safety.approvals import ApprovalError, ApprovalRegistry
from backend.safety.audit import AuditLog

from . import llm, prompts, verification
from .diagnosis import submit_diagnosis_spec, validate
from .evidence import format_for_llm
from .state import Investigation, Phase

MAX_STEPS = 10           # diagnostic tool calls per round
MAX_ROUNDS = 2           # a failed verification sends the agent back to investigate once
MAX_REJECTED_DIAGNOSES = 3
MAX_NUDGES = 2
APPROVAL_TIMEOUT_S = 15 * 60
MAX_CALLS_PER_TURN = 2


def _tool_specs() -> list[dict]:
    specs = []
    for s in registry.llm_tool_specs():
        s = json.loads(json.dumps(s))
        s["function"]["parameters"]["properties"]["reason"] = {
            "type": "string", "description": "one short sentence: which hypothesis this call tests"}
        specs.append(s)
    return specs + [submit_diagnosis_spec()]


class Agent:
    def __init__(self, client, approvals: ApprovalRegistry, audit: AuditLog, *, auto_approve: bool = False,
                 max_steps: int = MAX_STEPS, chat=None):
        self.client = client
        self.approvals = approvals
        self.audit = audit
        self.auto_approve = auto_approve
        self.max_steps = max_steps
        self.chat = chat or llm.chat
        self.tools = _tool_specs()

    # ------------------------------------------------------------------ helpers
    def _log(self, inv: Investigation, kind: str, **payload):
        self.audit.write(inv.id, kind, **payload)

    async def _run_tool(self, inv: Investigation, name: str, args: dict, reason: str | None) -> tuple[ToolResult, list]:
        inv.tool_calls += 1
        step = inv.tool_calls
        inv.emit("tool_call", step=step, tool=name, args=args, reason=reason)
        self._log(inv, "tool_call", step=step, tool=name, args=args, reason=reason)
        result = await asyncio.to_thread(registry.execute, self.client, name, args)
        evidence = inv.ledger.add(result, step)
        inv.emit("tool_result", step=step, tool=name, args=result.args, success=result.success, error=result.error,
                 duration_ms=result.duration_ms, data=result.data,
                 evidence=[e.model_dump(include={"id", "text", "anomaly", "subjects"}) for e in evidence])
        self._log(inv, "tool_result", step=step, tool=name, success=result.success, error=result.error,
                  evidence=[f"{e.id}{'!' if e.anomaly else ''} {e.text}" for e in evidence])
        return result, evidence

    # ------------------------------------------------------------------ phases
    async def run(self, inv: Investigation) -> Investigation:
        self._log(inv, "investigation_started", query=inv.query, auto_approve=self.auto_approve)
        try:
            await self._observe(inv)
            while True:
                diag = await self._investigate(inv)
                if diag is None:
                    inv.transition(Phase.INCONCLUSIVE, reason="insufficient evidence - no repair will be attempted")
                    break
                if diag.status == "healthy":
                    inv.transition(Phase.HEALTHY)
                    break
                if diag.recommended_action is None:
                    inv.transition(Phase.DIAGNOSED)
                    break
                if not await self._approval(inv, diag):
                    break
                if not await self._repair(inv, diag):
                    break
                if await self._verify(inv, diag):
                    break
                if inv.round >= MAX_ROUNDS:
                    inv.transition(Phase.REPAIR_FAILED, reason="recovery could not be verified; stopping safely")
                    break
                inv.round += 1
                inv.transition(Phase.INVESTIGATING, reason="verification failed - re-investigating", round=inv.round)
                failed = "; ".join(f"{c['check']}: {c['detail']}" for c in inv.verification["failed"][:8])
                inv.messages.append({"role": "user", "content": (
                    f"The repair ({diag.recommended_action.action} {diag.recommended_action.target}) was executed "
                    f"but verification FAILED: {failed}. The root cause is probably elsewhere. Investigate again "
                    f"and submit a new diagnosis.")})
        except llm.LLMUnavailable as e:
            self._fail(inv, f"LLM unavailable: {e}")
        except Exception as e:  # noqa: BLE001 - an investigation must never take the backend down
            self._fail(inv, f"{type(e).__name__}: {e}")
        self._log(inv, "investigation_finished", phase=inv.phase.value, tool_calls=inv.tool_calls,
                  error=inv.error, verification=(inv.verification or {}).get("verified"))
        return inv

    def _fail(self, inv: Investigation, message: str):
        """Stop in ERROR from any phase (evidence gathered so far is kept)."""
        inv.error = message
        inv.emit("error", message=message)
        if not inv.done:
            prev, inv.phase, inv.finished_at = inv.phase, Phase.ERROR, time.time()
            inv.emit("phase", previous=prev.value)

    async def _observe(self, inv: Investigation):
        inv.emit("note", text="Observing: taking a baseline snapshot of the ROS system")
        result, evidence = await self._run_tool(inv, "get_ros_health", {}, "baseline observation")
        if not result.success:
            raise RuntimeError(f"cannot observe the ROS system: {result.error}")
        inv.messages = [
            {"role": "system", "content": prompts.system_prompt(self.max_steps)},
            {"role": "user", "content": prompts.user_prompt(inv.query, format_for_llm(result, evidence))},
        ]
        inv.transition(Phase.INVESTIGATING)

    async def _investigate(self, inv: Investigation):
        steps = nudges = rejected = 0
        forced = False
        seen: dict[str, list[str]] = {}
        llm_turns = 0
        while True:
            llm_turns += 1
            if llm_turns > self.max_steps + 6:  # hard stop, whatever the model does
                inv.emit("note", text="LLM turn limit reached without an admissible diagnosis")
                return None
            if steps >= self.max_steps and not forced:
                forced = True
                inv.messages.append({"role": "user", "content": prompts.FORCE_DIAGNOSIS})
                inv.emit("note", text=f"Step budget ({self.max_steps}) reached - asking for a diagnosis")
            reply = await self.chat(inv.messages, self.tools)
            inv.llm_calls += 1
            inv.llm_seconds += reply.seconds
            for m in reply.malformed:
                inv.emit("warning", text=f"Malformed model output ignored: {m}")
            if not reply.tool_calls:
                nudges += 1
                if nudges > MAX_NUDGES or forced:
                    inv.emit("note", text="Model stopped calling tools without a valid diagnosis")
                    return None
                inv.messages.append(reply.raw_message)
                inv.messages.append({"role": "user", "content": prompts.NUDGE_NO_TOOL})
                continue
            nudges = 0
            inv.messages.append(reply.raw_message)
            for i, tc in enumerate(reply.tool_calls):
                if i >= MAX_CALLS_PER_TURN:
                    inv.messages.append({"role": "tool", "tool_name": tc.name,
                                         "content": "Not executed: call one tool at a time."})
                    continue
                if tc.name == "submit_diagnosis":
                    inv.transition(Phase.DIAGNOSING)
                    self._log(inv, "diagnosis_submitted", raw=tc.arguments)
                    diag, errors = validate(tc.arguments, inv.ledger)
                    if diag is not None:
                        inv.diagnosis = diag.model_dump()
                        inv.diagnosed_at = time.time()
                        inv.emit("diagnosis", diagnosis=inv.diagnosis)
                        self._log(inv, "diagnosis_accepted", root_cause=diag.root_cause,
                                  component=diag.faulty_component, confidence=diag.confidence,
                                  evidence=[e.id for e in diag.evidence])
                        return diag
                    rejected += 1
                    inv.emit("diagnosis_rejected", errors=errors, submitted=tc.arguments)
                    self._log(inv, "diagnosis_rejected", errors=errors)
                    if rejected >= MAX_REJECTED_DIAGNOSES:
                        return None
                    inv.transition(Phase.INVESTIGATING, reason="diagnosis rejected by evidence validator")
                    inv.messages.append({"role": "tool", "tool_name": tc.name,
                                         "content": "DIAGNOSIS REJECTED: " + " | ".join(errors)})
                    continue
                args = {k: v for k, v in tc.arguments.items() if k != "reason"}
                reason = tc.arguments.get("reason") if isinstance(tc.arguments.get("reason"), str) else None
                if forced:
                    inv.messages.append({"role": "tool", "tool_name": tc.name,
                                         "content": "Not executed: budget exhausted. Call submit_diagnosis."})
                    continue
                key = tc.name + json.dumps(args, sort_keys=True, default=str)
                if key in seen:
                    steps += 1
                    inv.messages.append({"role": "tool", "tool_name": tc.name, "content": (
                        f"Duplicate call - you already have these findings: {', '.join(seen[key]) or 'none'}. "
                        "Use a different tool or submit_diagnosis.")})
                    continue
                steps += 1
                result, evidence = await self._run_tool(inv, tc.name, args, reason)
                seen[key] = [e.id for e in evidence]
                inv.messages.append({"role": "tool", "tool_name": tc.name,
                                     "content": format_for_llm(result, evidence)})

    async def _approval(self, inv: Investigation, diag) -> bool:
        act = diag.recommended_action
        p = self.approvals.create(inv.id, act.action, act.target)
        inv.proposal = {"id": p.id, **act.model_dump(), "state": p.state,
                        "evidence": [f"{e.id}: {e.text}" for e in diag.evidence]}
        inv.transition(Phase.AWAITING_APPROVAL, proposal=inv.proposal)
        self._log(inv, "repair_proposed", proposal=inv.proposal)
        if self.auto_approve:
            self.approvals.decide(p.id, True, by="benchmark-auto-approve")
        try:
            await asyncio.wait_for(p.event.wait(), APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.approvals.expire(p.id)
        inv.proposal["state"] = p.state
        inv.proposal["decided_by"] = p.decided_by
        inv.emit("approval", state=p.state, decided_by=p.decided_by, proposal_id=p.id)
        self._log(inv, "approval_decision", proposal_id=p.id, state=p.state, by=p.decided_by)
        if p.state != "approved":
            inv.transition(Phase.REJECTED, reason=f"proposal {p.state}")
            return False
        inv.transition(Phase.REPAIRING)
        return True

    async def _repair(self, inv: Investigation, diag) -> bool:
        act = diag.recommended_action
        inv.emit("repair_started", action=act.action, target=act.target)
        try:
            res = await asyncio.to_thread(repair.execute, self.approvals, inv.proposal["id"], act.action, act.target)
        except (policies.PolicyViolation, ApprovalError) as e:
            res = {"executed": False, "error": str(e)}
        inv.repair = res
        inv.emit("repair_result", **res)
        self._log(inv, "repair_executed", **res)
        if not res.get("executed"):
            inv.transition(Phase.REPAIR_FAILED, reason=res.get("error", "repair not executed"))
            return False
        inv.transition(Phase.VERIFYING)
        return True

    async def _verify(self, inv: Investigation, diag) -> bool:
        target = diag.recommended_action.target
        loop = asyncio.get_running_loop()

        def on_attempt(i, checks):
            failed = [c for c in checks if not c["passed"]]
            loop.call_soon_threadsafe(lambda: inv.emit(
                "verification_attempt", attempt=i, passed=len(checks) - len(failed), total=len(checks),
                failed=[f"{c['check']}: {c['detail']}" for c in failed]))
        result = await asyncio.to_thread(verification.verify, self.client, target, on_attempt=on_attempt)
        inv.verification = result
        inv.emit("verification", **result)
        self._log(inv, "verification", verified=result["verified"], attempts=result["attempts"],
                  failed=[c["check"] for c in result["failed"]])
        if result["verified"]:
            inv.transition(Phase.RESOLVED, reason="recovery independently verified")
            return True
        return False
