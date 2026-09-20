"""Dashboard rate estimator: recovers as soon as a restarted publisher is heard (no lag behind the verifier)."""
import time
from collections import deque

from backend.monitor import Monitor


def monitor_with(times_ago: list[float]) -> Monitor:
    m = Monitor(client=None)
    now = time.monotonic()
    m._arrivals["/t"] = deque(now - a for a in sorted(times_ago, reverse=True))
    return m


def test_steady_20hz_reads_20hz():
    assert abs(monitor_with([0.05 * i for i in range(40)]).rate("/t") - 20.0) < 1.0


def test_a_just_restarted_publisher_reads_its_true_rate_not_a_fraction_of_the_window():
    # 12 messages at 20 Hz received in the last 0.55 s, nothing before that: the old count/window estimate said 6 Hz
    m = monitor_with([0.05 * i for i in range(12)])
    assert 18 < m.rate("/t") < 22


def test_silent_topic_reads_zero():
    assert monitor_with([1.5, 1.6, 1.7, 1.8]).rate("/t") == 0.0            # newest message 1.5 s old
    assert monitor_with([]).rate("/t") == 0.0
    assert monitor_with([0.1, 0.2]).rate("/t") == 0.0                      # too few messages to call it a rate


def test_low_rate_topic_is_estimated_correctly():
    assert abs(monitor_with([0.2 * i for i in range(10)]).rate("/t") - 5.0) < 0.5      # 5 Hz obstacle_distance
