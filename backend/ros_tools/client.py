"""Thread-safe rclpy access for the backend.

One observer node spins in a background thread. It keeps a TF buffer, the latest
/diagnostics statuses and a /rosout ring buffer. Everything else (graph queries,
rate measurement, parameter reads) is done on demand.

RobotOps' own nodes/endpoints are filtered out of every graph query so that
e.g. measuring /cmd_vel never makes it look like /cmd_vel has a subscriber.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

SELF_PREFIXES = ("robotops", "_ros2cli", "launch_ros")


def is_self(node_name: str) -> bool:
    return node_name.lstrip("/").startswith(SELF_PREFIXES)


@dataclass
class DiagEntry:
    name: str
    level: int
    message: str
    hardware_id: str
    values: dict
    received: float  # monotonic


class RosUnavailable(RuntimeError):
    pass


class RosClient:
    def __init__(self, node_name: str = "robotops_observer"):
        self._node_name = node_name
        self._lock = threading.RLock()
        self._started = False
        self._error: str | None = None
        self.diagnostics: dict[str, DiagEntry] = {}
        self.rosout: deque = deque(maxlen=500)
        self._stop_spin = threading.Event()  # set by shutdown(); NOT tied to _started (which is set after the thread starts)
        self.spin_errors = 0                 # benign executor races survived (see _spin)
        self.last_spin_error: str | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            try:
                import rclpy
                from rclpy.executors import MultiThreadedExecutor
                from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
                from tf2_ros import Buffer, TransformListener
                from diagnostic_msgs.msg import DiagnosticArray
                from rcl_interfaces.msg import Log

                self._rclpy = rclpy
                self._context = rclpy.Context()
                rclpy.init(context=self._context)
                self.node = rclpy.create_node(self._node_name, context=self._context)
                self.tf_buffer = Buffer(node=self.node)
                self._tf_listener = TransformListener(self.tf_buffer, self.node, spin_thread=False)
                self.node.create_subscription(DiagnosticArray, "/diagnostics", self._on_diag, 50)
                rosout_qos = QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                                        reliability=ReliabilityPolicy.RELIABLE)
                self.node.create_subscription(Log, "/rosout", self._on_log, rosout_qos)
                self._stop_spin.clear()
                self._executor = MultiThreadedExecutor(num_threads=4, context=self._context)
                self._executor.add_node(self.node)
                self._thread = threading.Thread(target=self._spin, daemon=True, name="ros-spin")
                self._thread.start()
                self._started = True
                self._error = None
            except Exception as e:  # noqa: BLE001 - surface any init failure as "ROS unavailable"
                self._error = f"{type(e).__name__}: {e}"
                raise RosUnavailable(self._error) from e

    def _spin(self):
        """Spin until shutdown. rclpy raises ("cannot use Destroyable because destruction was requested") when a subscription
        that sample_topic() just destroyed is still in the executor's wait set; that is a benign race, so it is counted and the
        loop continues. The client is only declared broken if errors are continuous (a truly dead executor)."""
        consecutive = 0
        while not self._stop_spin.is_set():
            try:
                self._executor.spin_once(timeout_sec=0.1)
                consecutive = 0
            except Exception as e:  # noqa: BLE001
                consecutive += 1
                self.spin_errors += 1
                self.last_spin_error = f"{type(e).__name__}: {e}"
                if consecutive >= 200:      # ~4 s of nothing but errors: not a race, the executor is gone
                    self._error = f"executor stopped: {e}"
                    return
                time.sleep(0.02)

    def shutdown(self):
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop_spin.set()
            try:
                self._executor.shutdown(timeout_sec=1.0)
                self.node.destroy_node()
                self._rclpy.try_shutdown(context=self._context)
            except Exception:  # noqa: BLE001
                pass

    @property
    def available(self) -> bool:
        return self._started and self._error is None

    def require(self):
        if not self._started:
            self.start()
        if self._error:
            raise RosUnavailable(self._error)

    # ---------------------------------------------------------------- callbacks
    def _on_diag(self, msg):
        now = time.monotonic()
        for s in msg.status:
            self.diagnostics[s.name] = DiagEntry(
                s.name, int.from_bytes(s.level, "little") if isinstance(s.level, bytes) else int(s.level),
                s.message, s.hardware_id, {kv.key: kv.value for kv in s.values}, now)

    def _on_log(self, msg):
        if is_self(msg.name):
            return
        self.rosout.append({"stamp": msg.stamp.sec + msg.stamp.nanosec / 1e9, "level": int(msg.level),
                            "node": msg.name, "msg": msg.msg, "received": time.time()})

    # ---------------------------------------------------------------- graph
    def node_names(self) -> list[str]:
        self.require()
        out = []
        for name, ns in self.node.get_node_names_and_namespaces():
            full = (ns.rstrip("/") + "/" + name) if ns != "/" else "/" + name
            if not is_self(name):
                out.append(full)
        return sorted(set(out))

    def topics(self) -> dict[str, list[str]]:
        self.require()
        return {n: t for n, t in self.node.get_topic_names_and_types()}

    def endpoints(self, topic: str) -> tuple[list[dict], list[dict]]:
        self.require()

        def conv(infos):
            res = []
            for i in infos:
                if is_self(i.node_name):
                    continue
                ns = i.node_namespace.rstrip("/")
                res.append({"node": f"{ns}/{i.node_name}", "type": i.topic_type,
                            "reliability": i.qos_profile.reliability.name.lower(),
                            "durability": i.qos_profile.durability.name.lower()})
            return res
        return (conv(self.node.get_publishers_info_by_topic(topic)),
                conv(self.node.get_subscriptions_info_by_topic(topic)))

    def node_interfaces(self, full_name: str) -> dict:
        self.require()
        ns, _, name = full_name.rpartition("/")
        ns = ns or "/"
        n = self.node
        return {
            "publishers": {t: ty for t, ty in n.get_publisher_names_and_types_by_node(name, ns)},
            "subscribers": {t: ty for t, ty in n.get_subscriber_names_and_types_by_node(name, ns)},
            "services": sorted(t for t, _ in n.get_service_names_and_types_by_node(name, ns)),
        }

    # ---------------------------------------------------------------- sampling
    def sample_topic(self, topic: str, duration: float, keep: bool = False) -> dict:
        """Subscribe for `duration` seconds. Returns message count and arrival times,
        and with keep=True the first and last deserialized messages."""
        self.require()
        from rosidl_runtime_py.utilities import get_message
        types = self.topics().get(topic)
        if not types:
            return {"exists": False, "count": 0, "times": [], "messages": []}
        msg_cls = get_message(types[0])
        times, msgs = [], []

        def cb(m):
            times.append(time.monotonic())
            if keep:
                if len(msgs) < 2:
                    msgs.append(m)
                else:
                    msgs[1] = m
        with self._lock:
            sub = self.node.create_subscription(msg_cls, topic, cb, 50)
        try:
            time.sleep(duration)
        finally:
            with self._lock:
                self.node.destroy_subscription(sub)
        return {"exists": True, "type": types[0], "count": len(times), "times": times, "messages": msgs}

    # ---------------------------------------------------------------- tf
    def lookup_tf(self, parent: str, child: str) -> dict:
        self.require()
        from rclpy.time import Time
        from rclpy.duration import Duration
        try:
            t = self.tf_buffer.lookup_transform(parent, child, Time(), timeout=Duration(seconds=0.5))
        except Exception as e:  # noqa: BLE001 - tf2 raises several exception types
            return {"available": False, "error": str(e).splitlines()[0][:200]}
        stamp = Time.from_msg(t.header.stamp)
        age = (self.node.get_clock().now() - stamp).nanoseconds / 1e9
        tr = t.transform.translation
        return {"available": True, "age_s": round(age, 3),
                "translation": [round(tr.x, 3), round(tr.y, 3), round(tr.z, 3)]}

    def tf_frames(self) -> list[str]:
        self.require()
        import yaml
        try:
            return sorted((yaml.safe_load(self.tf_buffer.all_frames_as_yaml()) or {}).keys())
        except Exception:  # noqa: BLE001
            return []

    # ---------------------------------------------------------------- params
    def get_parameters(self, full_name: str, timeout: float = 2.0) -> dict:
        self.require()
        from rcl_interfaces.srv import ListParameters, GetParameters
        from rclpy.parameter import parameter_value_to_python
        lc = self.node.create_client(ListParameters, f"{full_name}/list_parameters")
        gc = self.node.create_client(GetParameters, f"{full_name}/get_parameters")
        try:
            if not lc.wait_for_service(timeout_sec=timeout):
                raise TimeoutError(f"parameter service of {full_name} not available")
            names = self._call(lc, ListParameters.Request(), timeout).result.names
            names = [n for n in names if not n.startswith("qos_overrides") and n not in ("use_sim_time", "start_type_description_service")]
            vals = self._call(gc, GetParameters.Request(names=names), timeout).values
            return {n: parameter_value_to_python(v) for n, v in zip(names, vals)}
        finally:
            self.node.destroy_client(lc)
            self.node.destroy_client(gc)

    def _call(self, client, req, timeout):
        fut = client.call_async(req)
        end = time.monotonic() + timeout
        while not fut.done():
            if time.monotonic() > end:
                raise TimeoutError(f"service call {client.srv_name} timed out")
            time.sleep(0.01)
        return fut.result()


_client: RosClient | None = None


def get_client() -> RosClient:
    global _client
    if _client is None:
        _client = RosClient()
    return _client
