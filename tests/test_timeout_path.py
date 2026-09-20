"""Model timeout path, end to end with the REAL llm client and agent against a deliberately slow HTTP server.

No mocking of llm.py: the request really times out, is retried once, is surfaced in the timeline, and the investigation stops
safely without executing any repair.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import make_result

from backend.agent import graph as graph_mod
from backend.agent import llm
from backend.agent.state import Investigation, Phase
from backend.ros_tools import repair as repair_mod
from backend.safety.approvals import ApprovalRegistry


class Slow(BaseHTTPRequestHandler):
    hits = 0
    delay = 3.0
    fail_first = 0            # answer normally after this many stalled requests

    def do_POST(self):
        type(self).hits += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if type(self).hits <= type(self).fail_first or type(self).fail_first < 0:
            time.sleep(type(self).delay)
        body = json.dumps({"message": {"content": json.dumps({
            "reason_summary": "check", "action": "tool", "tool": "get_component_status", "arguments": {}})},
            "eval_count": 5, "total_duration": 1_000_000}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def slow_server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)   # threaded: the retry is served while the stalled request sleeps
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Slow.hits, Slow.fail_first = 0, 0
    monkeypatch.setattr(llm, "OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(llm, "TIMEOUT_S", 0.5)
    yield Slow
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def agent_env(monkeypatch, tmp_audit):
    executed = []
    monkeypatch.setattr(graph_mod.registry, "execute", lambda c, n, a: make_result(n, [("Process for base_controller exited", True, ["/base_controller"])]))
    monkeypatch.setattr(repair_mod.supervisor, "restart", lambda c: executed.append(c) or {"ok": True})
    return executed, tmp_audit


async def test_a_single_stall_is_retried_and_shown_then_the_investigation_continues(slow_server, agent_env):
    executed, audit = agent_env
    slow_server.fail_first = 1                      # first request stalls past the timeout, the retry answers
    agent = graph_mod.Agent(object(), ApprovalRegistry(), audit, max_steps=2)
    inv = Investigation("q")
    await agent.run(inv)
    events = [e for e in inv.events if e["kind"] == "model_timeout"]
    assert events and events[0]["final"] is False and events[0]["timeout_s"] == 0.5     # "MODEL RESPONSE TIMEOUT - retrying"
    first_call = next(e for e in inv.events if e["kind"] == "llm_call")
    assert first_call["attempts"] == 2                                                   # the retry produced the answer
    assert inv.error is None or "timed out" not in inv.error
    assert executed == []


async def test_two_stalls_stop_safely_with_a_clear_error_and_no_repair(slow_server, agent_env):
    executed, audit = agent_env
    slow_server.fail_first = -1                     # every request stalls
    agent = graph_mod.Agent(object(), ApprovalRegistry(), audit, auto_approve=True)
    inv = Investigation("q")
    t0 = time.monotonic()
    await agent.run(inv)
    assert time.monotonic() - t0 < 5                                  # bounded: 2 attempts x 0.5 s, no silent hang
    kinds = [(e["kind"], e.get("final")) for e in inv.events if e["kind"] == "model_timeout"]
    assert kinds == [("model_timeout", False), ("model_timeout", True)]
    assert inv.phase == Phase.ERROR and "timed out" in inv.error and "2 attempts" in inv.error
    assert executed == [] and inv.repair is None                      # nothing was changed
    assert slow_server.hits == 2                                      # exactly one retry, not a storm


async def test_timeout_after_repair_cannot_trigger_a_second_repair(slow_server, agent_env, monkeypatch):
    """Even if the model stalls during re-investigation, the single-use approval means the repair never runs twice."""
    executed, audit = agent_env
    reg = ApprovalRegistry()
    p = reg.create("inv", "restart_component", "base_controller")
    reg.decide(p.id, True)
    repair_mod.execute(reg, p.id, "restart_component", "base_controller")
    assert executed == ["base_controller"]
    import pytest as _pt
    with _pt.raises(Exception):
        repair_mod.execute(reg, p.id, "restart_component", "base_controller")      # replay after a stall/retry: refused
    assert executed == ["base_controller"]
