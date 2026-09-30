"""Integration tests against the live demo robot (skipped when scripts/start_demo.sh is not running).

Only the LLM is scripted here; ROS, the supervisor, tools, faults, repair and verification are real.
"""
import asyncio
import time

import pytest

from backend.agent import graph as graph_mod
from backend.agent import verification
from backend.agent.llm import LLMReply, ToolCall
from backend.agent.state import Investigation, Phase
from backend.ros_tools import registry, supervisor
from backend.ros_tools.client import get_client
from backend.ros_tools.common import ToolResult
from backend.safety.approvals import ApprovalRegistry

pytestmark = pytest.mark.ros

CHECKS_TIMEOUT = 45


@pytest.fixture(scope="module")
def client():
    c = get_client()
    c.start()
    time.sleep(2.5)
    yield c
    supervisor.reset()


def wait_until(pred, timeout=CHECKS_TIMEOUT, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(every)
    return False


def healthy(client):
    return not [c for c in verification.run_checks(client, None) if not c["passed"]]


@pytest.fixture
def robot(client):
    """A freshly reset, verified-healthy robot."""
    supervisor.reset()
    time.sleep(3)
    assert wait_until(lambda: healthy(client), 60, 2), "demo robot did not become healthy after reset"
    return client


def run(client, name, **args) -> ToolResult:
    return registry.execute(client, name, args)


def anomalies(res):
    return [f.text for f in res.findings if f.anomaly]


# ------------------------------------------------------------------ tool schemas
def test_every_tool_returns_valid_structured_result(robot):
    calls = [("get_ros_health", {}), ("list_nodes", {}), ("list_topics", {}), ("inspect_node", {"node": "/base_controller"}),
             ("inspect_topic", {"topic": "/cmd_vel"}), ("measure_topic_rate", {"topic": "/odom", "duration": 1}),
             ("check_tf", {}), ("inspect_parameters", {"node": "/base_controller"}), ("get_recent_diagnostics", {}),
             ("get_recent_logs", {}), ("get_component_status", {}), ("check_sensor_data", {"topic": "/scan"}),
             ("inspect_lifecycle_state", {"node": "/safety_monitor"})]
    for name, args in calls:
        res = run(robot, name, **args)
        assert isinstance(res, ToolResult) and res.tool == name and res.success, (name, res.error)
        ToolResult.model_validate(res.model_dump())                    # schema round-trip
        assert res.findings, f"{name} produced no findings"


def test_healthy_baseline_has_no_anomalies_in_core_tools(robot):
    for name, args in [("list_nodes", {}), ("inspect_topic", {"topic": "/cmd_vel"}), ("check_tf", {}),
                       ("inspect_parameters", {"node": "/base_controller"}), ("get_recent_diagnostics", {}),
                       ("check_sensor_data", {"topic": "/scan"}), ("inspect_lifecycle_state", {"node": "/safety_monitor"})]:
        assert anomalies(run(robot, name, **args)) == [], name


def test_inspect_topic_matches_the_spec_example(robot):
    res = run(robot, "inspect_topic", topic="/cmd_vel")
    assert res.data["publisher_count"] == 1 and res.data["subscriber_count"] == 1
    assert res.data["type"] == "geometry_msgs/msg/Twist" and res.success


def test_measured_rates_are_real(robot):
    r = run(robot, "measure_topic_rate", topic="/scan", duration=3)
    assert 8 <= r.data["rate_hz"] <= 12
    r = run(robot, "measure_topic_rate", topic="/odom", duration=2)
    assert 15 <= r.data["rate_hz"] <= 25


def test_unknown_topic_and_node_are_handled(robot):
    r = run(robot, "inspect_topic", topic="/does_not_exist")
    assert r.success and r.data["exists"] is False
    r = run(robot, "inspect_node", node="/ghost")
    assert r.success and r.data["exists"] is False
    r = run(robot, "measure_topic_rate", topic="/ghost", duration=1)
    assert r.success and r.data["rate_hz"] == 0.0 and r.data["exists"] is False
    r = run(robot, "inspect_parameters", node="/ghost")
    assert r.success and r.data["exists"] is False
    r = run(robot, "check_tf", parent_frame="nowhere", child_frame="void")
    assert r.success and anomalies(r)


def test_bad_arguments_never_reach_ros(robot):
    r = run(robot, "inspect_topic", topic="/cmd_vel; reboot")
    assert not r.success and "invalid argument" in r.error


# ------------------------------------------------------------------ fault injection
def inject_and_wait(client, fault, symptom, timeout=25):
    assert supervisor.inject(fault)["ok"]
    assert wait_until(symptom, timeout, 1.0), f"{fault}: expected symptom never appeared"


def test_controller_crash_produces_real_symptoms(robot):
    inject_and_wait(robot, "controller_crash", lambda: "/base_controller" not in robot.node_names())
    assert run(robot, "inspect_topic", topic="/cmd_vel").data["subscriber_count"] == 0
    assert any("base_controller" in t for t in anomalies(run(robot, "list_nodes")))
    assert any("exited" in t for t in anomalies(run(robot, "get_component_status")))
    assert wait_until(lambda: any("FATAL" in t for t in anomalies(run(robot, "get_recent_logs"))), 10)


def test_lidar_failure_produces_real_symptoms(robot):
    inject_and_wait(robot, "lidar_failure", lambda: run(robot, "measure_topic_rate", topic="/scan", duration=1.5).data["rate_hz"] == 0)
    assert "/lidar_driver" in robot.node_names()                            # alive but silent
    assert run(robot, "inspect_topic", topic="/cmd_vel").data["subscriber_count"] == 1   # rest of robot healthy
    assert wait_until(lambda: any("lidar_driver" in t and "ERROR" in t for t in anomalies(run(robot, "get_recent_diagnostics"))), 10)


def test_tf_failure_produces_real_symptoms(robot):
    inject_and_wait(robot, "tf_failure", lambda: bool(anomalies(run(robot, "check_tf"))))
    assert run(robot, "measure_topic_rate", topic="/scan", duration=1.5).data["rate_hz"] > 5    # sensor still publishing
    assert "/tf_broadcaster" in robot.node_names()


def test_topic_misconfig_produces_real_symptoms(robot):
    # the misconfigured controller is relaunched: wait until it is back in the graph, subscribed elsewhere
    inject_and_wait(robot, "topic_misconfig", lambda: "/base_controller" in robot.node_names()
                    and run(robot, "inspect_topic", topic="/cmd_vel").data["subscriber_count"] == 0)
    assert "/base_controller" in robot.node_names()                         # alive but listening elsewhere
    p = run(robot, "inspect_parameters", node="/base_controller")
    assert p.data["parameters"]["cmd_vel_topic"] == "/cmd_vel_nav" and anomalies(p)
    assert any("/cmd_vel_nav" in t for t in anomalies(run(robot, "list_topics")))


def test_node_crash_produces_real_symptoms(robot):
    inject_and_wait(robot, "node_crash", lambda: "/obstacle_monitor" not in robot.node_names())
    assert run(robot, "inspect_topic", topic="/scan").data["subscriber_count"] == 0


def test_commander_stall_produces_real_symptoms(robot):
    """velocity_commander hangs: /cmd_vel goes silent, but base_controller (which complains) is not the cause -
    the agent has to trace the symptom upstream, past the node that reports it."""
    inject_and_wait(robot, "commander_stall", lambda: run(robot, "measure_topic_rate", topic="/cmd_vel", duration=1.5).data["rate_hz"] == 0)
    assert "/velocity_commander" in robot.node_names()                      # alive but silent
    assert any("/cmd_vel" in t and "not publishing" in t
              for t in anomalies(run(robot, "measure_topic_rate", topic="/cmd_vel", duration=1.5)))
    assert wait_until(lambda: any("base_controller" in t and "cmd_vel" in t
                                  for t in anomalies(run(robot, "get_recent_diagnostics"))), 10)   # the complaint, not the cause
    assert wait_until(lambda: any("velocity_commander" in t and "STALE" in t
                                  for t in anomalies(run(robot, "get_recent_diagnostics"))), 10)    # the actual cause


def test_odometry_stall_produces_real_symptoms(robot):
    """wheel_odometry hangs: /odom and the odom->base_link transform both go stale, but base_link->laser (the
    other TF edge) and the rest of the robot keep working - a different edge than tf_failure affects."""
    inject_and_wait(robot, "odometry_stall", lambda: run(robot, "measure_topic_rate", topic="/odom", duration=1.5).data["rate_hz"] == 0)
    assert "/wheel_odometry" in robot.node_names()                          # alive but silent
    tf = run(robot, "check_tf")
    assert any("odom" in t and "base_link" in t for t in anomalies(tf))
    assert not any("laser" in t for t in anomalies(tf))                     # the other edge is unaffected
    assert run(robot, "measure_topic_rate", topic="/wheel_states", duration=1.5).data["rate_hz"] > 5   # rest of robot healthy


def test_sensor_drift_produces_real_symptoms(robot):
    """lidar_driver keeps publishing /scan at its normal rate - measure_topic_rate alone sees nothing wrong.
    Only content-level inspection (check_sensor_data) catches it: every beam saturates at range_min."""
    inject_and_wait(robot, "sensor_drift",
                    lambda: run(robot, "check_sensor_data", topic="/scan").data.get("distinct_values") == 1)
    assert "/lidar_driver" in robot.node_names()                            # alive, not crashed
    rate = run(robot, "measure_topic_rate", topic="/scan", duration=1.5)
    assert rate.data["rate_hz"] > 5 and anomalies(rate) == []               # rate alone looks perfectly healthy
    content = run(robot, "check_sensor_data", topic="/scan")
    assert content.data["distinct_values"] == 1 and content.data["min_range"] == content.data["max_range"]
    assert any("frozen" in t or "saturated" in t for t in anomalies(content))
    assert wait_until(lambda: any("lidar_driver" in t and ("frozen" in t or "stuck" in t)
                                  for t in anomalies(run(robot, "get_recent_diagnostics"))), 10)


def test_lifecycle_stall_produces_real_symptoms(robot):
    """safety_monitor deactivates and refuses to reactivate: it stays in the ROS graph and keeps publishing
    diagnostics (so it isn't "crashed"), but only inspect_lifecycle_state names the actual state; presence
    and diagnostics alone only say something is wrong, not precisely what."""
    inject_and_wait(robot, "lifecycle_stall",
                    lambda: run(robot, "inspect_lifecycle_state", node="/safety_monitor").data.get("state") == "inactive")
    assert "/safety_monitor" in robot.node_names()                          # alive, not crashed
    assert run(robot, "measure_topic_rate", topic="/safety_status", duration=1.5).data["rate_hz"] == 0
    state = run(robot, "inspect_lifecycle_state", node="/safety_monitor")
    assert state.data["state"] == "inactive" and state.data["expected_state"] == "active"
    assert any("inactive" in t and "active" in t for t in anomalies(state))
    assert wait_until(lambda: any("safety_monitor" in t and "inactive" in t
                                  for t in anomalies(run(robot, "get_recent_diagnostics"))), 10)


def test_random_fault_response_does_not_reveal_identity(robot):
    res = supervisor.inject("random")
    assert res["ok"] and res["injected"] == "random (hidden)"
    assert not any(f in str(res) for f in ("controller_crash", "lidar_failure", "tf_failure", "topic_misconfig",
                                           "node_crash", "commander_stall", "odometry_stall", "sensor_drift",
                                           "lifecycle_stall"))


def test_unknown_fault_rejected(robot):
    assert supervisor.inject("bogus")["ok"] is False


def test_reset_restores_healthy_baseline(robot):
    supervisor.inject("controller_crash")
    time.sleep(6)
    assert not healthy(robot)
    supervisor.reset()
    assert wait_until(lambda: healthy(robot), 60, 2)


# ------------------------------------------------------------------ verification is independent of exit codes
def test_verification_fails_while_broken_and_passes_after_real_repair(robot):
    supervisor.inject("controller_crash")
    time.sleep(6)
    bad = verification.run_checks(robot, "base_controller")
    failed = {c["check"] for c in bad if not c["passed"]}
    assert "/base_controller running" in failed and "/base_controller subscribed to /cmd_vel" in failed
    assert "odometry changing (robot moving)" in failed
    res = verification.verify(robot, "base_controller", attempts=1, settle_s=0.1)
    assert res["verified"] is False and res["failed"]                       # broken robot cannot verify

    assert supervisor.restart("base_controller")["ok"]
    ok = verification.verify(robot, "base_controller", attempts=5, settle_s=3)
    assert ok["verified"] is True and not ok["failed"]


def test_process_restart_alone_is_not_recovery_for_topic_misconfig(robot):
    """Exit-code-0 style success is not enough: restarting the wrong component must not verify."""
    supervisor.inject("topic_misconfig")
    time.sleep(6)
    assert supervisor.restart("lidar_driver")["ok"]                         # "successful" command, wrong fix
    res = verification.verify(robot, "lidar_driver", attempts=2, settle_s=2)
    assert res["verified"] is False
    assert any("/base_controller subscribed to /cmd_vel" == c["check"] for c in res["failed"])


# ------------------------------------------------------------------ end-to-end with a scripted LLM
class Script:
    def __init__(self, steps):
        self.steps = list(steps)

    async def __call__(self, messages, tools):
        s = self.steps.pop(0)
        if callable(s):
            s = s()
        return LLMReply(content="", tool_calls=[ToolCall(s[0], s[1])], raw_message={"role": "assistant", "content": ""})


async def test_end_to_end_controller_crash_flow_on_real_ros(robot, tmp_audit):
    supervisor.inject("controller_crash")
    await asyncio.sleep(6)
    approvals = ApprovalRegistry()
    holder = {}

    def submit():
        ids = [e.id for e in holder["inv"].ledger.anomalies() if "/base_controller" in e.subjects][:3]
        return ("submit_diagnosis", {"root_cause": "base_controller crashed", "faulty_component": "base_controller",
                                     "evidence_ids": ids, "recommended_action": "restart_component"})
    agent = graph_mod.Agent(robot, approvals, tmp_audit,
                            chat=Script([("inspect_topic", {"topic": "/cmd_vel"}), ("list_nodes", {}), submit]))
    inv = Investigation("My robot stopped moving", listener=lambda m: None)
    holder["inv"] = inv
    task = asyncio.create_task(agent.run(inv))
    for _ in range(300):
        if inv.phase == Phase.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.1)
    assert inv.phase == Phase.AWAITING_APPROVAL, (inv.phase, inv.error)
    assert "/base_controller" not in robot.node_names()                     # nothing repaired before approval
    approvals.decide(inv.proposal["id"], True, by="test")
    await asyncio.wait_for(task, 90)
    assert inv.phase == Phase.RESOLVED and inv.verification["verified"]
    assert await asyncio.to_thread(healthy, robot)


