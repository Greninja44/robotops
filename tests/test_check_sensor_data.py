"""check_sensor_data: the one tool that inspects message *content*, not just presence/rate. Presence
(list_topics) and rate (measure_topic_rate) both look healthy for a stuck/saturated sensor that keeps
publishing at its normal rate with wrong data - only sampling the actual values catches it."""
from backend.ros_tools import topics


class FakeMsg:
    def __init__(self, ranges):
        self.ranges = ranges


class FakeClient:
    def __init__(self, exists=True, messages=()):
        self._exists = exists
        self._messages = list(messages)

    def sample_topic(self, topic, duration, keep=False):
        if not self._exists:
            return {"exists": False, "count": 0, "times": [], "messages": []}
        return {"exists": True, "count": len(self._messages), "times": list(range(len(self._messages))), "messages": self._messages}


def test_topic_missing_is_an_anomaly():
    r = topics.check_sensor_data(FakeClient(exists=False), topic="/scan")
    assert not any(f.text for f in r.findings if not f.anomaly)   # nothing "normal" was said
    assert any("does not exist" in f.text for f in r.findings if f.anomaly)


def test_no_messages_sampled_is_an_anomaly():
    r = topics.check_sensor_data(FakeClient(messages=[]), topic="/scan")
    assert any("0 messages sampled" in f.text for f in r.findings if f.anomaly)


def test_varying_ranges_are_healthy():
    msg = FakeMsg([1.0, 2.5, 0.8, 3.2, 1.9, 4.0])
    r = topics.check_sensor_data(FakeClient(messages=[msg]), topic="/scan")
    assert r.data["distinct_values"] == 6 and r.data["content_checkable"] is True
    assert not any(f.anomaly for f in r.findings)


def test_frozen_ranges_are_an_anomaly():
    msg = FakeMsg([0.12] * 360)
    r = topics.check_sensor_data(FakeClient(messages=[msg]), topic="/scan")
    assert r.data["distinct_values"] == 1 and r.data["min_range"] == r.data["max_range"] == 0.12
    assert any("frozen" in f.text or "saturated" in f.text for f in r.findings if f.anomaly)


def test_two_distinct_values_still_counts_as_frozen():
    """A stuck sensor can alternate between two fixed values (quantization) - not just a single constant."""
    msg = FakeMsg([1.0, 2.0] * 180)
    r = topics.check_sensor_data(FakeClient(messages=[msg]), topic="/scan")
    assert r.data["distinct_values"] == 2
    assert any(f.anomaly for f in r.findings)


def test_nan_and_inf_are_dropped_before_judging_variety():
    msg = FakeMsg([float("nan"), float("inf"), float("-inf"), 1.0, 2.0, 3.0, 4.0, 5.0])
    r = topics.check_sensor_data(FakeClient(messages=[msg]), topic="/scan")
    assert r.data["valid_beams"] == 5 and r.data["distinct_values"] == 5
    assert not any(f.anomaly for f in r.findings)


def test_all_invalid_readings_is_an_anomaly():
    msg = FakeMsg([float("nan"), float("inf"), float("-inf")])
    r = topics.check_sensor_data(FakeClient(messages=[msg]), topic="/scan")
    assert r.data["valid_beams"] == 0
    assert any("none are valid" in f.text for f in r.findings if f.anomaly)


def test_message_type_without_ranges_is_reported_not_guessed():
    """A message type this tool doesn't understand yet must never be silently called healthy."""
    class Odom:
        pass
    r = topics.check_sensor_data(FakeClient(messages=[Odom()]), topic="/odom")
    assert r.data["content_checkable"] is False
    assert not any(f.anomaly for f in r.findings)   # not a lie that it's healthy, but not a false alarm either
    assert any("not implemented" in f.text for f in r.findings)


def test_only_looks_at_the_most_recent_message():
    old, new = FakeMsg([0.12] * 360), FakeMsg([1.0, 2.0, 3.0, 4.0, 5.0])
    r = topics.check_sensor_data(FakeClient(messages=[old, new]), topic="/scan")
    assert r.data["distinct_values"] == 5 and not any(f.anomaly for f in r.findings)
