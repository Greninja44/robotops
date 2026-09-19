"""Node-level diagnostic tools (read-only)."""
from __future__ import annotations

import time

from . import supervisor
from .common import manifest, node_of, ros_name, suggest, tool


@tool("list_nodes")
def list_nodes(client, r):
    nodes = client.node_names()
    expected = list(manifest()["nodes"])
    missing = [n for n in expected if n not in nodes]
    unexpected = [n for n in nodes if n not in expected]
    r.data.update(nodes=nodes, count=len(nodes), expected_missing=missing, unexpected=unexpected)
    for n in missing:
        r.anomaly(f"Expected node {n} ({manifest()['nodes'][n]['role']}) is NOT present in the ROS graph", n)
    if not missing:
        r.normal(f"All {len(expected)} expected nodes are present in the ROS graph", *expected)
    for n in unexpected:
        r.normal(f"Unexpected extra node {n} is running", n)


@tool("inspect_node")
def inspect_node(client, r, node: str):
    node = ros_name(node, "node")
    r.args["node"] = node
    nodes = client.node_names()
    expected_meta = manifest()["nodes"].get(node)
    if node not in nodes:
        r.data.update(exists=False, similar=suggest(node, nodes), expected=expected_meta is not None)
        if expected_meta:
            r.anomaly(f"Node {node} ({expected_meta['role']}) is expected but NOT running", node)
        else:
            r.normal(f"Node {node} does not exist (not part of this robot); similar: {r.data['similar']}", node)
        return
    iface = client.node_interfaces(node)
    hide = {"/rosout", "/parameter_events"}
    pubs = {t: ty for t, ty in iface["publishers"].items() if t not in hide}
    subs = {t: ty for t, ty in iface["subscribers"].items() if t not in hide}
    r.data.update(exists=True, publishes=pubs, subscribes=subs,
                  service_count=len(iface["services"]), role=(expected_meta or {}).get("role"))
    r.normal(f"Node {node} is running; publishes {sorted(pubs)}; subscribes {sorted(subs)}", node)
    if not expected_meta:
        return
    for topic, t in manifest()["topics"].items():
        if node in t["publishers"] and topic not in pubs:
            r.anomaly(f"Node {node} does not publish {topic} although the robot manifest expects it to", node, topic)
        if node in t["subscribers"] and topic not in subs:
            r.anomaly(f"Node {node} does not subscribe to {topic} although the robot manifest expects it to", node, topic)
    known = set(manifest()["topics"]) | {"/diagnostics", "/tf", "/tf_static"}
    for topic in sorted(set(subs) - known):
        r.anomaly(f"Node {node} subscribes to {topic}, which is not a topic in the robot manifest", node, topic)
    for topic in sorted(set(pubs) - known):
        r.anomaly(f"Node {node} publishes {topic}, which is not a topic in the robot manifest", node, topic)


@tool("inspect_parameters")
def inspect_parameters(client, r, node: str):
    node = ros_name(node, "node")
    r.args["node"] = node
    if node not in client.node_names():
        r.data.update(exists=False)
        r.anomaly(f"Cannot read parameters: node {node} is not running", node)
        return
    params = client.get_parameters(node)
    r.data.update(exists=True, parameters=params)
    expected = manifest().get("parameters", {}).get(node, {})
    mismatched = False
    for k, v in expected.items():
        if k in params and params[k] != v:
            mismatched = True
            r.anomaly(f"Parameter {node}.{k} = {params[k]!r} but the robot manifest expects {v!r}", node, k,
                      *([params[k]] if isinstance(params[k], str) and params[k].startswith("/") else []))
    r.normal(f"{node} parameters: " + ", ".join(f"{k}={v!r}" for k, v in params.items()), node)
    if expected and not mismatched:
        r.normal(f"{node} parameters match the robot manifest", node)


@tool("get_component_status")
def get_component_status(client, r):
    """Process manager view (like `systemctl status`), from the demo supervisor."""
    comps = supervisor.status()
    r.data["components"] = {n: {k: c[k] for k in ("state", "pid", "exit_code", "restarts", "uptime_s")}
                            for n, c in comps.items()}
    for name, c in comps.items():
        node = node_of(name) or name
        if c["state"] != "running":
            r.anomaly(f"Process for {name} has {c['state']} (exit code {c['exit_code']})", node)
    running = [n for n, c in comps.items() if c["state"] == "running"]
    if running:
        r.normal(f"Processes running: {', '.join(running)}", *[node_of(n) or n for n in running])


@tool("get_ros_health")
def get_ros_health(client, r):
    """Coarse whole-system check: graph reachable, expected nodes, diagnostics levels."""
    nodes = client.node_names()
    expected = list(manifest()["nodes"])
    missing = [n for n in expected if n not in nodes]
    topics = client.topics()
    now = time.monotonic()
    levels = {"OK": 0, "WARN": 0, "ERROR": 0, "STALE": 0}
    for d in client.diagnostics.values():
        if now - d.received > 3.0:
            levels["STALE"] += 1
        else:
            levels[{0: "OK", 1: "WARN", 2: "ERROR"}.get(d.level, "STALE")] += 1
    r.data.update(ros_graph_reachable=True, node_count=len(nodes), topic_count=len(topics),
                  expected_nodes_missing=missing, diagnostics_levels=levels)
    r.normal(f"ROS graph reachable: {len(nodes)} nodes, {len(topics)} topics")
    if missing:
        r.anomaly(f"{len(missing)} expected node(s) missing: {', '.join(missing)}", *missing)
    else:
        r.normal("All expected nodes present")
    if levels["ERROR"] or levels["WARN"] or levels["STALE"]:
        r.anomaly(f"Diagnostics: {levels['ERROR']} ERROR, {levels['WARN']} WARN, {levels['STALE']} STALE, "
                  f"{levels['OK']} OK (use get_recent_diagnostics for details)")
    else:
        r.normal(f"Diagnostics: all {levels['OK']} statuses OK")
