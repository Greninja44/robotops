"""Unit tests for scripts/learn_manifest.py's pure logic (merge, diff). No ROS needed: these test the
functions that decide what to keep from an existing manifest vs. what to derive from a live observation,
using hand-built "learned" dicts that stand in for what learn() would have returned."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("learn_manifest", Path(__file__).resolve().parent.parent / "scripts" / "learn_manifest.py")
lm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lm)


def learned(nodes=None, topics=None, tf_edges=None, parameters=None, lifecycle=None):
    return {"nodes": nodes or {}, "topics": topics or {}, "tf_edges": tf_edges or [], "parameters": parameters or {},
           "lifecycle": lifecycle or {}}


def test_from_scratch_leaves_todo_placeholders_and_computes_rate_from_margin():
    manifest, warnings = lm.build_manifest(
        learned(nodes={"/base_controller": {"component": "base_controller"}},
               topics={"/cmd_vel": {"type": "geometry_msgs/msg/Twist", "publishers": ["/a"], "subscribers": ["/b"], "measured_hz": 10.0}}),
        existing=None, margin=0.5)
    assert manifest["robot"].startswith("TODO") and manifest["description"].startswith("TODO")
    assert manifest["nodes"]["/base_controller"]["role"].startswith("TODO")
    assert manifest["topics"]["/cmd_vel"]["min_rate_hz"] == 5.0          # 0.5 * 10.0, no prior threshold to keep
    assert warnings == []


def test_merge_keeps_prose_and_prior_thresholds_but_refreshes_structure():
    existing = {"robot": "r", "description": "d",
               "nodes": {"/base_controller": {"component": "base_controller", "role": "the real role"}},
               "topics": {"/cmd_vel": {"type": "geometry_msgs/msg/Twist", "publishers": ["/old"], "subscribers": [], "min_rate_hz": 3.0}},
               "tf": [], "parameters": {}}
    manifest, warnings = lm.build_manifest(
        learned(nodes={"/base_controller": {"component": "base_controller"}},
               topics={"/cmd_vel": {"type": "geometry_msgs/msg/Twist", "publishers": ["/velocity_commander"], "subscribers": ["/base_controller"], "measured_hz": 11.0}}),
        existing=existing, margin=0.5)
    assert manifest["robot"] == "r" and manifest["nodes"]["/base_controller"]["role"] == "the real role"
    assert manifest["topics"]["/cmd_vel"]["min_rate_hz"] == 3.0                       # kept, not recomputed from 11.0
    assert manifest["topics"]["/cmd_vel"]["publishers"] == ["/velocity_commander"]    # structure still refreshed
    assert warnings == []


def test_min_rate_above_measured_is_flagged():
    existing = {"topics": {"/scan": {"min_rate_hz": 50.0}}}
    _, warnings = lm.build_manifest(
        learned(topics={"/scan": {"type": "x", "publishers": [], "subscribers": [], "measured_hz": 10.0}}),
        existing=existing, margin=0.5)
    assert any("exceeds the just-measured" in w for w in warnings)


def test_single_candidate_tf_broadcaster_is_resolved_automatically():
    manifest, warnings = lm.build_manifest(
        learned(tf_edges=[{"parent": "odom", "child": "base_link", "measured_hz": 20.0, "candidates": ["/wheel_odometry"]}]),
        existing=None, margin=0.5)
    assert manifest["tf"] == [{"parent": "odom", "child": "base_link", "broadcaster": "/wheel_odometry"}]
    assert warnings == []


def test_ambiguous_tf_broadcaster_falls_back_to_a_matching_prior_silently():
    existing = {"tf": [{"parent": "odom", "child": "base_link", "broadcaster": "/wheel_odometry"}]}
    manifest, warnings = lm.build_manifest(
        learned(tf_edges=[{"parent": "odom", "child": "base_link", "measured_hz": 20.0,
                           "candidates": ["/wheel_odometry", "/tf_broadcaster"]}]),
        existing=existing, margin=0.5)
    assert manifest["tf"][0]["broadcaster"] == "/wheel_odometry"
    assert warnings == []


def test_ambiguous_tf_broadcaster_with_no_usable_prior_is_left_null_and_flagged():
    manifest, warnings = lm.build_manifest(
        learned(tf_edges=[{"parent": "odom", "child": "base_link", "measured_hz": 20.0,
                           "candidates": ["/wheel_odometry", "/tf_broadcaster"]}]),
        existing=None, margin=0.5)
    assert manifest["tf"][0]["broadcaster"] is None
    assert any("cannot attribute a broadcaster" in w for w in warnings)


def test_new_parameter_is_added_and_flagged_but_untracked_node_is_skipped():
    existing = {"parameters": {"/base_controller": {"cmd_vel_topic": "/cmd_vel"}}}
    manifest, warnings = lm.build_manifest(
        learned(parameters={"/base_controller": {"cmd_vel_topic": "/cmd_vel", "max_wheel_speed": 12.0},
                            "/lidar_driver": {"some_param": 1}}),
        existing=existing, margin=0.5)
    assert manifest["parameters"] == {"/base_controller": {"cmd_vel_topic": "/cmd_vel", "max_wheel_speed": 12.0}}
    assert any("max_wheel_speed" in w for w in warnings)
    assert "/lidar_driver" not in manifest["parameters"]          # never tracked before -> not pulled in silently


def test_diff_summary_reports_added_removed_and_changed_thresholds():
    old = {"nodes": {"/a": {}}, "topics": {"/t": {"min_rate_hz": 5.0}}, "parameters": {}, "tf": []}
    new = {"nodes": {"/a": {}, "/b": {}}, "topics": {"/t": {"min_rate_hz": 8.0}}, "parameters": {},
          "tf": [{"parent": "odom", "child": "base_link"}]}
    lines = lm.diff_summary(old, new)
    assert any("+ nodes" in l and "/b" in l for l in lines)
    assert any("~ /t.min_rate_hz" in l and "5.0" in l and "8.0" in l for l in lines)
    assert any("+ tf" in l for l in lines)


def test_diff_summary_is_empty_for_identical_manifests():
    m = {"nodes": {"/a": {}}, "topics": {}, "parameters": {}, "tf": []}
    assert lm.diff_summary(m, m) == []


# ---------------------------------------------------------------------------- resolve_tf_broadcasters_actively
# The real detection signal (found by direct observation against the live demo robot - see docs/STATUS.md):
# supervisor.restart() itself blocks for ~0.3-0.5s while the node is still alive, so a "before" snapshot taken
# before that call is stale by the time it returns and cannot be compared against. The robust signal is two
# *consecutive* post-restart samples reporting the identical timestamp for one edge, while an unrelated edge
# keeps advancing every ~50ms (20 Hz) - these tests exercise exactly that comparison, not real timing.

class FakeSupervisor:
    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    def restart(self, component):
        self.calls.append(component)
        if component in self.fail_for:
            raise ConnectionError("supervisor unreachable")
        return {"ok": True}


def frames_sequence(edges_over_time: dict[str, list[float | None]]):
    """edges_over_time: {child_frame: [ts_call1, ts_call2, ...]}. Returns a callable good for len(next value list)
    calls, each returning {child: {"most_recent_transform": ts}} for that call index."""
    calls = {"n": 0}

    def fake(client):
        i = calls["n"]
        calls["n"] += 1
        return {child: {"most_recent_transform": (ts[i] if i < len(ts) else ts[-1])} for child, ts in edges_over_time.items()}
    return fake


def test_no_op_when_every_edge_already_has_one_candidate(monkeypatch):
    sup = FakeSupervisor()
    monkeypatch.setattr(lm, "supervisor", sup)
    edges = [{"parent": "odom", "child": "base_link", "candidates": ["/wheel_odometry"]}]
    lm.resolve_tf_broadcasters_actively(object(), edges)
    assert sup.calls == [] and edges[0]["candidates"] == ["/wheel_odometry"]


def test_resolves_to_the_candidate_whose_restart_actually_froze_it(monkeypatch):
    sup = FakeSupervisor()
    monkeypatch.setattr(lm, "supervisor", sup)
    monkeypatch.setattr(lm.time, "sleep", lambda s: None)
    # Candidates are tried in sorted order: /tf_broadcaster first (6 samples, indices 0-5, always advancing -
    # its restart must NOT resolve the edge), then /wheel_odometry (indices 6-11, freezes at 200.0 twice -
    # its restart IS what should resolve the edge). The mock can't see which candidate is "really" being
    # restarted, so staging the freeze at the second candidate's sample window is what proves the attribution
    # tracks *which restart* produced the freeze, not just "some restart, somewhere."
    monkeypatch.setattr(lm, "_tf_frames", frames_sequence({
        "laser": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 200.0, 200.0, 200.0, 201.0, 202.0, 203.0],
    }))
    edges = [{"parent": "base_link", "child": "laser", "candidates": ["/tf_broadcaster", "/wheel_odometry"]}]
    lm.resolve_tf_broadcasters_actively(object(), edges)
    assert edges[0]["candidates"] == ["/wheel_odometry"]
    assert sup.calls == ["tf_broadcaster", "wheel_odometry"]    # resolved after the second candidate - no more tried


def test_a_restart_failure_is_skipped_not_raised(monkeypatch):
    sup = FakeSupervisor(fail_for=("tf_broadcaster",))
    monkeypatch.setattr(lm, "supervisor", sup)
    monkeypatch.setattr(lm.time, "sleep", lambda s: None)
    # always-advancing, so the surviving candidate's restart never falsely "resolves" it either - this test
    # isolates just the "a failed restart must not crash the function" behaviour.
    monkeypatch.setattr(lm, "_tf_frames", frames_sequence({"laser": [float(i) for i in range(50)]}))
    edges = [{"parent": "base_link", "child": "laser", "candidates": ["/tf_broadcaster", "/wheel_odometry"]}]
    lm.resolve_tf_broadcasters_actively(object(), edges)   # must not raise
    assert sup.calls == ["tf_broadcaster", "wheel_odometry"] * 3         # still tried every pass, gave up cleanly
    assert edges[0]["candidates"] == ["/tf_broadcaster", "/wheel_odometry"]   # left ambiguous, not guessed


def test_gives_up_after_max_passes_when_nothing_ever_freezes(monkeypatch):
    sup = FakeSupervisor()
    monkeypatch.setattr(lm, "supervisor", sup)
    monkeypatch.setattr(lm.time, "sleep", lambda s: None)
    # always-advancing timestamps -> no two consecutive samples ever match -> never resolved
    monkeypatch.setattr(lm, "_tf_frames", frames_sequence({"laser": [float(i) for i in range(50)]}))
    edges = [{"parent": "base_link", "child": "laser", "candidates": ["/a", "/b"]}]
    lm.resolve_tf_broadcasters_actively(object(), edges)
    assert edges[0]["candidates"] == ["/a", "/b"]
    assert sup.calls.count("a") == 3 and sup.calls.count("b") == 3       # tried every candidate, every pass


# ---------------------------------------------------------------------------- lifecycle learning
def test_a_freshly_observed_lifecycle_state_is_the_expectation_with_no_prior():
    manifest, warnings = lm.build_manifest(learned(lifecycle={"/safety_monitor": "active"}), existing=None, margin=0.5)
    assert manifest["lifecycle"] == {"/safety_monitor": "active"}
    assert warnings == []


def test_a_fresh_observation_overrides_a_stale_prior_unlike_a_rate_threshold():
    """Unlike min_rate_hz (kept from --merge, only refreshed with a fresh discovery if no prior exists), there
    is no reason to prefer a stale expected lifecycle state over what was just measured on a healthy robot -
    both represent the same fact, so the fresh observation always wins."""
    existing = {"lifecycle": {"/safety_monitor": "inactive"}}   # e.g. hand-edited wrong, or from an old manifest
    manifest, _ = lm.build_manifest(learned(lifecycle={"/safety_monitor": "active"}), existing=existing, margin=0.5)
    assert manifest["lifecycle"] == {"/safety_monitor": "active"}


def test_a_node_that_lost_its_lifecycle_interface_is_dropped_and_flagged():
    existing = {"lifecycle": {"/safety_monitor": "active"}}
    manifest, warnings = lm.build_manifest(learned(lifecycle={}), existing=existing, margin=0.5)
    assert manifest["lifecycle"] == {}
    assert any("no longer has a lifecycle interface" in w for w in warnings)


def test_plain_nodes_never_appear_in_the_lifecycle_section():
    manifest, _ = lm.build_manifest(learned(nodes={"/base_controller": {"component": "base_controller"}}, lifecycle={}),
                                    existing=None, margin=0.5)
    assert manifest["lifecycle"] == {}


def test_diff_summary_reports_a_changed_lifecycle_expectation():
    old = {"lifecycle": {"/safety_monitor": "inactive"}}
    new = {"lifecycle": {"/safety_monitor": "active"}}
    lines = lm.diff_summary(old, new)
    assert any("~ /safety_monitor.lifecycle" in l and "'inactive'" in l and "'active'" in l for l in lines)


def test_diff_summary_reports_added_and_removed_lifecycle_nodes():
    old = {"lifecycle": {}}
    new = {"lifecycle": {"/safety_monitor": "active"}}
    lines = lm.diff_summary(old, new)
    assert any("+ lifecycle" in l and "/safety_monitor" in l for l in lines)
    assert lm.diff_summary(new, old) and any("- lifecycle" in l for l in lm.diff_summary(new, old))
