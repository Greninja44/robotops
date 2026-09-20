"""Catalogue of read-only diagnostic tools the agent may call automatically.

Only tools listed here can be invoked by the LLM. Arguments are filtered to the
declared schema before the call; the tool functions validate values themselves.
State-changing actions are NOT here - see repair.py.
"""
from __future__ import annotations

from . import diagnostics, logs, nodes, tf, topics
from .common import ToolResult

_TOPIC = {"topic": {"type": "string", "description": "Absolute topic name, e.g. /cmd_vel"}}
_NODE = {"node": {"type": "string", "description": "Absolute node name, e.g. /base_controller"}}

READ_ONLY_TOOLS = {
    "get_ros_health": (nodes.get_ros_health,
                       "Whole-system overview: node/topic counts, missing expected nodes, diagnostics summary.", {}, []),
    "list_nodes": (nodes.list_nodes, "List running ROS nodes and which expected nodes are missing.", {}, []),
    "list_topics": (topics.list_topics,
                    "List active topics with publisher/subscriber counts; flags unexpected or orphaned topics.", {}, []),
    "inspect_node": (nodes.inspect_node,
                     "Show what a node publishes and subscribes, compared with the robot manifest.", _NODE, ["node"]),
    "inspect_topic": (topics.inspect_topic,
                      "Show a topic's type, publishers and subscribers, compared with the robot manifest.",
                      _TOPIC, ["topic"]),
    "measure_topic_rate": (topics.measure_topic_rate,
                           "Subscribe to a topic for a few seconds and measure its real message rate in Hz.",
                           {**_TOPIC, "duration": {"type": "number", "description": "seconds, 1-2.5 (default 2)"}},
                           ["topic"]),
    "check_tf": (tf.check_tf,
                 "Check that TF transforms are available and fresh. Omit both frames to check all expected transforms.",
                 {"parent_frame": {"type": "string"}, "child_frame": {"type": "string"}}, []),
    "inspect_parameters": (nodes.inspect_parameters,
                           "Read a node's parameters and compare them with the robot manifest.", _NODE, ["node"]),
    "get_recent_diagnostics": (diagnostics.get_recent_diagnostics,
                               "Latest /diagnostics status (OK/WARN/ERROR/STALE) reported by every component.", {}, []),
    "get_recent_logs": (logs.get_recent_logs,
                        "Recent WARN/ERROR/FATAL messages from /rosout, optionally for one node.",
                        {"node": {"type": "string", "description": "optional node filter"}}, []),
    "get_component_status": (nodes.get_component_status,
                             "Process manager view: which component processes are running or have exited (exit codes).",
                             {}, []),
}

# Implemented but not offered to the LLM (inspect_topic covers them); used by UI/tests.
EXTRA_TOOLS = {"get_publishers": topics.get_publishers, "get_subscribers": topics.get_subscribers}


def llm_tool_specs() -> list[dict]:
    specs = []
    for name, (_, desc, props, required) in READ_ONLY_TOOLS.items():
        specs.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return specs


def normalize_args(name: str, args: dict | None) -> dict:
    """Keep only the arguments a tool declares (models sometimes pass irrelevant ones); drop empty values."""
    if name not in READ_ONLY_TOOLS:
        return dict(args or {})
    props = READ_ONLY_TOOLS[name][2]
    return {k: v for k, v in (args or {}).items() if k in props and v not in (None, "")}


def execute(client, name: str, args: dict | None) -> ToolResult:
    if name not in READ_ONLY_TOOLS:
        return ToolResult(tool=name, args=args or {}, success=False,
                          error=f"unknown or non-read-only tool {name!r}; allowed: {', '.join(READ_ONLY_TOOLS)}")
    fn, _, props, required = READ_ONLY_TOOLS[name]
    args = {k: v for k, v in (args or {}).items() if k in props and v not in (None, "")}
    missing = [k for k in required if k not in args]
    if missing:
        return ToolResult(tool=name, args=args, success=False, error=f"missing required argument(s): {missing}")
    return fn(client, **args)
