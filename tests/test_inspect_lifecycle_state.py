"""inspect_lifecycle_state: precise evidence for a managed (lifecycle) node's actual state, distinct from
mere presence (list_nodes) or diagnostics text. A plain node correctly reports "not lifecycle-managed" - it
is a normal answer, never a failure, since most nodes in this robot have no lifecycle interface at all."""
from backend.ros_tools import nodes


class FakeClient:
    def __init__(self, present=True, state=None):
        self._present = present
        self._state = state

    def node_names(self):
        return ["/safety_monitor", "/base_controller", "/velocity_commander"] if self._present else []

    def get_lifecycle_state(self, node, timeout=1.5):
        return self._state


def test_node_not_running_is_an_anomaly():
    r = nodes.inspect_lifecycle_state(FakeClient(present=False), node="/safety_monitor")
    assert not r.data["exists"]
    assert any("not running" in f.text for f in r.findings if f.anomaly)


def test_plain_node_with_no_lifecycle_interface_is_normal_not_an_error():
    r = nodes.inspect_lifecycle_state(FakeClient(state=None), node="/base_controller")
    assert r.data["exists"] and r.data["lifecycle_managed"] is False
    assert not any(f.anomaly for f in r.findings)
    assert any("no lifecycle interface" in f.text for f in r.findings)


def test_state_matching_the_manifest_is_healthy():
    r = nodes.inspect_lifecycle_state(FakeClient(state="active"), node="/safety_monitor")
    assert r.data["lifecycle_managed"] is True and r.data["state"] == "active"
    assert r.data["expected_state"] == "active"          # from demo_robot/manifest.json's "lifecycle" section
    assert not any(f.anomaly for f in r.findings)


def test_state_not_matching_the_manifest_is_an_anomaly():
    r = nodes.inspect_lifecycle_state(FakeClient(state="inactive"), node="/safety_monitor")
    assert r.data["state"] == "inactive" and r.data["expected_state"] == "active"
    assert any("inactive" in f.text and "active" in f.text for f in r.findings if f.anomaly)


def test_a_lifecycle_node_with_no_manifest_expectation_is_reported_without_judgement():
    """If a lifecycle-managed node isn't (yet) listed in the manifest's "lifecycle" section, its state is
    still reported, but nothing is called an anomaly - there is no expectation to compare against."""
    r = nodes.inspect_lifecycle_state(FakeClient(state="unconfigured"), node="/velocity_commander")
    assert r.data["lifecycle_managed"] is True and r.data["expected_state"] is None
    assert not any(f.anomaly for f in r.findings)
