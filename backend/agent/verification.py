"""Independent post-repair verification.

A repair is never considered successful because a command returned OK. After a
repair we re-measure the live ROS system against the robot manifest: the
repaired component's own interfaces, and the whole robot (all nodes, all topic
rates, all transforms, odometry actually changing, diagnostics OK).
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

from backend.ros_tools.common import manifest, node_of

SAMPLE_S = 2.0


def _check(name: str, passed: bool, detail: str, scope: str) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail, "scope": scope}


def run_checks(client, target: str | None) -> list[dict]:
    m = manifest()
    checks: list[dict] = []
    target_node = node_of(target) if target else None
    nodes = client.node_names()

    def scope_of(*subjects):
        return "target" if target_node and target_node in subjects else "system"

    # nodes
    for n in m["nodes"]:
        checks.append(_check(f"{n} running", n in nodes, "present in ROS graph" if n in nodes else "missing", scope_of(n)))

    # topic rates + odometry motion, sampled in parallel
    topics = list(m["topics"])
    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        samples = dict(zip(topics, pool.map(lambda t: client.sample_topic(t, SAMPLE_S, keep=(t == "/odom")), topics)))
    for t, s in samples.items():
        exp = m["topics"][t]
        rate = len(s["times"]) / SAMPLE_S
        checks.append(_check(f"{t} publishing", rate >= exp["min_rate_hz"],
                             f"{rate:.1f} Hz (min {exp['min_rate_hz']})", scope_of(*exp["publishers"])))
        if exp["subscribers"]:
            _, subs = client.endpoints(t)
            sub_nodes = {x["node"] for x in subs}
            for n in exp["subscribers"]:
                checks.append(_check(f"{n} subscribed to {t}", n in sub_nodes,
                                     "subscribed" if n in sub_nodes else f"subscribers: {sorted(sub_nodes) or 'none'}",
                                     scope_of(n)))
    odom = samples["/odom"]["messages"]
    if len(odom) == 2:
        p0, p1 = odom[0].pose.pose.position, odom[1].pose.pose.position
        moved = math.hypot(p1.x - p0.x, p1.y - p0.y)
        checks.append(_check("odometry changing (robot moving)", moved > 0.05,
                             f"moved {moved:.2f} m in {SAMPLE_S:.0f} s", scope_of("/base_controller", "/wheel_odometry")))
    else:
        checks.append(_check("odometry changing (robot moving)", False, "no /odom messages", "system"))

    # transforms
    for e in m["tf"]:
        r = client.lookup_tf(e["parent"], e["child"])
        ok = r["available"] and r["age_s"] < 1.0
        detail = f"{r['age_s'] * 1000:.0f} ms old" if r["available"] else r["error"]
        checks.append(_check(f"TF {e['parent']}->{e['child']} fresh", ok, detail, scope_of(e["broadcaster"])))

    # diagnostics
    now = time.monotonic()
    for n in m["nodes"]:
        d = client.diagnostics.get(n.lstrip("/"))
        ok = d is not None and d.level == 0 and now - d.received < 3.0
        detail = "no diagnostics" if d is None else (
            "stale" if now - d.received >= 3.0 else f"{['OK', 'WARN', 'ERROR'][min(d.level, 2)]}: {d.message}")
        checks.append(_check(f"{n} diagnostics OK", ok, detail, scope_of(n)))
    return checks


def verify(client, target: str | None, attempts: int = 4, settle_s: float = 3.0, on_attempt=None) -> dict:
    """Retry a few times: a restarted node needs a moment to join the graph and publish."""
    checks: list[dict] = []
    for i in range(1, attempts + 1):
        time.sleep(settle_s)
        checks = run_checks(client, target)
        failed = [c for c in checks if not c["passed"]]
        if on_attempt:
            on_attempt(i, checks)
        if not failed:
            return {"verified": True, "attempts": i, "checks": checks, "failed": []}
    return {"verified": False, "attempts": attempts, "checks": checks,
            "failed": [c for c in checks if not c["passed"]]}
