"""API endpoint tests (FastAPI TestClient). Non-ROS tests never start the ROS client."""
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.agent.state import Investigation, Phase


@pytest.fixture
def api(tmp_audit, monkeypatch):
    monkeypatch.setattr(main.S, "audit", tmp_audit)
    main.S.investigations.clear()
    main.S.current = None
    return TestClient(main.app)          # no `with`: lifespan (ROS start, pollers) is not run


def test_tools_endpoint_lists_read_only_and_gated_actions(api):
    d = api.get("/api/tools").json()
    assert "inspect_topic" in {t["name"] for t in d["read_only"]}
    assert d["state_changing"] == [{"action": "restart_component", "description": d["state_changing"][0]["description"],
                                    "requires_approval": True}]
    assert set(d["repairable_components"]) >= {"base_controller", "lidar_driver"}


def test_faults_endpoint(api):
    assert set(api.get("/api/faults").json()["faults"]) == {"controller_crash", "lidar_failure", "tf_failure",
                                                            "topic_misconfig", "node_crash", "random"}


def test_inject_validates_fault_name(api):
    assert api.post("/api/faults/inject", json={"fault": "rm -rf"}).status_code == 422


def test_inject_random_does_not_leak_identity(api, monkeypatch):
    monkeypatch.setattr(main.supervisor, "inject", lambda f: {"ok": True, "injected": "random (hidden)"})
    r = api.post("/api/faults/inject", json={"fault": "random"})
    assert r.status_code == 200 and r.json() == {"ok": True, "injected": "hidden"}
    audit = api.get("/api/audit").json()
    assert [a for a in audit if a["kind"] == "fault_injected"][-1]["requested"] == "random"


def test_inject_reports_supervisor_down(api, monkeypatch):
    def down(_):
        raise ConnectionError("refused")
    monkeypatch.setattr(main.supervisor, "inject", down)
    assert api.post("/api/faults/inject", json={"fault": "lidar_failure"}).status_code == 503


def test_query_validation(api):
    assert api.post("/api/investigations", json={"query": ""}).status_code == 422
    assert api.post("/api/investigations", json={"query": "x" * 501}).status_code == 422
    assert api.post("/api/investigations", json={}).status_code == 422


def test_unknown_investigation_404(api):
    assert api.get("/api/investigations/nope").status_code == 404
    assert api.post("/api/investigations/nope/approve", json={"proposal_id": "x"}).status_code == 404


def test_only_one_investigation_at_a_time(api):
    main.S.current = Investigation("running")
    r = api.post("/api/investigations", json={"query": "another"})
    assert r.status_code == 409


def _awaiting():
    inv = Investigation("q")
    for p in (Phase.INVESTIGATING, Phase.DIAGNOSING, Phase.AWAITING_APPROVAL):
        inv.transition(p)
    prop = main.S.approvals.create(inv.id, "restart_component", "base_controller")
    inv.proposal = {"id": prop.id, "state": "pending"}
    main.S.investigations[inv.id] = inv
    return inv, prop


def test_approve_and_reject_flow_enforced(api):
    inv, prop = _awaiting()
    assert api.post(f"/api/investigations/{inv.id}/approve", json={"proposal_id": "wrong"}).status_code == 409
    assert prop.state == "pending"
    r = api.post(f"/api/investigations/{inv.id}/approve", json={"proposal_id": prop.id, "operator": "alice"})
    assert r.status_code == 200 and r.json()["state"] == "approved" and prop.decided_by == "alice"
    assert api.post(f"/api/investigations/{inv.id}/reject", json={"proposal_id": prop.id}).status_code == 409   # no flip-flop


def test_reject_endpoint(api):
    inv, prop = _awaiting()
    assert api.post(f"/api/investigations/{inv.id}/reject", json={"proposal_id": prop.id}).json()["state"] == "rejected"


def test_cannot_approve_when_not_awaiting(api):
    inv = Investigation("q")
    inv.proposal = {"id": "abc"}
    main.S.investigations[inv.id] = inv
    assert api.post(f"/api/investigations/{inv.id}/approve", json={"proposal_id": "abc"}).status_code == 409


def test_manual_tool_endpoint_uses_same_allowlist(api):
    r = api.post("/api/tools/restart_component", json={"args": {"target": "base_controller"}}).json()
    assert r["success"] is False and "non-read-only" in r["error"]
    r = api.post("/api/tools/inspect_topic", json={"args": {"topic": "/a; b"}}).json()
    assert r["success"] is False and "invalid argument" in r["error"]


def test_status_degrades_gracefully_when_everything_is_down(api, monkeypatch):
    monkeypatch.setattr(main.supervisor, "status", lambda t=0: (_ for _ in ()).throw(ConnectionError()))

    async def down():
        return {"reachable": False, "model": "m", "model_available": False}
    monkeypatch.setattr(main.llm, "status", down)
    d = api.get("/api/status").json()
    assert d["backend"] == "ok" and d["supervisor"]["reachable"] is False and d["llm"]["reachable"] is False


def test_websocket_hello_and_event_fanout(api):
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"


@pytest.mark.ros
def test_health_and_graph_on_live_robot():
    with TestClient(main.app) as c:
        import time
        time.sleep(4)
        h = c.get("/api/health").json()
        assert h["ros_available"] and set(h["components"]) == {"controller", "lidar", "odometry", "tf", "navigation", "ros_graph"}
        g = c.get("/api/graph").json()
        assert {n["id"] for n in g["nodes"]} >= {"/base_controller", "/lidar_driver"}
        assert any(e["source"] == "/velocity_commander" and e["target"] == "/cmd_vel" for e in g["edges"])
