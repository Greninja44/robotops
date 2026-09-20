"""The agent must discover faults from ROS evidence only: no fault identity may reach it.

Runtime audit of real runs is done with ROBOTOPS_AUDIT_PROMPTS=1 (scripts/ui_random_demo.py scans every recorded model input);
these tests pin the structural guarantees.
"""
import re
from pathlib import Path

from backend.ros_tools import nodes as nodes_tools

ROOT = Path(__file__).resolve().parents[1]
FAULT_IDS = ("controller_crash", "lidar_failure", "tf_failure", "topic_misconfig", "node_crash")
# Code the agent's inputs are built from. backend/main.py (the injection API) is deliberately NOT in this list.
AGENT_SIDE = ["backend/agent", "backend/ros_tools", "backend/safety", "backend/monitor.py", "backend/readiness.py"]


def sources():
    for rel in AGENT_SIDE:
        p = ROOT / rel
        yield from ([p] if p.is_file() else sorted(p.rglob("*.py")))


def test_no_agent_side_module_knows_fault_ids_or_reads_ground_truth():
    offenders = []
    for f in sources():
        text = f.read_text()
        for needle in (*FAULT_IDS, "ground_truth"):
            if re.search(rf"\b{needle}\b", text):
                offenders.append(f"{f.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_only_the_demo_supervisor_writes_ground_truth():
    writers = [str(f.relative_to(ROOT)) for f in ROOT.rglob("*.py")
               if ".venv" not in f.parts and "tests" not in f.parts and "ground_truth" in f.read_text()]
    assert sorted(writers) == ["demo_robot/supervisor.py", "scripts/ui_random_demo.py"]   # supervisor writes; harness reads AFTER the run


def test_process_status_tool_never_exposes_launch_arguments(monkeypatch):
    """launch_args would reveal a topic_misconfig (`-p cmd_vel_topic:=/cmd_vel_nav`); the tool must not pass it on."""
    monkeypatch.setattr(nodes_tools.supervisor, "status", lambda *a, **k: {
        "base_controller": {"state": "running", "pid": 5, "exit_code": None, "restarts": 1, "uptime_s": 3.0,
                            "launch_args": ["-p", "cmd_vel_topic:=/cmd_vel_nav"]}})
    res = nodes_tools.get_component_status(client=None)
    blob = res.model_dump_json()
    assert "launch_args" not in blob and "cmd_vel_nav" not in blob


def test_random_injection_response_is_hidden_at_every_layer():
    from backend.ros_tools import supervisor as sup_client
    assert "hidden" in (ROOT / "demo_robot" / "supervisor.py").read_text()      # supervisor response
    assert "hidden" in (ROOT / "backend" / "main.py").read_text()               # API response
    assert not hasattr(sup_client, "ground_truth")
