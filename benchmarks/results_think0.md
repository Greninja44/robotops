# RobotOps benchmark results

Run: 2026-09-19 11:06:41 · model `qwen3:4b` (thinking off) · Linux 6.18.33.2-microsoft-standard-WSL2, ROS 2 lyrical

Query given to the agent for every fault: *"Diagnose the robot."* (no hint about the fault).

| metric | value |
|---|---|
| runs | 5 |
| valid runs | 5 |
| diagnosis success rate | 0.8 |
| repair success rate | 1.0 |
| verification success rate | 0.8 |
| median diagnosis time s | 84.6 |
| median total time s | 89.6 |
| median tool calls | 2 |

| fault | diagnosed component | diagnosis | repair | verified | diag time (s) | total (s) | tool calls | tools used |
|---|---|---|---|---|---|---|---|---|
| controller_crash | base_controller | ✅ | ✅ | ✅ | 84.6 | 89.6 | 2 | get_ros_health → get_component_status |
| lidar_failure | lidar_driver | ✅ | ✅ | ✅ | 81.0 | 86.3 | 3 | get_ros_health → get_recent_diagnostics → inspect_parameters |
| tf_failure | tf_broadcaster | ✅ | ✅ | ✅ | 43.2 | 48.6 | 2 | get_ros_health → get_recent_diagnostics |
| topic_misconfig | wheel_odometry | ❌ | ✅ | ❌ | 114.0 | 443.7 | 5 | get_ros_health → measure_topic_rate → inspect_node → inspect_node → inspect_node |
| node_crash | obstacle_monitor | ✅ | ✅ | ✅ | 166.1 | 171.2 | 2 | get_ros_health → get_component_status |

Diagnosis = the component named by the agent matches the injected fault. Repair = the approved (benchmark auto-approve) action was executed. Verified = independent post-repair re-measurement of the whole robot passed.
