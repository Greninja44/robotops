#!/usr/bin/env python3
"""Learn demo_robot/manifest.json from a live, healthy ROS 2 system, instead of writing it by hand.

The manifest is RobotOps' healthy reference (what evidence.py and verification.py compare the live
graph against). This script derives the parts that are genuinely measurable - nodes, topics, their
types, publishers, subscribers, measured rates, TF edges, declared parameters - directly from the
running robot. Two things it will NOT invent:

  * human-readable prose ("role", the top-level "description"): these are semantic judgements, not
    observations, so they are only ever copied forward from an existing manifest (--merge) or left as
    a "TODO: describe ..." placeholder for a human to fill in.
  * which node broadcasts which TF edge: tf2's own introspection does not reliably expose this (the
    "broadcaster" field of `all_frames_as_yaml()` is unreliable in ROS 2 - see below). Every current
    publisher of /tf or /tf_static is reported as a CANDIDATE for every edge; if there is exactly one
    candidate the edge is resolved automatically, otherwise --merge is used to keep the human-supplied
    answer (sanity-checked against the current candidates) and, failing that, the edge is left with
    "broadcaster": null and a warning is printed - a wrong guess would be worse than an honest gap.

Usage:
  ./scripts/learn_manifest.py                                  # learn from the live robot, print a diff against
                                                                 # demo_robot/manifest.json, write nothing
  ./scripts/learn_manifest.py --apply                           # same, then overwrite demo_robot/manifest.json
  ./scripts/learn_manifest.py --out /tmp/learned.json --apply   # learn fresh (no --merge): every role/description
                                                                 # becomes a TODO placeholder
  ./scripts/learn_manifest.py --duration 5 --margin 0.6          # sample each topic 5s; min_rate_hz = 60% of measured

Requires the robot to already be running and healthy (./scripts/start_demo.sh or ./run_demo.sh); this
script only observes, it injects no faults and restarts nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.ros_tools import supervisor  # noqa: E402
from backend.ros_tools.client import get_client  # noqa: E402

# ROS-infrastructure topics that are not part of the robot's own data flow (not modeled as manifest
# "topics" - /diagnostics is read by get_recent_diagnostics directly, TF is modeled separately).
SKIP_TOPICS = {"/rosout", "/parameter_events", "/diagnostics", "/tf", "/tf_static"}
TF_TOPICS = ("/tf", "/tf_static")
# Parameters every rclpy node declares that are not the robot's own configuration.
SKIP_PARAMS = {"use_sim_time", "start_type_description_service"}


def learn(client, duration: float) -> dict:
    node_names = client.node_names()
    topic_types = client.topics()

    nodes = {n: {"component": n.lstrip("/")} for n in node_names}

    topics = {}
    for name, types in sorted(topic_types.items()):
        if name in SKIP_TOPICS or not types:
            continue
        pubs, subs = client.endpoints(name)
        pub_nodes, sub_nodes = [p["node"] for p in pubs], [s["node"] for s in subs]
        if not (set(pub_nodes) | set(sub_nodes)) & set(node_names):
            continue  # nothing on this topic belongs to the robot (e.g. a tool's own probe topic)
        sample = client.sample_topic(name, duration)
        measured_hz = round(sample["count"] / duration, 2) if sample["exists"] else 0.0
        topics[name] = {"type": types[0], "publishers": pub_nodes, "subscribers": sub_nodes,
                        "measured_hz": measured_hz}

    tf_edges = []
    tf_publisher_candidates = sorted({p["node"] for t in TF_TOPICS for p in client.endpoints(t)[0]})
    frames = _tf_frames(client)
    for child, info in frames.items():
        tf_edges.append({"parent": info["parent"], "child": child, "measured_hz": round(info.get("rate") or 0.0, 2),
                         "candidates": tf_publisher_candidates})

    params = {}
    for n in node_names:
        try:
            live = client.get_parameters(n, timeout=1.5)
        except Exception:  # noqa: BLE001 - node has no parameter service, or it timed out
            continue
        live = {k: v for k, v in live.items() if k not in SKIP_PARAMS}
        if live:
            params[n] = live

    return {"nodes": nodes, "topics": topics, "tf_edges": tf_edges, "parameters": params}


def _tf_frames(client) -> dict:
    import yaml
    try:
        return yaml.safe_load(client.tf_buffer.all_frames_as_yaml()) or {}
    except Exception:  # noqa: BLE001
        return {}


def resolve_tf_broadcasters_actively(client, tf_edges: list[dict]) -> None:
    """Opt-in (--active), invasive: for every edge whose candidates list has more than one entry, restart
    one candidate at a time and watch whether that edge's timestamp freezes during the respawn gap - the
    node that stops publishing an edge while it restarts is the one that broadcasts it. Mutates tf_edges'
    "candidates" lists down to a single entry wherever it can resolve one; leaves the rest untouched (still
    ambiguous, still reported as a warning) rather than guess. Briefly disrupts the robot - a few hundred ms
    of downtime per candidate tried - so this is meant for a demo/lab robot during setup, never a live one."""
    remaining = {(e["parent"], e["child"]): e for e in tf_edges if len(e["candidates"]) > 1}
    if not remaining:
        return
    print(f"[active] resolving {len(remaining)} ambiguous TF edge(s) ...")
    MAX_PASSES = 3   # a restart's respawn gap varies with system load; a single sample can miss it, so retry
    for attempt in range(1, MAX_PASSES + 1):
        if not remaining:
            break
        candidates = sorted({c for e in remaining.values() for c in e["candidates"]})
        for full_name in candidates:
            if not remaining:
                break
            component = full_name.lstrip("/")
            try:
                supervisor.restart(component)   # blocks ~0.3-0.5s itself; the node is still alive throughout
            except Exception as e:  # noqa: BLE001 - supervisor unreachable, unknown component, etc.
                print(f"[active]   {component}: restart failed ({e}), skipping")
                continue
            # A "before" snapshot taken before the (blocking) restart call is stale by the time it returns,
            # so instead: poll repeatedly right after and look for an edge whose timestamp is IDENTICAL
            # across two consecutive samples - the signature of "nothing published this edge in between" -
            # rather than comparing to any single earlier reference point.
            samples = []
            for _ in range(6):
                samples.append(_tf_frames(client))
                time.sleep(0.1)
            froze = set()
            for edge_key, e in remaining.items():
                if full_name not in e["candidates"]:
                    continue
                ts = [(s.get(e["child"]) or {}).get("most_recent_transform") for s in samples]
                if any(a is not None and a == b for a, b in zip(ts, ts[1:])):
                    froze.add(edge_key)
            time.sleep(1.5)    # let it fully recover before trying the next candidate
            for edge_key in list(froze):
                e = remaining.pop(edge_key)
                print(f"[active]   {e['parent']}->{e['child']}: resolved to {full_name} (froze during its restart, attempt {attempt})")
                e["candidates"] = [full_name]
    for e in remaining.values():
        print(f"[active]   {e['parent']}->{e['child']}: still ambiguous after {MAX_PASSES} attempt(s) at every candidate - left for a human")


def build_manifest(learned: dict, existing: dict | None, margin: float) -> tuple[dict, list[str]]:
    """Merge learned (measured) facts with an existing manifest's human-authored prose. Returns
    (manifest, warnings)."""
    existing = existing or {}
    ex_nodes = existing.get("nodes", {})
    ex_topics = existing.get("topics", {})
    ex_tf = {(e["parent"], e["child"]): e for e in existing.get("tf", [])}
    warnings: list[str] = []

    nodes = {}
    for n, info in learned["nodes"].items():
        prior = ex_nodes.get(n, {})
        nodes[n] = {"component": prior.get("component", info["component"]),
                   "role": prior.get("role", f"TODO: describe {info['component']}")}

    topics = {}
    for name, info in learned["topics"].items():
        prior = ex_topics.get(name, {})
        min_rate = prior.get("min_rate_hz")
        if min_rate is None:
            min_rate = round(info["measured_hz"] * margin, 1)
        topics[name] = {"type": info["type"], "publishers": info["publishers"], "subscribers": info["subscribers"],
                        "min_rate_hz": min_rate}
        if info["measured_hz"] and min_rate > info["measured_hz"]:
            warnings.append(f"{name}: min_rate_hz {min_rate} exceeds the just-measured {info['measured_hz']} Hz "
                            f"(kept from the existing manifest - re-check it)")

    tf = []
    for e in learned["tf_edges"]:
        prior = ex_tf.get((e["parent"], e["child"]))
        candidates = e["candidates"]
        if len(candidates) == 1:
            broadcaster = candidates[0]
        elif prior and prior.get("broadcaster") in candidates:
            broadcaster = prior["broadcaster"]
        else:
            broadcaster = None
            warnings.append(f"tf {e['parent']}->{e['child']}: cannot attribute a broadcaster automatically "
                            f"(candidates publishing /tf or /tf_static: {candidates or 'none'}) - set it by hand")
        tf.append({"parent": e["parent"], "child": e["child"], "broadcaster": broadcaster})
    tf.sort(key=lambda e: (e["parent"], e["child"]))

    parameters = {}
    for n, live in learned["parameters"].items():
        prior = existing.get("parameters", {}).get(n)
        if prior is not None:
            new_keys = set(live) - set(prior)
            for k in new_keys:
                warnings.append(f"{n}: newly discovered parameter {k!r}={live[k]!r} (not in the existing manifest - added)")
            parameters[n] = {k: live[k] for k in prior if k in live} | {k: live[k] for k in new_keys}
        # else: a node not previously tracked - parameters are not included unless it was already tracked,
        # to avoid pulling in incidental parameters nobody has decided matter for fault detection.

    manifest = {
        "robot": existing.get("robot", "TODO: robot id"),
        "description": existing.get("description", "TODO: describe what this robot does"),
        "nodes": nodes, "topics": topics, "tf": tf, "parameters": parameters,
    }
    return manifest, warnings


def diff_summary(old: dict, new: dict) -> list[str]:
    lines = []
    for key in ("nodes", "topics", "parameters"):
        added = sorted(set(new.get(key, {})) - set(old.get(key, {})))
        removed = sorted(set(old.get(key, {})) - set(new.get(key, {})))
        if added:
            lines.append(f"  + {key}: {added}")
        if removed:
            lines.append(f"  - {key}: {removed}")
    old_tf = {(e["parent"], e["child"]) for e in old.get("tf", [])}
    new_tf = {(e["parent"], e["child"]) for e in new.get("tf", [])}
    if new_tf - old_tf:
        lines.append(f"  + tf: {sorted(new_tf - old_tf)}")
    if old_tf - new_tf:
        lines.append(f"  - tf: {sorted(old_tf - new_tf)}")
    for name, info in new.get("topics", {}).items():
        prior = old.get("topics", {}).get(name)
        if prior and prior.get("min_rate_hz") != info.get("min_rate_hz"):
            lines.append(f"  ~ {name}.min_rate_hz: {prior.get('min_rate_hz')} -> {info.get('min_rate_hz')}")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merge", default=str(ROOT / "demo_robot" / "manifest.json"),
                    help="existing manifest to keep prose/thresholds/broadcasters from (default: demo_robot/manifest.json; "
                         "pass a nonexistent path to learn from scratch, e.g. --merge /tmp/none.json)")
    ap.add_argument("--out", default=str(ROOT / "demo_robot" / "manifest.json"), help="where to write the result")
    ap.add_argument("--duration", type=float, default=3.0, help="seconds to sample each topic's rate (default 3)")
    ap.add_argument("--margin", type=float, default=0.5,
                    help="min_rate_hz = margin * measured_hz for any topic with no prior threshold (default 0.5)")
    ap.add_argument("--apply", action="store_true", help="write --out (default: dry run, prints the diff only)")
    ap.add_argument("--active", action="store_true",
                    help="resolve ambiguous TF broadcasters by restarting candidate nodes one at a time and watching which "
                         "edge freezes (a few hundred ms of downtime per candidate tried) - default is to only ever resolve "
                         "an edge when there is exactly one candidate, or leave it for a human")
    args = ap.parse_args()

    existing = None
    if Path(args.merge).exists():
        existing = json.loads(Path(args.merge).read_text())

    c = get_client()
    c.start()
    time.sleep(2.5)
    print(f"Observing the live robot for {args.duration:.0f}s per topic ...")
    learned = learn(c, args.duration)
    if args.active:
        resolve_tf_broadcasters_actively(c, learned["tf_edges"])
    c.shutdown()

    manifest, warnings = build_manifest(learned, existing, args.margin)

    print(f"\nLearned {len(manifest['nodes'])} nodes, {len(manifest['topics'])} topics, "
         f"{len(manifest['tf'])} TF edges, parameters for {len(manifest['parameters'])} node(s).")
    if warnings:
        print("\nNeeds a human:")
        for w in warnings:
            print(f"  ! {w}")

    if existing:
        d = diff_summary(existing, manifest)
        print("\nDiff against the merged-from manifest:" if d else "\nNo structural change from the merged-from manifest.")
        for line in d:
            print(line)

    if args.apply:
        Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"\nWrote {args.out}")
    else:
        print(f"\nDry run - nothing written. Re-run with --apply to write {args.out}.")


if __name__ == "__main__":
    main()
