# RobotOps benchmark results

Run: 2026-09-19 11:24:06 · model `llama3.2:latest` (thinking off) · Linux 6.18.33.2-microsoft-standard-WSL2, ROS 2 lyrical

Query given to the agent for every fault: *"Diagnose the robot."* (no hint about the fault).

| metric | value |
|---|---|
| runs | 5 |
| valid runs | 5 |
| diagnosis success rate | 0.0 |
| repair success rate | 0.0 |
| verification success rate | 0.0 |
| median diagnosis time s | None |
| median total time s | 9.7 |
| median tool calls | 7 |

| fault | diagnosed component | diagnosis | repair | verified | diag time (s) | total (s) | tool calls | tools used |
|---|---|---|---|---|---|---|---|---|
| controller_crash | - | ❌ | ❌ | ❌ | None | 16.6 | 7 | get_ros_health → inspect_node → inspect_topic → inspect_node → get_recent_logs → get_recent_diagnostics → list_nodes |
| lidar_failure | - | ❌ | ❌ | ❌ | None | 9.7 | 7 | get_ros_health → get_recent_diagnostics → inspect_topic → inspect_parameters → get_recent_logs → inspect_topic → check_tf |
| tf_failure | - | ❌ | ❌ | ❌ | None | 9.5 | 8 | get_ros_health → get_recent_diagnostics → inspect_parameters → inspect_parameters → get_recent_logs → inspect_parameters → get_recent_logs → inspect_topic |
| topic_misconfig | - | ❌ | ❌ | ❌ | None | 17.0 | 7 | get_ros_health → get_recent_diagnostics → inspect_topic → inspect_node → inspect_node → check_tf → list_nodes |
| node_crash | - | ❌ | ❌ | ❌ | None | 8.9 | 7 | get_ros_health → inspect_node → inspect_parameters → get_recent_logs → list_nodes → get_recent_diagnostics → list_topics |

Diagnosis = the component named by the agent matches the injected fault. Repair = the approved (benchmark auto-approve) action was executed. Verified = independent post-repair re-measurement of the whole robot passed.
