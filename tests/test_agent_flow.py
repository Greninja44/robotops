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
    for _ in range(500):
        if inv.phase == Phase.AWAITING_APPROVAL or inv.done:
            break
        await asyncio.sleep(0.01)
    assert inv.phase == Phase.AWAITING_APPROVAL, (inv.phase, inv.error)
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
    agent, approvals = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4"))])
    inv = Investigation("q")
    task = asyncio.create_task(agent.run(inv))
    await approve_when_pending(inv, approvals, approve=False)
    await task
    assert inv.phase == Phase.REJECTED and env["executed"] == [] and inv.repair is None


async def test_invalid_diagnosis_is_fed_back_and_agent_recovers(env):
    agent, approvals = make_agent(env, [
        diag(ids=("E1",)),                    # too early (no checks of its own yet) and only 1 id -> rejected
        call("inspect_topic", topic="/cmd_vel"),
        call("get_component_status"),
        diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert any(e["kind"] == "diagnosis_rejected" for e in inv.events)
    assert inv.phase == Phase.RESOLVED


async def test_repeated_rejected_diagnoses_end_inconclusive_and_no_repair(env):
    agent, _ = make_agent(env, [diag(ids=("E99",))] * 5)
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
                                call("get_component_status"), diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.tool_calls == 3                                       # baseline + two real calls (the repeat was not run)
    assert inv.phase == Phase.RESOLVED


async def test_baseline_health_check_is_not_repeated_by_the_model(env):
    agent, _ = make_agent(env, [call("get_ros_health"), call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4"))],
                          auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.tool_calls == 3                                       # baseline + two real checks (the repeated baseline was not run)
    assert inv.phase == Phase.RESOLVED


async def test_unknown_tool_from_llm_is_a_failed_tool_not_an_execution(env):
    agent, _ = make_agent(env, [call("restart_component", target="base_controller"), call("inspect_topic", topic="/cmd_vel"), call("get_component_status"),
                                diag(ids=("E2", "E4"))], auto_approve=True)
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
    agent, _ = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4")),
                                call("get_component_status"), call("inspect_topic", topic="/cmd_vel"), diag(ids=("E2", "E7"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    assert inv.phase == Phase.REPAIR_FAILED and inv.round == 2
    assert env["executed"] == ["base_controller"] * 2                # bounded: exactly two attempts


async def test_healthy_diagnosis_with_no_anomalies(env, monkeypatch):
    monkeypatch.setattr(graph_mod.registry, "execute", lambda c, n, a: make_result(n, [("all good", False, [])]))
    agent, _ = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("list_nodes"), diag(component="none", ids=("E1",), action="none", cause="no fault")])
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
    agent, _ = make_agent(env, [bad, call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4"))], auto_approve=True)
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


async def test_diagnosis_from_a_single_tool_call_is_rejected_until_corroborated(env):
    # E2 and E3 both come from the automatic baseline (one tool call): not enough, the model must check something itself
    agent, _ = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E3")), diag(ids=("E2", "E4"))],
                          auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    rej = [e for e in inv.events if e["kind"] == "diagnosis_rejected"]
    assert rej and "2 different tool calls" in rej[0]["errors"][0]
    assert inv.tool_calls == 3 and inv.phase == Phase.RESOLVED


async def test_model_timeout_and_retry_are_visible_in_the_timeline(env):
    async def chat(messages, tools):
        notify = llm.retry_notifier.get()
        notify({"kind": "model_timeout", "final": False, "timeout_s": 60})
        return call("get_component_status")
    replies = [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4"))]
    scripted = Script(replies)

    async def wrapped(messages, tools):
        llm.retry_notifier.get()({"kind": "model_timeout", "final": False, "timeout_s": 60})
        return await scripted(messages, tools)
    agent, _ = make_agent(env, [], auto_approve=True)
    agent.chat = wrapped
    inv = Investigation("q")
    await agent.run(inv)
    assert [e for e in inv.events if e["kind"] == "model_timeout"] and inv.phase == Phase.RESOLVED


def test_json_decision_parsing_maps_to_tool_calls():
    tc, problem, reason = llm.parse_decision('{"reason_summary":"check consumer","action":"tool","tool":"inspect_topic",'
                                             '"arguments":{"topic":"/cmd_vel"}}')
    assert problem is None and tc.name == "inspect_topic" and tc.arguments == {"topic": "/cmd_vel", "reason": "check consumer"}
    tc, problem, _ = llm.parse_decision('{"reason_summary":"done","action":"diagnose","root_cause":"x",'
                                        '"faulty_component":"base_controller","evidence_ids":["E2","E4"],'
                                        '"recommended_action":"restart_component"}')
    assert tc.name == "submit_diagnosis" and tc.arguments["evidence_ids"] == ["E2", "E4"]
    for bad in ("", "{not json", "[1]", '{"action":"tool"}', '{"action":"explode"}'):
        assert llm.parse_decision(bad)[0] is None


def test_json_protocol_never_offers_state_changing_tools():
    schema = llm.decision_schema()
    assert "restart_component" not in schema["properties"]["tool"]["enum"]
    assert set(schema["properties"]["tool"]["enum"]) == set(graph_mod.registry.READ_ONLY_TOOLS)


def test_tool_results_are_sent_to_the_model_as_user_turns():
    out = llm._plain_messages([{"role": "system", "content": "s"}, {"role": "assistant", "content": "{}"},
                               {"role": "tool", "tool_name": "list_nodes", "content": "E4 ..."}])
    assert [m["role"] for m in out] == ["system", "assistant", "user"] and out[2]["content"].startswith("TOOL RESULT list_nodes")


def test_system_prompt_is_compact_and_contains_no_fault_information():
    from backend.agent import prompts
    text = prompts.system_prompt(10)
    assert len(text) < 3500
    for word in ("controller_crash", "lidar_failure", "tf_failure", "topic_misconfig", "node_crash", "inject"):
        assert word not in text
    assert "/cmd_vel" in text and "base_controller" in text        # architecture knowledge is there


async def test_conclusion_before_two_own_checks_is_refused(env):
    agent, _ = make_agent(env, [call("inspect_topic", topic="/cmd_vel"), diag(ids=("E2", "E4")),
                                call("get_component_status"), diag(ids=("E2", "E4"))], auto_approve=True)
    inv = Investigation("q")
    await agent.run(inv)
    rej = [e for e in inv.events if e["kind"] == "diagnosis_rejected"]
    assert rej and "at least 2 direct checks" in rej[0]["errors"][0]
    assert inv.phase == Phase.RESOLVED and inv.tool_calls == 3


def test_tool_arguments_are_normalized_to_declared_parameters():
    from backend.ros_tools import registry
    assert registry.normalize_args("list_nodes", {"node": "base_controller"}) == {}
    assert registry.normalize_args("inspect_topic", {"topic": "/cmd_vel", "node": "x", "duration": None}) == {"topic": "/cmd_vel"}


def test_used_parameterless_tools_are_removed_from_the_selectable_set(env):
    agent, _ = make_agent(env, [])
    names = lambda seen: {t["function"]["name"] for t in agent._selectable_tools(seen)}   # noqa: E731
    assert "list_nodes" in names({})
    n = names({"list_nodes{}": ["E4"], "get_ros_health{}": ["E1"]})
    assert "list_nodes" not in n and "get_ros_health" not in n and "inspect_topic" in n and "submit_diagnosis" in n
    assert "inspect_topic" in names({'inspect_topic{"topic": "/cmd_vel"}': ["E5"]})     # tools with arguments stay selectable


def test_decision_schema_narrows_the_tool_enum():
    assert llm.decision_schema(["inspect_topic"])["properties"]["tool"]["enum"] == ["inspect_topic"]


def test_diagnose_is_not_selectable_until_the_model_has_made_its_own_checks(env):
    agent, _ = make_agent(env, [])
    names = lambda tools: {t["function"]["name"] for t in tools}   # noqa: E731
    assert "submit_diagnosis" not in names(agent._selectable_tools({}, allow_diagnose=False))
    assert "submit_diagnosis" in names(agent._selectable_tools({}, allow_diagnose=True))
    assert names(agent._selectable_tools({}, only_diagnose=True)) == {"submit_diagnosis"}


def test_decision_schema_action_enum_follows_what_is_allowed():
    assert llm.decision_schema(["inspect_topic"], ["tool"])["properties"]["action"]["enum"] == ["tool"]
    assert llm.decision_schema(None, ["diagnose"])["properties"]["action"]["enum"] == ["diagnose"]
    assert set(llm.decision_schema()["properties"]["action"]["enum"]) == {"tool", "diagnose"}


async def test_the_model_is_offered_no_diagnose_action_until_two_checks_are_done(env):
    seen_offers = []

    async def chat(messages, tools):
        seen_offers.append({t["function"]["name"] for t in tools})
        return [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"), diag(ids=("E2", "E4"))][len(seen_offers) - 1]
    agent, _ = make_agent(env, [], auto_approve=True)
    agent.chat = chat
    inv = Investigation("q")
    await agent.run(inv)
    assert "submit_diagnosis" not in seen_offers[0] and "submit_diagnosis" not in seen_offers[1]
    assert "submit_diagnosis" in seen_offers[2] and inv.phase == Phase.RESOLVED


async def test_after_a_rejected_diagnosis_the_next_turn_must_be_a_new_check(env):
    offers = []
    script = [call("inspect_topic", topic="/cmd_vel"), call("get_component_status"),
              diag(ids=("E2", "E3")),                       # rejected: only baseline evidence (corroboration)
              call("inspect_topic", topic="/scan"), diag(ids=("E2", "E4"))]

    async def chat(messages, tools):
        offers.append("submit_diagnosis" in {t["function"]["name"] for t in tools})
        return script[len(offers) - 1]
    agent, _ = make_agent(env, [], auto_approve=True)
    agent.chat = chat
    inv = Investigation("q")
    await agent.run(inv)
    assert offers == [False, False, True, False, True]      # turn 4 (right after the rejection) cannot diagnose


async def test_a_verbatim_repeat_withholds_that_tool_and_does_not_burn_the_budget(env):
    offers = []
    script = [call("inspect_topic", topic="/cmd_vel"), call("inspect_topic", topic="/cmd_vel"),   # repeat
              call("get_component_status"), diag(ids=("E2", "E4"))]

    async def chat(messages, tools):
        offers.append({t["function"]["name"] for t in tools})
        return script[len(offers) - 1]
    agent, _ = make_agent(env, [], auto_approve=True, max_steps=3)
    agent.chat = chat
    inv = Investigation("q")
    await agent.run(inv)
    assert "inspect_topic" in offers[1]            # offered again (with other args it would be legitimate)
    assert "inspect_topic" not in offers[2]        # ... but withheld right after the verbatim repeat
    assert inv.phase == Phase.RESOLVED and inv.tool_calls == 3          # baseline + 2 real checks; budget of 3 not exhausted
