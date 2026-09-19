"""TF diagnostic tool (read-only)."""
from __future__ import annotations

from .common import InvalidArgument, frame_name, manifest, tool

STALE_AFTER_S = 1.0


@tool("check_tf")
def check_tf(client, r, parent_frame: str | None = None, child_frame: str | None = None):
    """Check one transform, or every transform the robot manifest expects when no frames are given."""
    if parent_frame and child_frame:
        pairs = [(frame_name(parent_frame), frame_name(child_frame))]
    elif parent_frame or child_frame:
        raise InvalidArgument("give both parent_frame and child_frame, or neither")
    else:
        pairs = [(e["parent"], e["child"]) for e in manifest()["tf"]]
    broadcaster = {(e["parent"], e["child"]): e["broadcaster"] for e in manifest()["tf"]}
    r.data["frames_known"] = client.tf_frames()
    results = {}
    for parent, child in pairs:
        key = f"tf:{parent}->{child}"
        res = client.lookup_tf(parent, child)
        results[f"{parent}->{child}"] = res
        owner = [broadcaster[(parent, child)]] if (parent, child) in broadcaster else []
        if not res["available"]:
            r.anomaly(f"Transform {parent}->{child} is NOT available: {res['error']}", key, *owner)
        elif res["age_s"] > STALE_AFTER_S:
            r.anomaly(f"Transform {parent}->{child} is STALE: last update {res['age_s']:.1f}s ago "
                      f"(broadcaster stopped publishing)", key, *owner)
        else:
            r.normal(f"Transform {parent}->{child} is fresh ({res['age_s'] * 1000:.0f} ms old)", key, *owner)
    r.data["transforms"] = results
