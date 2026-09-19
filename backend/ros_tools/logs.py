"""/rosout log tool (read-only)."""
from __future__ import annotations

import time

from .common import clamp, manifest, ros_name, tool

LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "FATAL"}
# Logs older than the last demo reset describe a previous incident, not this one.
state = {"since": 0.0}


@tool("get_recent_logs")
def get_recent_logs(client, r, node: str | None = None, seconds: float = 120.0):
    seconds = clamp(seconds, 5.0, 600.0, 120.0)
    node_filter = ros_name(node, "node").lstrip("/") if node else None
    r.args.update(node=node_filter, seconds=seconds)
    cutoff = max(time.time() - seconds, state["since"])
    entries = [e for e in list(client.rosout)
               if e["received"] >= cutoff and e["level"] >= 30
               and (node_filter is None or e["node"] == node_filter)]
    r.data["entries"] = [{"level": LEVELS.get(e["level"], e["level"]), "node": e["node"], "msg": e["msg"],
                          "age_s": round(time.time() - e["received"], 1)} for e in entries[-30:]]
    for e in r.data["entries"][-8:]:
        n = "/" + e["node"]
        subj = [n] if n in manifest()["nodes"] else []
        if e["level"] in ("ERROR", "FATAL"):
            r.anomaly(f"{e['level']} log from {n} ({e['age_s']:.0f}s ago): {e['msg']}", *subj)
    if not entries:
        r.normal(f"No WARN/ERROR/FATAL logs in the last {seconds:.0f}s" + (f" from /{node_filter}" if node_filter else ""))
