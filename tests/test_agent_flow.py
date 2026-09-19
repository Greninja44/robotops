"""Agent state machine driven by a scripted LLM and canned tool results (unit level)."""
import asyncio

import pytest
from conftest import make_result

from backend.agent import graph as graph_mod
from backend.agent import llm
from backend.agent.llm import LLMReply, ToolCall
from backend.agent.state import Investigation, Phase
from backend.ros_tools import repair as repair_mod
from backend.safety.approvals import ApprovalRegistry


def call(name, **args):
    return LLMReply(content="", tool_calls=[ToolCall(name, args)], raw_message={"role": "assistant", "content": ""})


def text(t="thinking"):
    return LLMReply(content=t, raw_message={"role": "assistant", "content": t})


def diag(component="base_controller", ids=("E2", "E4"), action="restart_component", cause="controller crashed"):
    return call("submit_diagnosis", root_cause=cause, faulty_component=component, evidence_ids=list(ids),
                recommended_action=action)


CANNED = {
    "get_ros_health": [("ROS graph reachable", False, []), ("1 expected node missing: /base_controller", True, ["/base_controller"]),
                       ("Diagnostics: 1 STALE", True, [])],
    "inspect_topic": [("/cmd_vel: expected subscriber /base_controller is missing", True, ["/cmd_vel", "/base_controller"])],
    "get_component_status": [("base_controller exited (code 1)", True, ["/base_controller"])],
}


class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def __call__(self, messages, tools):
        self.calls += 1
        if not self.replies:
            raise llm.LLMUnavailable("script exhausted")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def env(monkeypatch, tmp_audit):
    executed = []

    def fake_execute(client, name, args):
        if name not in CANNED:
            from backend.ros_tools.common import ToolResult
            return ToolResult(tool=name, args=args, success=False, error="unknown or non-read-only tool")
        return make_result(name, CANNED[name])
    monkeypatch.setattr(graph_mod.registry, "execute", fake_execute)
    monkeypatch.setattr(repair_mod.supervisor, "restart", lambda c: executed.append(c) or {"ok": True, "pid": 1})
    verdicts = [{"verified": True, "attempts": 1, "checks": [], "failed": []}]
    monkeypatch.setattr(graph_mod.verification, "verify", lambda client, target, on_attempt=None: verdicts.pop(0))
    return {"executed": executed, "verdicts": verdicts, "audit": tmp_audit}


def make_agent(env, replies, **kw):
    approvals = ApprovalRegistry()
    a = graph_mod.Agent(object(), approvals, env["audit"], chat=Script(replies), **kw)
    return a, approvals


async def approve_when_pending(inv, approvals, approve=True):
    while inv.phase != Phase.AWAITING_APPROVAL:
        await asyncio.sleep(0.01)
    approvals.decide(inv.proposal["id"], approve, by="test")


async def test_full_loop_resolves_only_after_approval_and_verification(env):
    agent, approvals = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag()])
    inv = Investigation("robot stopped")
    task = asyncio.create_task(agent.run(inv))
    await asyncio.sleep(0.3)
    assert inv.phase == Phase.AWAITING_APPROVAL and env["executed"] == []       # nothing executed before approval
    approvals.decide(inv.proposal["id"], True, by="test")
    await task
    assert inv.phase == Phase.RESOLVED and env["executed"] == ["base_controller"]
    assert inv.verification["verified"] and inv.tool_calls == 3
    kinds = [r["kind"] for r in env["audit"].tail(100)]
    for k in ("tool_call", "tool_result", "diagnosis_accepted", "repair_proposed", "approval_decision",
              "repair_executed", "verification", "investigation_finished"):
        assert k in kinds


async def test_rejected_proposal_executes_nothing(env):
    agent, approvals = make_agent(env, [call("get_component_status"), diag(ids=("E2", "E4"))])
    inv = Investigation("q")
    task = asyncio.create_task(agent.run(inv))
    await approve_when_pending(inv, approvals, approve=False)
    await task
    assert inv.phase == Phase.REJECTED and env["executed"] == [] and inv.repair is None


