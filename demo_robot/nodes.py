#!/usr/bin/env python3
"""Deterministic ROS 2 demo robot for RobotOps.

Each component runs as its own OS process:  python3 nodes.py <component> [--ros-args ...]

Faults are triggered by the supervisor with SIGUSR1. What a fault *does* is
component-specific (crash, driver stall, hang) and is deliberately not
announced on any ROS interface -- RobotOps has to infer it from symptoms.
"""
import math
import os
import random
import signal
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rcl_interfaces.msg import SetParametersResult
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState, LaserScan
from std_msgs.msg import Float32
from tf2_ros import TransformBroadcaster, Buffer, TransformListener

OK, WARN, ERROR = DiagnosticStatus.OK, DiagnosticStatus.WARN, DiagnosticStatus.ERROR


class DemoNode(Node):
    hardware_id = "demo_robot"

    def __init__(self, name):
        super().__init__(name)
        self.fault_requested = False
        self.hung = False
        self._diag_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.create_timer(1.0, self._publish_diagnostics)

    # subclasses override
    def diagnostic(self):
        return OK, "Running", {}

    def on_fault(self):
        pass

    def _publish_diagnostics(self):
        if self.hung:
            return
        level, message, values = self.diagnostic()
        status = DiagnosticStatus(
            level=level, name=self.get_name(), message=message, hardware_id=self.hardware_id,
            values=[KeyValue(key=k, value=str(v)) for k, v in values.items()])
        arr = DiagnosticArray(status=[status])
        arr.header.stamp = self.get_clock().now().to_msg()
        self._diag_pub.publish(arr)

    def crash(self, message, code):
        """Simulate an unrecoverable error: log FATAL to /rosout, then exit."""
        self.get_logger().fatal(message)
        time.sleep(0.5)  # let /rosout deliver before the process disappears
        self.destroy_node()
        time.sleep(0.3)  # let the graph update propagate (otherwise DDS lease expiry takes ~10 s)
        rclpy.try_shutdown()
        os._exit(code)


class VelocityCommander(DemoNode):
    """Stands in for a planner/teleop: publishes a gentle patrol pattern on /cmd_vel."""

    def __init__(self):
        super().__init__("velocity_commander")
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.t0 = time.monotonic()
        self.create_timer(0.1, self.tick)

    def tick(self):
        t = time.monotonic() - self.t0
        msg = Twist()
        msg.linear.x = 0.25
        msg.angular.z = 0.25 + 0.1 * math.sin(t / 3.0)
        self.pub.publish(msg)

    def diagnostic(self):
        return OK, "Publishing patrol velocity commands on /cmd_vel", {"rate_hz": 10}


class BaseController(DemoNode):
    """Differential-drive base controller: /cmd_vel -> wheel velocities."""
    WHEEL_SEP, WHEEL_R, CMD_TIMEOUT = 0.40, 0.05, 0.5
    hardware_id = "motor_board_can0"

    def __init__(self):
        super().__init__("base_controller")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("max_wheel_speed", 12.0)
        self.last_cmd, self.last_cmd_time, self.cmd_count = Twist(), 0.0, 0
        self.sub = None
        self._subscribe(self.get_parameter("cmd_vel_topic").value)
        self.add_on_set_parameters_callback(self._on_params)
        self.pub = self.create_publisher(JointState, "/wheel_states", 10)
        self.create_timer(0.05, self.tick)

    def _subscribe(self, topic):
        if self.sub is not None:
            self.destroy_subscription(self.sub)
        self.cmd_topic = topic
        self.sub = self.create_subscription(Twist, topic, self._on_cmd, 10)
        self.get_logger().info(f"Listening for velocity commands on {topic}")

    def _on_params(self, params):
        for p in params:
            if p.name == "cmd_vel_topic":
                if p.type_ != Parameter.Type.STRING or not p.value.startswith("/"):
                    return SetParametersResult(successful=False, reason="must be an absolute topic name")
                self._subscribe(p.value)
        return SetParametersResult(successful=True)

    def _on_cmd(self, msg):
        self.last_cmd, self.last_cmd_time = msg, time.monotonic()
        self.cmd_count += 1

    def tick(self):
        cmd = self.last_cmd if time.monotonic() - self.last_cmd_time < self.CMD_TIMEOUT else Twist()
        v, w = cmd.linear.x, cmd.angular.z
        left = (v - w * self.WHEEL_SEP / 2) / self.WHEEL_R
        right = (v + w * self.WHEEL_SEP / 2) / self.WHEEL_R
        msg = JointState(name=["left_wheel", "right_wheel"], velocity=[left, right])
        msg.header.stamp = self.get_clock().now().to_msg()
        self.pub.publish(msg)

    def diagnostic(self):
        age = time.monotonic() - self.last_cmd_time
        vals = {"cmd_vel_topic": self.cmd_topic, "commands_received": self.cmd_count}
        if self.last_cmd_time == 0.0:
            return WARN, f"No velocity commands received yet on {self.cmd_topic}", vals
        if age > 2.0:
            return WARN, f"No velocity commands on {self.cmd_topic} for {age:.1f}s - wheels stopped", vals
        return OK, f"Driving wheels from {self.cmd_topic}", vals

    def on_fault(self):
        self.crash("Motor driver CAN bus timeout: no heartbeat from motor board for 500 ms. "
                   "base_controller terminating.", 1)


