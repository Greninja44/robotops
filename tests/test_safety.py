"""Approval enforcement, repair allowlisting, audit log."""
import json

import pytest

from backend.ros_tools import repair
from backend.safety import policies
from backend.safety.approvals import ApprovalError, ApprovalRegistry


def test_allowlist_rejects_unknown_action_and_target():
    with pytest.raises(policies.PolicyViolation):
        policies.check_action("run_shell", "base_controller")
    with pytest.raises(policies.PolicyViolation):
        policies.check_action("restart_component", "/bin/sh")
    with pytest.raises(policies.PolicyViolation):
        policies.check_action("restart_component", "base_controller; rm -rf /")
    assert policies.check_action("restart_component", "base_controller")["requires_approval"] is True


def test_every_repairable_component_is_a_manifest_component_and_has_risk():
    assert set(policies.REPAIRABLE) == set(policies.components())
    assert all(v["risk"] in ("low", "medium", "high") for v in policies.REPAIRABLE.values())


def test_set_parameter_is_allowlisted_for_one_component_only():
    """A second, narrower guarded repair primitive: only base_controller, to a fixed, ordered list of canonical
    (parameter, value) pairs - the model picks the target, never the value."""
    assert set(policies.PARAMETER_FIX) <= set(policies.components())
    pol = policies.check_action("set_parameter", "base_controller")
    assert pol == {"action": "set_parameter", "target": "base_controller", "risk": "low", "requires_approval": True}
    with pytest.raises(policies.PolicyViolation, match="no allowlisted parameter fix"):
        policies.check_action("set_parameter", "lidar_driver")          # restart_component-only component
    with pytest.raises(policies.PolicyViolation):
        policies.check_action("set_parameter", "/bin/sh")


def test_expected_result_is_action_aware():
    restart_text = policies.expected_result("restart_component", "base_controller")
    param_text = policies.expected_result("set_parameter", "base_controller")
    assert restart_text and param_text and restart_text != param_text
    assert policies.expected_result("set_parameter", "lidar_driver") == ""      # no fix defined -> no claim made


def test_set_parameter_repair_calls_the_ros_client_not_the_supervisor(monkeypatch):
    calls = []
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: pytest.fail("must not restart the process"))

    class FakeClient:
        def set_parameters(self, full_name, values):
            calls.append((full_name, values))
            return {"results": [{"name": k, "successful": True, "reason": ""} for k in values], "all_successful": True}
    monkeypatch.setattr(repair.ros_client, "get_client", lambda: FakeClient())
    reg = ApprovalRegistry()
    p = reg.create("inv", "set_parameter", "base_controller")
    reg.decide(p.id, True)
    res = repair.execute(reg, p.id, "set_parameter", "base_controller")
    assert res["executed"] is True and res["action"] == "set_parameter"
    assert calls == [("/base_controller", {"cmd_vel_topic": "/cmd_vel", "max_wheel_speed": 12.0})]


def test_set_parameter_repair_reports_failure_without_raising(monkeypatch):
    def boom(full_name, values):
        raise TimeoutError("parameter service of /base_controller not available")

    class FakeClient:
        set_parameters = staticmethod(boom)
    monkeypatch.setattr(repair.ros_client, "get_client", lambda: FakeClient())
    reg = ApprovalRegistry()
    p = reg.create("inv", "set_parameter", "base_controller")
    reg.decide(p.id, True)
    res = repair.execute(reg, p.id, "set_parameter", "base_controller")
    assert res["executed"] is False and "TimeoutError" in res["error"]


def test_repair_refused_without_approval(monkeypatch):
    calls = []
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: calls.append(c) or {"ok": True})
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    with pytest.raises(ApprovalError):
        repair.execute(reg, p.id, "restart_component", "base_controller")     # still pending
    assert calls == []


def test_repair_refused_after_rejection(monkeypatch):
    calls = []
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: calls.append(c) or {"ok": True})
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, False)
    with pytest.raises(ApprovalError):
        repair.execute(reg, p.id, "restart_component", "base_controller")
    assert calls == []


def test_repair_refused_for_unknown_proposal(monkeypatch):
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: pytest.fail("must not run"))
    with pytest.raises(ApprovalError):
        repair.execute(ApprovalRegistry(), "nope", "restart_component", "base_controller")


def test_approval_is_bound_to_exact_action_and_target(monkeypatch):
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: pytest.fail("must not run"))
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, True)
    with pytest.raises(ApprovalError, match="differ"):
        repair.execute(reg, p.id, "restart_component", "lidar_driver")    # approved X, asked to do Y


def test_approved_repair_runs_once_only(monkeypatch):
    calls = []
    monkeypatch.setattr(repair.supervisor, "restart", lambda c: calls.append(c) or {"ok": True, "pid": 1})
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, True, by="alice")
    res = repair.execute(reg, p.id, "restart_component", "base_controller")
    assert res["executed"] and calls == ["base_controller"]
    with pytest.raises(ApprovalError):                                       # replay
        repair.execute(reg, p.id, "restart_component", "base_controller")
    assert calls == ["base_controller"]


def test_supervisor_failure_is_reported_not_raised(monkeypatch):
    def boom(_):
        raise ConnectionError("down")
    monkeypatch.setattr(repair.supervisor, "restart", boom)
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, True)
    res = repair.execute(reg, p.id, "restart_component", "base_controller")
    assert res["executed"] is False and "ConnectionError" in res["error"]


def test_decisions_cannot_be_changed_or_repeated():
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, False)
    with pytest.raises(ApprovalError):
        reg.decide(p.id, True)
    with pytest.raises(ApprovalError):
        reg.decide("missing", True)


def test_llm_tool_list_contains_no_state_changing_tool():
    from backend.agent import graph
    names = {t["function"]["name"] for t in graph._tool_specs()}
    assert "submit_diagnosis" in names
    assert not any(n.startswith(("restart", "kill", "set_", "launch", "exec", "run_")) for n in names)
    from backend.ros_tools import registry
    assert not set(registry.READ_ONLY_TOOLS) & {"restart_component", "execute", "repair"}


def test_audit_log_is_append_only_jsonl(tmp_audit):
    tmp_audit.write("i1", "tool_call", tool="list_nodes", args={})
    tmp_audit.write("i1", "approval_decision", state="approved")
    lines = tmp_audit.path.read_text().splitlines()
    assert [json.loads(l)["kind"] for l in lines] == ["tool_call", "approval_decision"]
    assert all({"ts", "time", "investigation"} <= set(json.loads(l)) for l in lines)
    assert len(tmp_audit.tail(1)) == 1