async def test_invalid_diagnosis_is_fed_back_and_agent_recovers(env):
    agent, approvals = make_agent(env, [
        diag(ids=("E1",)),                    # baseline E1 is non-anomalous, only 1 id -> rejected
        call("get_component_status"),
        diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert any(e["kind"] == "diagnosis_rejected" for e in inv.events)
    assert inv.phase == Phase.RESOLVED


async def test_three_rejected_diagnoses_end_inconclusive_and_no_repair(env):
    agent, _ = make_agent(env, [diag(ids=("E99",))] * 3)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.INCONCLUSIVE and env["executed"] == []


async def test_model_that_never_calls_tools_is_inconclusive(env):
    agent, _ = make_agent(env, [text()] * 5)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.INCONCLUSIVE and env["executed"] == []


async def test_step_budget_is_enforced(env):
    replies = [call("inspect_topic", topic=f"/t{i}") for i in range(30)]
    agent, _ = make_agent(env, replies, max_steps=3)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.tool_calls <= 1 + 3                                   # baseline + budget
    assert inv.phase == Phase.INCONCLUSIVE and agent.chat.calls <= 3 + 6 + 1


async def test_duplicate_calls_are_not_executed_twice(env):
    agent, _ = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("inspect_topic", topic="/cmd_vel"),
                                diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.tool_calls == 2                                       # baseline + one real call


async def test_unknown_tool_from_llm_is_a_failed_tool_not_an_execution(env):
    agent, _ = make_agent(env, [call("restart_component", target="base_controller"), diag(ids=("E2", "E3"))],
                          auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    failed = [e for e in inv.events if e["kind"] == "tool_result" and not e["success"]]
    assert failed and "unknown or non-read-only" in failed[0]["error"]
    assert env["executed"] == ["base_controller"]                    # only via the approved-proposal path


async def test_llm_outage_ends_in_error_without_side_effects(env):
    agent, _ = make_agent(env, [llm.LLMUnavailable("Ollama unreachable")])
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.ERROR and "LLM unavailable" in inv.error and env["executed"] == []
    assert inv.ledger.anomalies()                                    # baseline evidence retained


async def test_failed_verification_reinvestigates_once_then_stops_safely(env):
    env["verdicts"][:] = [{"verified": False, "attempts": 4, "checks": [], "failed": [{"check": "x", "detail": "still down"}]}] * 2
    agent, _ = make_agent(env, [diag(ids=("E2", "E3")), call("get_component_status"), diag(ids=("E2", "E4"))],
                          auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.REPAIR_FAILED and inv.round == 2
    assert env["executed"] == ["base_controller"] * 2                # bounded: exactly two attempts


async def test_healthy_diagnosis_with_no_anomalies(env, monkeypatch):
    monkeypatch.setattr(graph_mod.registry, "execute", lambda c, n, a: make_result(n, [("all good", False, [])]))
    agent, _ = make_agent(env, [diag(component="none", ids=("E1",), action="none", cause="no fault")])
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.HEALTHY and env["executed"] == []


async def test_ros_unavailable_at_baseline_ends_in_error(env, monkeypatch):
    from backend.ros_tools.common import ToolResult
    monkeypatch.setattr(graph_mod.registry, "execute",
                        lambda c, n, a: ToolResult(tool=n, args={}, success=False, error="ROS unavailable: x"))
    agent, _ = make_agent(env, [])
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.ERROR and "cannot observe" in inv.error


async def test_malformed_llm_tool_calls_do_not_crash(env):
    bad = LLMReply(content="", malformed=["arguments for x are not valid JSON"])
    agent, _ = make_agent(env, [bad, call("get_component_status"), diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert any(e["kind"] == "warning" for e in inv.events) and inv.phase == Phase.RESOLVED


def test_llm_output_parsing_handles_string_arguments_and_bad_json(monkeypatch):
    import httpx

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": "<think>secret</think>hi", "tool_calls": [
                {"function": {"name": "list_nodes", "arguments": "{}"}},
                {"function": {"name": "inspect_topic", "arguments": "{not json"}},
                {"function": {"name": 7, "arguments": {}}}]}, "eval_count": 5, "total_duration": 1_000_000_000}

    class Client:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return Resp()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    r = asyncio.run(llm.chat([], []))
    assert r.content == "hi" and [t.name for t in r.tool_calls] == ["list_nodes"] and len(r.malformed) == 2
    assert "secret" not in str(r.raw_message)                       # private reasoning never re-sent/stored


def test_llm_single_timeout_is_retried_once(monkeypatch):
    import httpx
    attempts = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": "ok"}, "eval_count": 1, "total_duration": 1}

    class Client:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k):
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ReadTimeout("stall")
            return Resp()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert asyncio.run(llm.chat([], [])).content == "ok" and len(attempts) == 2


def test_llm_timeout_and_connection_errors_map_to_unavailable(monkeypatch):
    import httpx

    class Client:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): raise httpx.ReadTimeout("t")
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(llm.LLMUnavailable, match="timed out .*2 attempts"):
        asyncio.run(llm.chat([], []))