class WheelOdometry(DemoNode):
    """Integrates wheel velocities into /odom and TF odom->base_link."""

    def __init__(self):
        super().__init__("wheel_odometry")
        self.x = self.y = self.th = 0.0
        self.v = self.w = 0.0
        self.last_wheels = 0.0
        self.last_t = time.monotonic()
        self.create_subscription(JointState, "/wheel_states", self._on_wheels, 10)
        self.pub = self.create_publisher(Odometry, "/odom", 10)
        self.tf = TransformBroadcaster(self)
        self.create_timer(0.05, self.tick)

    def _on_wheels(self, msg):
        if len(msg.velocity) == 2:
            left, right = msg.velocity
            r, sep = BaseController.WHEEL_R, BaseController.WHEEL_SEP
            self.v = (left + right) * r / 2
            self.w = (right - left) * r / sep
            self.last_wheels = time.monotonic()

    def tick(self):
        now = time.monotonic()
        dt, self.last_t = now - self.last_t, now
        if now - self.last_wheels > 0.5:
            self.v = self.w = 0.0
        self.th += self.w * dt
        self.x += self.v * math.cos(self.th) * dt
        self.y += self.v * math.sin(self.th) * dt
        stamp = self.get_clock().now().to_msg()
        qz, qw = math.sin(self.th / 2), math.cos(self.th / 2)

        odom = Odometry()
        odom.header.stamp, odom.header.frame_id, odom.child_frame_id = stamp, "odom", "base_link"
        odom.pose.pose.position.x, odom.pose.pose.position.y = self.x, self.y
        odom.pose.pose.orientation.z, odom.pose.pose.orientation.w = qz, qw
        odom.twist.twist.linear.x, odom.twist.twist.angular.z = self.v, self.w
        self.pub.publish(odom)

        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = stamp, "odom", "base_link"
        t.transform.translation.x, t.transform.translation.y = self.x, self.y
        t.transform.rotation.z, t.transform.rotation.w = qz, qw
        self.tf.sendTransform(t)

    def diagnostic(self):
        age = time.monotonic() - self.last_wheels
        if self.last_wheels == 0.0 or age > 2.0:
            return WARN, "No /wheel_states received - odometry is not updating (robot stationary)", \
                {"wheel_states_age_s": round(age, 1) if self.last_wheels else "never"}
        return OK, "Integrating wheel odometry", {"x": round(self.x, 2), "y": round(self.y, 2)}


class LidarDriver(DemoNode):
    """Simulated 2D lidar in a 6 m x 4 m room."""
    hardware_id = "rplidar_ttyUSB0"
    N = 360

    def __init__(self):
        super().__init__("lidar_driver")
        self.pub = self.create_publisher(LaserScan, "/scan", 10)
        self.stalled_since = None
        self.scans = 0
        self.rng = random.Random(7)
        self.create_timer(0.1, self.tick)

    def tick(self):
        if self.stalled_since is not None:
            return
        msg = LaserScan()
        msg.header.stamp, msg.header.frame_id = self.get_clock().now().to_msg(), "laser"
        msg.angle_min, msg.angle_max = -math.pi, math.pi
        msg.angle_increment = 2 * math.pi / self.N
        msg.scan_time, msg.time_increment = 0.1, 0.1 / self.N
        msg.range_min, msg.range_max = 0.12, 12.0
        ranges = []
        for i in range(self.N):
            a = msg.angle_min + i * msg.angle_increment
            c, s = math.cos(a), math.sin(a)
            d = min(3.0 / abs(c) if abs(c) > 1e-6 else 1e9, 2.0 / abs(s) if abs(s) > 1e-6 else 1e9)
            ranges.append(float(d + self.rng.gauss(0, 0.01)))
        msg.ranges = ranges
        self.pub.publish(msg)
        self.scans += 1

    def diagnostic(self):
        if self.stalled_since is not None:
            age = time.monotonic() - self.stalled_since
            return ERROR, f"No data from device for {age:.1f}s (serial read timeout on /dev/ttyUSB0)", \
                {"scans_published": self.scans, "device": "/dev/ttyUSB0"}
        return OK, "Scanning at 10 Hz", {"scans_published": self.scans, "device": "/dev/ttyUSB0"}

    def on_fault(self):
        self.stalled_since = time.monotonic()
        self.get_logger().error("Serial read timeout on /dev/ttyUSB0 - lidar driver stalled, no scans")


