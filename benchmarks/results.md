# RobotOps benchmark results

Run: 2026-09-19 12:12:43 · model `qwen3:4b` (thinking off) · Linux 6.18.33.2-microsoft-standard-WSL2, ROS 2 lyrical

Query given to the agent for every fault: *"Diagnose the robot."* (no hint about the fault).

| metric | value |
|---|---|
| runs | 10 |
| valid runs | 7 |
| diagnosis success rate | 0.714 |
| repair success rate | 0.714 |
| verification success rate | 0.571 |
| median diagnosis time s | 129.9 |
| median total time s | 147.8 |
| median tool calls | 2 |

| fault | diagnosed component | diagnosis | repair | verified | diag time (s) | total (s) | tool calls | tools used |
|---|---|---|---|---|---|---|---|---|
| controller_crash | base_controller | ✅ | ✅ | ✅ | 129.9 | 135.9 | 3 | get_ros_health → list_nodes → get_component_status |
| lidar_failure | - | ❌ | ❌ | ❌ | None | 151.3 | 2 | get_ros_health → get_recent_diagnostics |
| tf_failure | tf_broadcaster | ✅ | ✅ | ✅ | 95.6 | 101.8 | 2 | get_ros_health → get_recent_diagnostics |
| topic_misconfig | base_controller | ✅ | ✅ | ✅ | 140.4 | 147.8 | 3 | get_ros_health → get_recent_diagnostics → inspect_parameters |
| node_crash | - | ❌ | ❌ | ❌ | None | 120.1 | 1 | get_ros_health |
| controller_crash | - | baseline unhealthy | | | | | | |
| lidar_failure | lidar_driver | ✅ | ✅ | ❌ | 57.9 | 193.5 | 2 | get_ros_health → get_recent_diagnostics |
| tf_failure | - | baseline unhealthy | | | | | | |
| topic_misconfig | - | baseline unhealthy | | | | | | |
| node_crash | obstacle_monitor | ✅ | ✅ | ✅ | 151.8 | 156.9 | 2 | get_ros_health → list_topics |

Diagnosis = the component named by the agent matches the injected fault. Repair = the approved (benchmark auto-approve) action was executed. Verified = independent post-repair re-measurement of the whole robot passed.
