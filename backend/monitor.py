"""Live system monitor for the dashboard (independent of the agent).

Passively tracks message arrival on the manifest topics and builds:
  * component health (HEALTHY / DEGRADED / FAILED / UNKNOWN) for the status bar
  * a graph snapshot (nodes, topics, edges, expected-but-missing parts) for React Flow

This is plain monitoring, not diagnosis: it says *what* looks unhealthy, never *why*.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque

from backend.ros_tools.client import RosClient
from backend.ros_tools.common import manifest

HEALTHY, DEGRADED, FAILED, UNKNOWN = "HEALTHY", "DEGRADED", "FAILED", "UNKNOWN"
GRAPH_TOPICS_HIDDEN = {"/rosout", "/parameter_events", "/diagnostics", "/tf", "/tf_static"}  # TF is drawn as its own nodes


class Monitor:
    def __init__(self, client: RosClient):
        self.client = client
        self._arrivals: dict[str, deque] = {}
        self._odom: deque = deque(maxlen=60)   # (t, x, y)
        self._lock = threading.Lock()
        self._subs_created = False

    def _ensure_subscriptions(self):
        if self._subs_created:
            return
        from rosidl_runtime_py.utilities import get_message
        for topic, meta in manifest()["topics"].items():
            self._arrivals[topic] = deque(maxlen=200)
            cls = get_message(meta["type"])
            if topic == "/odom":
                cb = self._on_odom
            else:
                cb = (lambda t: (lambda _m: self._arrivals[t].append(time.monotonic())))(topic)
            self.client.node.create_subscription(cls, topic, cb, 20)
        self._subs_created = True

    def _on_odom(self, m):
        now = time.monotonic()
        self._arrivals["/odom"].append(now)
        with self._lock:
            self._odom.append((now, m.pose.pose.position.x, m.pose.pose.position.y))

    def rate(self, topic: str, window: float = 2.0, max_age: float = 0.7) -> float:
        """Message rate in Hz from the messages actually received in the last `window` seconds (n-1 intervals over the span they
        cover), 0 if the newest one is older than `max_age`. Unlike count/window this recovers as soon as a restarted publisher
        is heard again, so the dashboard does not lag behind the independent verification."""
        now = time.monotonic()
        times = [t for t in list(self._arrivals.get(topic, ())) if now - t <= window]
        if len(times) < 3 or now - times[-1] > max_age:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    def odom_moved(self, window: float = 2.0) -> float | None:
        now = time.monotonic()
        with self._lock:
            pts = [p for p in self._odom if now - p[0] <= window]
        if len(pts) < 2:
            return None
        return math.hypot(pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2])

    # ------------------------------------------------------------------ health
    def health(self) -> dict:
        try:
            self.client.require()
            self._ensure_subscriptions()
            nodes = set(self.client.node_names())
        except Exception as e:  # noqa: BLE001
            comps = {k: {"state": UNKNOWN, "detail": "ROS unavailable"} for k in
                     ("controller", "lidar", "odometry", "tf", "navigation", "ros_graph")}
            return {"overall": UNKNOWN, "components": comps, "ros_available": False, "error": str(e)}

        m = manifest()
        c: dict[str, dict] = {}

        def st(state, detail):
            return {"state": state, "detail": detail}

        _, cmd_subs = self.client.endpoints("/cmd_vel")
        wheel = self.rate("/wheel_states")
        if "/base_controller" not in nodes:
            c["controller"] = st(FAILED, "base_controller not running")
        elif "/base_controller" not in {s["node"] for s in cmd_subs}:
            c["controller"] = st(DEGRADED, "running but not subscribed to /cmd_vel")
        elif wheel < 10:
            c["controller"] = st(DEGRADED, f"/wheel_states {wheel:.0f} Hz")
        else:
            c["controller"] = st(HEALTHY, f"/wheel_states {wheel:.0f} Hz")

        scan = self.rate("/scan")
        if "/lidar_driver" not in nodes:
            c["lidar"] = st(FAILED, "lidar_driver not running")
        elif scan < 1:
            c["lidar"] = st(FAILED, "/scan not publishing")
        elif scan < m["topics"]["/scan"]["min_rate_hz"]:
            c["lidar"] = st(DEGRADED, f"/scan {scan:.1f} Hz")
        else:
            c["lidar"] = st(HEALTHY, f"/scan {scan:.0f} Hz")

        odom = self.rate("/odom")
        moved = self.odom_moved()
        if odom < 1:
            c["odometry"] = st(FAILED, "/odom not publishing")
        elif moved is not None and moved < 0.02:
            c["odometry"] = st(DEGRADED, f"/odom {odom:.0f} Hz, robot not moving")
        else:
            c["odometry"] = st(HEALTHY, f"/odom {odom:.0f} Hz, moving")

        stale = []
        for e in m["tf"]:
            age = self._tf_age(e["parent"], e["child"])
            if age is None or age > 1.0:
                stale.append(f"{e['parent']}->{e['child']}")
        c["tf"] = st(FAILED, "stale: " + ", ".join(stale)) if stale else st(HEALTHY, "all transforms fresh")

        obst = self.rate("/obstacle_distance")
        if "/obstacle_monitor" not in nodes:
            c["navigation"] = st(FAILED, "obstacle_monitor not running")
        elif obst < m["topics"]["/obstacle_distance"]["min_rate_hz"]:
            c["navigation"] = st(DEGRADED, "obstacle detection not producing output")
        else:
            c["navigation"] = st(HEALTHY, f"obstacle detection {obst:.0f} Hz")

        missing = [n for n in m["nodes"] if n not in nodes]
        orphan = self._orphan_topics()
        if missing:
            c["ros_graph"] = st(DEGRADED, f"missing: {', '.join(missing)}")
        elif orphan:
            c["ros_graph"] = st(DEGRADED, f"unexpected topic(s): {', '.join(orphan)}")
        else:
            c["ros_graph"] = st(HEALTHY, f"{len(nodes)} nodes, all expected present")

        states = [v["state"] for v in c.values()]
        overall = FAILED if FAILED in states else DEGRADED if DEGRADED in states else HEALTHY
        return {"overall": overall, "components": c, "ros_available": True}

    def _tf_age(self, parent, child) -> float | None:
        from rclpy.time import Time
        try:
            t = self.client.tf_buffer.lookup_transform(parent, child, Time())
        except Exception:  # noqa: BLE001
            return None
        return (self.client.node.get_clock().now() - Time.from_msg(t.header.stamp)).nanoseconds / 1e9

    def _orphan_topics(self) -> list[str]:
        known = set(manifest()["topics"]) | GRAPH_TOPICS_HIDDEN | {"/tf"}
        out = []
        for t in self.client.topics():
            if t in known:
                continue
            pubs, subs = self.client.endpoints(t)
            if pubs or subs:
                out.append(t)
        return out

    # ------------------------------------------------------------------ graph
    def graph(self) -> dict:
        try:
            self.client.require()
            live_nodes = set(self.client.node_names())
            topics = self.client.topics()
        except Exception:  # noqa: BLE001
            return {"nodes": [], "topics": [], "edges": [], "ros_available": False}
        m = manifest()
        nodes = []
        for n in sorted(set(m["nodes"]) | live_nodes):
            nodes.append({"id": n, "label": n.lstrip("/"), "expected": n in m["nodes"], "alive": n in live_nodes,
                          "role": m["nodes"].get(n, {}).get("role")})
        topic_list, edges = [], []
        seen_edges = set()
        for t in sorted(set(topics) | set(m["topics"])):
            if t in GRAPH_TOPICS_HIDDEN:
                continue
            pubs, subs = self.client.endpoints(t) if t in topics else ([], [])
            if t not in m["topics"] and not pubs and not subs:
                continue
            exp = m["topics"].get(t)
            rate = round(self.rate(t), 1) if exp else None
            topic_list.append({"id": t, "label": t, "expected": exp is not None, "rate_hz": rate,
                               "min_rate_hz": exp["min_rate_hz"] if exp else None,
                               "publishers": len(pubs), "subscribers": len(subs)})
            for p in pubs:
                seen_edges.add((p["node"], t))
                edges.append({"source": p["node"], "target": t, "kind": "pub", "live": True})
            for s in subs:
                seen_edges.add((t, s["node"]))
                edges.append({"source": t, "target": s["node"], "kind": "sub", "live": True})
            if exp:  # expected-but-missing connections are drawn as broken edges
                for p in exp["publishers"]:
                    if (p, t) not in seen_edges:
                        edges.append({"source": p, "target": t, "kind": "pub", "live": False})
                for s in exp["subscribers"]:
                    if (t, s) not in seen_edges:
                        edges.append({"source": t, "target": s, "kind": "sub", "live": False})
        tf = []
        for e in m["tf"]:
            age = self._tf_age(e["parent"], e["child"])
            tf.append({"parent": e["parent"], "child": e["child"], "broadcaster": e["broadcaster"],
                       "age_s": None if age is None else round(age, 2), "fresh": age is not None and age < 1.0})
        return {"nodes": nodes, "topics": topic_list, "edges": edges, "tf": tf, "ros_available": True}