async def test_rejected_repair_leaves_robot_untouched(robot, tmp_audit):
    supervisor.inject("controller_crash")
    await asyncio.sleep(6)
    approvals = ApprovalRegistry()
    holder = {}

    def submit():
        ids = [e.id for e in holder["inv"].ledger.anomalies() if "/base_controller" in e.subjects][:3]
        return ("submit_diagnosis", {"root_cause": "crash", "faulty_component": "base_controller",
                                     "evidence_ids": ids, "recommended_action": "restart_component"})
    agent = graph_mod.Agent(robot, approvals, tmp_audit,
                            chat=Script([("list_nodes", {}), ("inspect_topic", {"topic": "/cmd_vel"}), submit]))   # 2 own checks before a diagnosis is admitted
    inv = Investigation("q", listener=lambda m: None)
    holder["inv"] = inv
    task = asyncio.create_task(agent.run(inv))
    for _ in range(300):
        if inv.phase == Phase.AWAITING_APPROVAL or inv.done:
            break
        await asyncio.sleep(0.1)
    assert inv.phase == Phase.AWAITING_APPROVAL, (inv.phase, inv.error)
    approvals.decide(inv.proposal["id"], False)
    await task
    assert inv.phase == Phase.REJECTED and "/base_controller" not in robot.node_names()
