"""Sanitisation of LLM-supplied arguments + registry behaviour (no ROS needed)."""
import pytest

from backend.ros_tools import registry
from backend.ros_tools.common import InvalidArgument, clamp, frame_name, ros_name


@pytest.mark.parametrize("bad", ["/cmd_vel; rm -rf /", "$(reboot)", "../../etc/passwd", "/a b", "", "/cmd vel", "a\nb",
                                 "/x" * 100, None, 42, "/cmd_vel`id`", "/1abc"])
def test_ros_name_rejects_injection_and_garbage(bad):
    with pytest.raises(InvalidArgument):
        ros_name(bad)


def test_ros_name_normalises():
    assert ros_name("cmd_vel") == "/cmd_vel"
    assert ros_name(" /ns/base_controller ") == "/ns/base_controller"


def test_frame_name():
    assert frame_name("/base_link") == "base_link"
    with pytest.raises(InvalidArgument):
        frame_name("base link; ls")


def test_clamp():
    assert clamp(100, 1, 8, 3) == 8 and clamp(-5, 1, 8, 3) == 1 and clamp("x", 1, 8, 3) == 3


class _NoRos:
    def __getattr__(self, name):
        raise AssertionError("tool must not reach ROS for invalid input")


def test_registry_rejects_unknown_tool():
    r = registry.execute(_NoRos(), "restart_component", {"target": "base_controller"})
    assert not r.success and "unknown or non-read-only" in r.error


def test_registry_rejects_missing_required_args():
    r = registry.execute(_NoRos(), "inspect_topic", {})
    assert not r.success and "missing required" in r.error


def test_registry_drops_undeclared_arguments():
    r = registry.execute(_NoRos(), "inspect_topic", {"topic": "/x; ls", "shell": "true"})
    assert not r.success and "invalid argument" in r.error and "shell" not in r.args


def test_tool_specs_are_valid_json_schema_shaped():
    for spec in registry.llm_tool_specs():
        f = spec["function"]
        assert f["name"] in registry.READ_ONLY_TOOLS and f["description"]
        assert f["parameters"]["type"] == "object"
        assert set(f["parameters"]["required"]) <= set(f["parameters"]["properties"])


class _RosDown:
    def node_names(self):
        from backend.ros_tools.client import RosUnavailable
        raise RosUnavailable("no ros")
    topics = node_names


def test_ros_unavailable_becomes_structured_failure():
    r = registry.execute(_RosDown(), "list_nodes", {})
    assert not r.success and r.error.startswith("ROS unavailable") and r.findings == []
