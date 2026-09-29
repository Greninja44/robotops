"""Unit tests for scripts/learn_manifest.py's pure logic (merge, diff). No ROS needed: these test the
functions that decide what to keep from an existing manifest vs. what to derive from a live observation,
using hand-built "learned" dicts that stand in for what learn() would have returned."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("learn_manifest", Path(__file__).resolve().parent.parent / "scripts" / "learn_manifest.py")
lm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lm)


def learned(nodes=None, topics=None, tf_edges=None, parameters=None):
    return {"nodes": nodes or {}, "topics": topics or {}, "tf_edges": tf_edges or [], "parameters": parameters or {}}


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
