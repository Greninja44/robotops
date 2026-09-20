"""The rclpy executor thread must survive the subscription create/destroy race instead of killing the ROS client."""
import threading
import time

from backend.ros_tools.client import RosClient


class FlakyExecutor:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def spin_once(self, timeout_sec=0.1):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("cannot use Destroyable because destruction was requested")
        time.sleep(0.005)


def run_spin(executor, seconds=0.6):
    c = RosClient()
    c._executor = executor
    # Real start() ordering: the spin thread starts BEFORE _started is set. (A bug once tied the loop to _started and the executor
    # never spun, so the client saw the graph but received no data.)
    t = threading.Thread(target=c._spin, daemon=True)
    t.start()
    time.sleep(0.05)
    c._started = True
    time.sleep(seconds)
    alive, available = t.is_alive(), c.available     # read before the test stops the thread
    c._stop_spin.set()
    t.join(2)
    c.available_while_running = available
    return c, alive


def test_transient_executor_errors_are_survived_and_counted():
    ex = FlakyExecutor(failures=25)
    c, alive = run_spin(ex)
    assert alive and c._error is None and c.available_while_running is True
    assert c.spin_errors == 25 and "Destroyable" in c.last_spin_error
    assert ex.calls > 25                          # it kept spinning after the errors


def test_a_genuinely_dead_executor_is_still_reported():
    ex = FlakyExecutor(failures=10**9)
    c, alive = run_spin(ex, seconds=6)
    assert c._error and c._error.startswith("executor stopped")
    assert c.available_while_running is False


def test_spin_thread_does_not_exit_when_started_flag_is_not_yet_set():
    ex = FlakyExecutor(failures=0)
    c = RosClient()
    c._executor = ex
    t = threading.Thread(target=c._spin, daemon=True)
    t.start()                        # _started is still False here, exactly as in start()
    time.sleep(0.3)
    assert t.is_alive() and ex.calls > 10
    c._stop_spin.set()
    t.join(2)
    assert not t.is_alive()
