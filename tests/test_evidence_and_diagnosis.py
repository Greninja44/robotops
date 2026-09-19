"""Evidence ledger + diagnosis validation: the LLM cannot cite what tools never observed."""
from conftest import make_result

from backend.agent import diagnosis
from backend.agent.evidence import EvidenceLedger, format_for_llm


def _ledger_with_controller_fault():
    l = EvidenceLedger()
    l.add(make_result("list_nodes", [
        ("Expected node /base_controller is NOT present", True, ["/base_controller"]),
        ("5 other nodes present", False, ["/lidar_driver"])]), 1)
    l.add(make_result("inspect_topic", [
        ("/cmd_vel: expected subscriber /base_controller is missing", True, ["/cmd_vel", "/base_controller"])]), 2)
    return l


def test_ledger_assigns_sequential_ids_from_tool_findings():
    l = _ledger_with_controller_fault()
    assert list(l.items) == ["E1", "E2", "E3"]
    assert l.get("e1").source == "list_nodes" and l.get("E3").step == 2
    assert [e.id for e in l.anomalies()] == ["E1", "E3"]


def test_format_for_llm_lists_ids_and_truncates_data():
    l = EvidenceLedger()
    r = make_result("x", [("something", True, [])], blob="a" * 5000)
    txt = format_for_llm(r, l.add(r, 1))
    assert "E1 [ANOMALY] something" in txt and "truncated" in txt and len(txt) < 1200


def test_failed_tool_is_reported_not_invented():
    r = make_result("x", success=False)
    r.error = "timeout"
    assert format_for_llm(r, []) == "TOOL FAILED (x): timeout"


def test_valid_diagnosis_is_accepted_with_derived_score():
    l = _ledger_with_controller_fault()
    d, errors = diagnosis.validate({"root_cause": "controller crashed", "faulty_component": "base_controller",
                                    "evidence_ids": ["E1", "E3"], "recommended_action": "restart_component"}, l)
    assert errors == [] and d.status == "diagnosed"
    assert d.recommended_action.action == "restart_component" and d.recommended_action.requires_approval
    assert d.recommended_action.risk == "medium"
    # 0.30 base + 2 * 0.15 + 0.10 (2 tools) = 0.70 -- computed from evidence, not from the model
    assert d.confidence == 0.7 and any("base 0.30" in b for b in d.confidence_basis)


def test_invented_evidence_ids_are_rejected():
    l = _ledger_with_controller_fault()
    d, errors = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller",
                                    "evidence_ids": ["E1", "E99"], "recommended_action": "restart_component"}, l)
    assert d is None and "E99" in errors[0]


def test_no_evidence_no_diagnosis():
    d, errors = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller",
                                    "evidence_ids": [], "recommended_action": "restart_component"}, _ledger_with_controller_fault())
    assert d is None and errors


def test_single_piece_of_evidence_is_insufficient_for_repair():
    d, errors = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller",
                                    "evidence_ids": ["E1"], "recommended_action": "restart_component"}, _ledger_with_controller_fault())
    assert d is None and "at least 2" in errors[0]


def test_evidence_about_a_different_component_does_not_support_repair():
    d, errors = diagnosis.validate({"root_cause": "x", "faulty_component": "lidar_driver",
                                    "evidence_ids": ["E1", "E3"], "recommended_action": "restart_component"}, _ledger_with_controller_fault())
    assert d is None and "none of the cited evidence shows an anomaly in lidar_driver" in errors[0]


def test_non_anomalous_evidence_does_not_support_repair():
    l = EvidenceLedger()
    l.add(make_result("a", [("all fine", False, ["/base_controller"]), ("still fine", False, ["/base_controller"])]), 1)
    d, errors = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller",
                                    "evidence_ids": ["E1", "E2"], "recommended_action": "restart_component"}, l)
    assert d is None


def test_unknown_component_and_action_rejected():
    l = _ledger_with_controller_fault()
    d, e = diagnosis.validate({"root_cause": "x", "faulty_component": "rm -rf /", "evidence_ids": ["E1", "E3"],
                               "recommended_action": "restart_component"}, l)
    assert d is None and "not a component" in e[0]
    d, e = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller", "evidence_ids": ["E1", "E3"],
                               "recommended_action": "run_shell"}, l)
    assert d is None and "not allowlisted" in e[0]


def test_cannot_declare_healthy_with_unexplained_anomalies():
    l = _ledger_with_controller_fault()
    d, e = diagnosis.validate({"root_cause": "fine", "faulty_component": "none", "evidence_ids": ["E2"],
                               "recommended_action": "none"}, l)
    assert d is None and "unexplained anomalies" in e[0]


def test_healthy_allowed_when_no_anomalies():
    l = EvidenceLedger()
    l.add(make_result("a", [("all good", False, [])]), 1)
    d, e = diagnosis.validate({"root_cause": "no fault", "faulty_component": "none", "evidence_ids": ["E1"],
                               "recommended_action": "none"}, l)
    assert e == [] and d.status == "healthy" and d.recommended_action is None


def test_evidence_ids_given_as_string_are_tolerated():
    l = _ledger_with_controller_fault()
    d, e = diagnosis.validate({"root_cause": "x", "faulty_component": "base_controller",
                               "evidence_ids": "E1, E3", "recommended_action": "restart_component"}, l)
    assert e == [] and d is not None


def test_score_is_capped_and_conservative():
    l = EvidenceLedger()
    for i, tool in enumerate(["a", "b", "c", "d"]):
        l.add(make_result(tool, [(f"anomaly {i}", True, ["/base_controller"])]), i)
    ev = list(l.items.values())
    score, _ = diagnosis.evidence_score(ev, ev)
    assert score == 0.95
    assert diagnosis.evidence_score(ev[:1], ev[:1])[0] == 0.45
