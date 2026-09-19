# Shared environment for every RobotOps process. Source, don't execute.
ROBOTOPS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROBOTOPS_ROOT
set +u
source /opt/ros/lyrical/setup.bash
export ROS_DOMAIN_ID="${ROBOTOPS_DOMAIN_ID:-73}"
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://$ROBOTOPS_ROOT/config/cyclonedds.xml"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROBOTOPS_ROOT${PYTHONPATH:+:$PYTHONPATH}"
