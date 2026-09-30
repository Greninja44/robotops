"""Action policy: what RobotOps may do to the robot, and under which conditions.

* Read-only diagnostic tools run automatically (see ros_tools/registry.py).
* State-changing actions exist only in ACTIONS below, only for allowlisted demo
  components, always require human approval, and are refused unless the
  diagnosis cites anomaly evidence about the target (NO EVIDENCE -> NO REPAIR).
"""
from __future__ import annotations

from backend.ros_tools.common import component_subjects, manifest

# action -> description. Nothing else can be executed.
ACTIONS = {
    "restart_component": "Stop the component's process and start it again with its canonical configuration "
                         "(fixes crashed, stalled or hung components)",
    "set_parameter": "Set a single allowlisted parameter on a running component back to its canonical value, "
                     "without a restart (fixes a component that is running but misconfigured)",
}

# Only components of the demo robot are repairable, with an explicit risk rating.
REPAIRABLE = {
    "base_controller":    {"risk": "medium", "note": "robot will resume motion as soon as the controller is back"},
    "lidar_driver":       {"risk": "low",    "note": "no /scan for ~2 s during restart"},
    "tf_broadcaster":     {"risk": "low",    "note": "sensor transforms briefly unavailable"},
    "obstacle_monitor":   {"risk": "medium", "note": "obstacle detection offline during restart"},
    "wheel_odometry":     {"risk": "medium", "note": "odometry pose resets to origin"},
    "velocity_commander": {"risk": "medium", "note": "motion commands resume immediately"},
    "safety_monitor":     {"risk": "low",    "note": "restart re-runs the normal configure->activate bring-up"},
}

EXPECTED_RESULT = {
    "base_controller": "base_controller back in the graph, subscribed to /cmd_vel, /wheel_states flowing, odometry changing",
    "lidar_driver": "/scan publishing at >= 5 Hz, lidar diagnostics OK, obstacle detection restored",
    "tf_broadcaster": "base_link->laser transform fresh, obstacle detection restored",
    "obstacle_monitor": "obstacle_monitor running, /obstacle_distance publishing",
    "wheel_odometry": "/odom publishing and changing, odom->base_link transform fresh",
    "velocity_commander": "/cmd_vel publishing at >= 5 Hz",
    "safety_monitor": "safety_monitor lifecycle state active, /safety_status publishing",
}

# set_parameter allowlist: component -> the ONE parameter it may be set to and its canonical value.
# Deliberately narrow (no arbitrary parameter, no arbitrary value) - this is a second guarded repair
# primitive, not a general parameter-setting capability.
PARAMETER_FIX = {
    "base_controller": {"param": "cmd_vel_topic", "value": "/cmd_vel", "risk": "low",
                        "note": "live parameter update, no process restart; velocity commands keep flowing"},
}

PARAM_EXPECTED_RESULT = {
    "base_controller": "base_controller subscribed to /cmd_vel again (parameter corrected live, no restart), "
                       "/wheel_states flowing",
}

MIN_EVIDENCE_FOR_REPAIR = 2


class PolicyViolation(Exception):
    pass


def check_action(action: str, target: str) -> dict:
    """Raise PolicyViolation unless (action, target) is allowlisted. Returns the policy entry."""
    if action not in ACTIONS:
        raise PolicyViolation(f"action {action!r} is not allowlisted (allowed: {', '.join(ACTIONS)})")
    if action == "set_parameter":
        if target not in PARAMETER_FIX:
            raise PolicyViolation(f"target {target!r} has no allowlisted parameter fix "
                                  f"(only {', '.join(PARAMETER_FIX)}; use restart_component instead)")
        return {"action": action, "target": target, "risk": PARAMETER_FIX[target]["risk"], "requires_approval": True}
    if target not in REPAIRABLE:
        raise PolicyViolation(f"target {target!r} is not an allowlisted demo component")
    return {"action": action, "target": target, "risk": REPAIRABLE[target]["risk"], "requires_approval": True}


def expected_result(action: str, target: str) -> str:
    return (PARAM_EXPECTED_RESULT if action == "set_parameter" else EXPECTED_RESULT).get(target, "")


def evidence_supports_target(evidence: list, target: str) -> list:
    """Cited anomaly evidence that concerns the target (its node, topics or TF edges)."""
    subj = component_subjects(target)
    return [e for e in evidence if e.anomaly and subj.intersection(e.subjects)]


def repair_gate(evidence: list, target: str) -> None:
    """NO EVIDENCE -> NO REPAIR."""
    if len(evidence) < MIN_EVIDENCE_FOR_REPAIR:
        raise PolicyViolation(f"at least {MIN_EVIDENCE_FOR_REPAIR} pieces of cited evidence are required "
                              f"before a repair can be proposed (got {len(evidence)})")
    if not evidence_supports_target(evidence, target):
        raise PolicyViolation(f"none of the cited evidence shows an anomaly in {target} "
                              f"(its node, topics or transforms: {sorted(component_subjects(target))})")


def components() -> list[str]:
    return [m["component"] for m in manifest()["nodes"].values()]