class TfBroadcaster(DemoNode):
    """Publishes the base_link->laser mounting transform (like robot_state_publisher)."""

    def __init__(self):
        super().__init__("tf_broadcaster")
        self.tf = TransformBroadcaster(self)
        self.create_timer(0.05, self.tick)

    def tick(self):
        if self.hung:
            return
        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = self.get_clock().now().to_msg(), "base_link", "laser"
        t.transform.translation.x, t.transform.translation.z = 0.15, 0.20
        t.transform.rotation.w = 1.0
        self.tf.sendTransform(t)

    def diagnostic(self):
        return OK, "Broadcasting base_link->laser", {"rate_hz": 20}

    def on_fault(self):
        self.hung = True  # deadlocked: stays in the graph, publishes nothing


class ObstacleMonitor(DemoNode):
    """Navigation safety layer: nearest obstacle distance from /scan (needs TF)."""

    def __init__(self):
        super().__init__("obstacle_monitor")
        self.last_scan, self.last_scan_t = None, 0.0
        self.tf_error = None
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self.create_subscription(Odometry, "/odom", lambda m: None, 10)
        self.pub = self.create_publisher(Float32, "/obstacle_distance", 10)
        self.create_timer(0.2, self.tick)

    def _on_scan(self, msg):
        self.last_scan, self.last_scan_t = msg, time.monotonic()

    def tick(self):
        if self.last_scan is None or time.monotonic() - self.last_scan_t > 1.0:
            return
        try:
            t = self.buffer.lookup_transform("base_link", "laser", rclpy.time.Time())
            age = (self.get_clock().now() - rclpy.time.Time.from_msg(t.header.stamp)).nanoseconds / 1e9
            if age > 1.0:
                raise RuntimeError(f"transform is {age:.1f}s old")
            self.tf_error = None
        except Exception as e:  # noqa: BLE001 - any TF failure is reported the same way
            self.tf_error = str(e).splitlines()[0][:160]
            return
        valid = [r for r in self.last_scan.ranges if self.last_scan.range_min < r < self.last_scan.range_max]
        self.pub.publish(Float32(data=min(valid) if valid else float("inf")))

    def diagnostic(self):
        if self.last_scan is None or time.monotonic() - self.last_scan_t > 1.0:
            age = "never" if self.last_scan is None else f"{time.monotonic() - self.last_scan_t:.1f}s"
            return ERROR, f"LaserScan on /scan is stale (last: {age}) - obstacle detection disabled", {}
        if self.tf_error:
            return ERROR, f"Cannot transform base_link<-laser: {self.tf_error} - obstacle detection disabled", {}
        return OK, "Obstacle detection active", {}

    def on_fault(self):
        self.crash("Unhandled exception in costmap update thread: IndexError: list index out of range. "
                   "obstacle_monitor terminating.", 134)


COMPONENTS = {
    "velocity_commander": VelocityCommander,
    "base_controller": BaseController,
    "wheel_odometry": WheelOdometry,
    "lidar_driver": LidarDriver,
    "tf_broadcaster": TfBroadcaster,
    "obstacle_monitor": ObstacleMonitor,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMPONENTS:
        sys.exit(f"usage: nodes.py <{'|'.join(COMPONENTS)}> [--ros-args ...]")
    rclpy.init(args=sys.argv)
    node = COMPONENTS[sys.argv[1]]()
    signal.signal(signal.SIGUSR1, lambda *_: setattr(node, "fault_requested", True))
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            if node.fault_requested:
                node.fault_requested = False
                node.on_fault()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
