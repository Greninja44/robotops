"""/diagnostics tool (read-only)."""
from __future__ import annotations

import time

from .common import manifest, tool

STALE_AFTER_S = 3.0
LEVEL = {0: "OK", 1: "WARN", 2: "ERROR", 3: "STALE"}


@tool("get_recent_diagnostics")
def get_recent_diagnostics(client, r):
    now = time.monotonic()
    out = {}
    for name, d in sorted(client.diagnostics.items()):
        age = now - d.received
        level = "STALE" if age > STALE_AFTER_S else LEVEL.get(d.level, "UNKNOWN")
        node = "/" + name if not name.startswith("/") else name
        out[name] = {"level": level, "message": d.message, "age_s": round(age, 1), "values": d.values}
        subj = [node] if node in manifest()["nodes"] else []
        if level == "STALE":
            r.anomaly(f"Diagnostics from {name} are STALE: no update for {age:.0f}s "
                      f"(last message: '{d.message}')", *subj)
        elif level in ("WARN", "ERROR"):
            r.anomaly(f"{name} reports {level}: {d.message}", *subj)
    ok = [n for n, v in out.items() if v["level"] == "OK"]
    if ok:
        r.normal(f"Diagnostics OK for: {', '.join(ok)}", *["/" + n for n in ok])
    if not out:
        r.anomaly("No /diagnostics messages have been received")
    r.data["statuses"] = out
