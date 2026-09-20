"""Diagnosis schema, validation against the evidence ledger, and the evidence score.

The LLM proposes a diagnosis via the `submit_diagnosis` tool. Code - not the
LLM - decides whether it is admissible and how confident to be.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.safety import policies

from .evidence import Evidence, EvidenceLedger

NONE = "none"


class ProposedAction(BaseModel):
    action: str
    target: str
    risk: str
    requires_approval: bool = True
    reason: str
    expected_result: str


class Diagnosis(BaseModel):
    status: Literal["diagnosed", "healthy"]
    root_cause: str
    faulty_component: str
    evidence: list[Evidence]
    confidence: float
    confidence_basis: list[str]           # human-readable breakdown of the heuristic score
    recommended_action: ProposedAction | None = None
    uncited_anomalies: list[str] = Field(default_factory=list)


def submit_diagnosis_spec() -> dict:
    comps = policies.components() + [NONE]
    return {"type": "function", "function": {
        "name": "submit_diagnosis",
        "description": ("Submit the root-cause diagnosis once the evidence supports it. Cite the evidence IDs "
                        "(E1, E2, ...) that prove it. Use faulty_component='none' only if every check is healthy."),
        "parameters": {"type": "object", "properties": {
            "root_cause": {"type": "string", "description": "one sentence: what failed and how"},
            "faulty_component": {"type": "string", "enum": comps},
            "evidence_ids": {"type": "array", "items": {"type": "string"},
                             "description": "IDs of findings that prove the root cause, e.g. [\"E3\",\"E7\"]"},
            "recommended_action": {"type": "string", "enum": list(policies.ACTIONS) + [NONE]},
        }, "required": ["root_cause", "faulty_component", "evidence_ids", "recommended_action"]}}}


def evidence_score(cited: list[Evidence], supporting: list[Evidence]) -> tuple[float, list[str]]:
    """Heuristic, conservative score from the *quality* of cited evidence (not from the LLM).

    0.30 base
    +0.15 per anomaly finding about the faulty component (max 3)
    +0.10 if that support comes from >= 2 different tools, +0.10 more for >= 3 tools
    capped at 0.95
    """
    basis = ["base 0.30"]
    score = 0.30
    n = min(len(supporting), 3)
    score += 0.15 * n
    basis.append(f"+{0.15 * n:.2f}: {len(supporting)} anomaly finding(s) about the faulty component (max 3 count)")
    tools = {e.source for e in supporting}
    if len(tools) >= 2:
        score += 0.10
        basis.append(f"+0.10: corroborated by {len(tools)} independent tools ({', '.join(sorted(tools))})")
    if len(tools) >= 3:
        score += 0.10
        basis.append("+0.10: corroborated by >= 3 tools")
    score = min(score, 0.95)
    return round(score, 2), basis


def validate(raw: dict, ledger: EvidenceLedger) -> tuple[Diagnosis | None, list[str]]:
    """Returns (diagnosis, []) when admissible, else (None, errors) to feed back to the LLM."""
    errors: list[str] = []
    root_cause = str(raw.get("root_cause") or "").strip()
    component = str(raw.get("faulty_component") or "").strip().lstrip("/")
    action = str(raw.get("recommended_action") or NONE).strip()
    ids = raw.get("evidence_ids") or []
    if isinstance(ids, str):
        ids = [s for s in ids.replace(",", " ").split() if s]
    if not root_cause:
        errors.append("root_cause is empty")
    if component not in policies.components() + [NONE]:
        errors.append(f"faulty_component {component!r} is not a component of this robot")
    cited, unknown = [], []
    for i in ids:
        e = ledger.get(str(i))
        (cited.append(e) if e else unknown.append(str(i)))
    if unknown:
        errors.append(f"evidence IDs {unknown} do not exist - cite only IDs returned by tools")
    if not cited:
        errors.append("no valid evidence cited")
    if errors:
        return None, errors

    uncited = [f"{e.id}: {e.text}" for e in ledger.anomalies() if e not in cited]

    if component == NONE:
        if action != NONE:
            return None, ["recommended_action must be 'none' when faulty_component is 'none'"]
        if ledger.anomalies():
            return None, [f"cannot declare the system healthy: there are unexplained anomalies "
                          f"({'; '.join(uncited[:3])})"]
        return Diagnosis(status="healthy", root_cause=root_cause, faulty_component=NONE, evidence=cited,
                         confidence=0.0, confidence_basis=["no fault claimed"]), []

    if len({e.step for e in cited}) < 2:
        return None, ["cite findings from at least 2 different tool calls (corroboration): make one more direct "
                      "check of the suspected component (e.g. inspect_topic, inspect_node, check_tf, "
                      "get_component_status), then resubmit"]

    supporting = policies.evidence_supports_target(cited, component)
    try:
        policies.repair_gate(cited, component)
    except policies.PolicyViolation as e:
        return None, [f"insufficient evidence: {e}. Investigate further, then resubmit."]

    score, basis = evidence_score(cited, supporting)
    rec = None
    if action != NONE:
        try:
            pol = policies.check_action(action, component)
        except policies.PolicyViolation as e:
            return None, [str(e)]
        rec = ProposedAction(**pol, reason=root_cause, expected_result=policies.EXPECTED_RESULT.get(component, ""))
    return Diagnosis(status="diagnosed", root_cause=root_cause, faulty_component=component, evidence=cited,
                     confidence=score, confidence_basis=basis, recommended_action=rec,
                     uncited_anomalies=uncited), []
