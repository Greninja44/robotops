#!/usr/bin/env bash
# Verify a running RobotOps demo end-to-end. Prints PASS/FAIL per check and a summary.
#   ./verify_demo.sh            quick checks (~1 min)
#   ./verify_demo.sh --full     also run a complete LLM investigation + approved repair + verification (~2-3 min)
cd "$(dirname "$0")"
API="${ROBOTOPS_API:-http://127.0.0.1:8000}"
FULL=0; [[ "$1" == "--full" ]] && FULL=1
PASS=0; FAIL=0; FAILED=()
ok()   { printf "  \033[32mPASS\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; FAIL=$((FAIL+1)); FAILED+=("$1"); }
check(){ local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi; }
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
wait_for() { local n=$1; shift; for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }

echo "RobotOps demo verification"
echo "-- services"
check "backend reachable ($API)"       curl -sf -m 5 "$API/api/status"
check "frontend reachable"             bash -c "curl -sf -m 5 $API/ | grep -q '<div id=\"root\">'"
check "frontend bundle served"         bash -c "curl -sf -m 5 $API/ | grep -o '/assets/[^\"]*\\.js' | head -1 | xargs -I{} curl -sf -m 5 $API{} -o /dev/null"
STATUS=$(curl -sf -m 5 "$API/api/status" || echo '{}')
[[ "$(echo "$STATUS" | jget "d['ros']['available']" 2>/dev/null)" == "True" ]] && ok "ROS 2 client connected" || bad "ROS 2 client connected"
[[ "$(echo "$STATUS" | jget "d['supervisor']['reachable']" 2>/dev/null)" == "True" ]] && ok "demo robot supervisor reachable" || bad "demo robot supervisor reachable"
[[ "$(echo "$STATUS" | jget "d['llm']['model_available']" 2>/dev/null)" == "True" ]] && ok "LLM reachable ($(echo "$STATUS" | jget "d['llm']['model']"))" || bad "LLM reachable (Ollama + model)"

echo "-- robot"
if [[ "$(curl -sf -m 5 -X POST "$API/api/demo/reset" | jget "d['ok']" 2>/dev/null)" == "True" ]]; then ok "demo reset"; else bad "demo reset"; fi
healthy() { [[ "$(curl -sf -m 5 "$API/api/health" | jget "d['overall']")" == "HEALTHY" ]]; }
wait_for 45 healthy && ok "ROS system HEALTHY (all 6 nodes, topics, TF, odometry)" || bad "ROS system HEALTHY"

echo "-- agent tool execution"
TOOL=$(curl -sf -m 20 -X POST "$API/api/tools/inspect_topic" -H 'content-type: application/json' -d '{"args":{"topic":"/cmd_vel"}}' || echo '{}')
[[ "$(echo "$TOOL" | jget "d['success'] and d['data']['subscriber_count']==1 and d['data']['publisher_count']==1")" == "True" ]] && ok "inspect_topic(/cmd_vel): 1 publisher, 1 subscriber" || bad "inspect_topic(/cmd_vel)"
RATE=$(curl -sf -m 20 -X POST "$API/api/tools/measure_topic_rate" -H 'content-type: application/json' -d '{"args":{"topic":"/scan","duration":2}}' || echo '{}')
[[ "$(echo "$RATE" | jget "d['success'] and d['data']['rate_hz']>5")" == "True" ]] && ok "measure_topic_rate(/scan) = $(echo "$RATE" | jget "d['data']['rate_hz']") Hz" || bad "measure_topic_rate(/scan)"
BAD=$(curl -sf -m 20 -X POST "$API/api/tools/inspect_topic" -H 'content-type: application/json' -d '{"args":{"topic":"/x; rm -rf /"}}' || echo '{}')
[[ "$(echo "$BAD" | jget "(not d['success']) and 'invalid argument' in d['error']")" == "True" ]] && ok "malicious tool argument rejected" || bad "malicious tool argument rejected"
NS=$(curl -sf -m 20 -X POST "$API/api/tools/restart_component" -H 'content-type: application/json' -d '{"args":{"target":"base_controller"}}' || echo '{}')
[[ "$(echo "$NS" | jget "not d['success']")" == "True" ]] && ok "state-changing tool is not callable via the tool API" || bad "state-changing tool is not callable via the tool API"

echo "-- fault injection"
RES=$(curl -sf -m 20 -X POST "$API/api/faults/inject" -H 'content-type: application/json' -d '{"fault":"controller_crash"}' || echo '{}')
[[ "$(echo "$RES" | jget "d['ok']")" == "True" ]] && ok "fault injected (controller_crash)" || bad "fault injected"
not_healthy() { ! healthy; }
wait_for 25 not_healthy && ok "system health left HEALTHY (a real component failed)" || bad "system health left HEALTHY"
node_missing() { curl -sf -m 20 -X POST "$API/api/tools/list_nodes" -H 'content-type: application/json' -d '{"args":{}}' | jget "'/base_controller' in d['data']['expected_missing']" | grep -q True; }
wait_for 20 node_missing && ok "list_nodes sees /base_controller missing" || bad "list_nodes sees /base_controller missing"

if [[ $FULL == 1 ]]; then
  echo "-- full loop (LLM investigation -> approval -> repair -> verification)"
  INV=$(curl -sf -m 10 -X POST "$API/api/investigations" -H 'content-type: application/json' -d '{"query":"My robot stopped moving. Diagnose it."}' | jget "d['id']" 2>/dev/null)
  [[ -n "$INV" ]] && ok "investigation started ($INV)" || bad "investigation started"
  phase() { curl -sf -m 5 "$API/api/investigations/$INV" | jget "d['phase']"; }
  PH=""; for _ in $(seq 1 90); do PH=$(phase); [[ "$PH" =~ ^(awaiting_approval|inconclusive|error|resolved|repair_failed)$ ]] && break; sleep 3; done
  if [[ "$PH" == "awaiting_approval" ]]; then
    ok "agent produced an evidence-backed proposal"
    D=$(curl -sf -m 5 "$API/api/investigations/$INV")
    [[ "$(echo "$D" | jget "d['diagnosis']['faulty_component']")" == "base_controller" ]] && ok "diagnosis: base_controller" || bad "diagnosis: base_controller (got $(echo "$D" | jget "d['diagnosis']['faulty_component']"))"
    [[ "$(echo "$D" | jget "len(d['diagnosis']['evidence'])>=2")" == "True" ]] && ok "diagnosis cites >= 2 evidence items" || bad "diagnosis cites evidence"
    PID=$(echo "$D" | jget "d['proposal']['id']")
    sleep 1; healthy && bad "robot untouched before approval" || ok "nothing repaired before approval"
    curl -sf -m 10 -X POST "$API/api/investigations/$INV/approve" -H 'content-type: application/json' -d "{\"proposal_id\":\"$PID\",\"operator\":\"verify_demo\"}" >/dev/null && ok "approved" || bad "approved"
    for _ in $(seq 1 40); do PH=$(phase); [[ "$PH" =~ ^(resolved|repair_failed|error)$ ]] && break; sleep 3; done
    [[ "$PH" == "resolved" ]] && ok "RECOVERY VERIFIED by independent checks" || bad "recovery verified (phase=$PH)"
    healthy && ok "system back to HEALTHY" || bad "system back to HEALTHY"
  else
    bad "agent produced a proposal (phase=$PH)"
  fi
fi

curl -sf -m 30 -X POST "$API/api/demo/reset" >/dev/null 2>&1
echo
if [[ $FAIL -eq 0 ]]; then printf "\033[32mRESULT: PASS\033[0m  (%d checks)\n" "$PASS"; exit 0
else printf "\033[31mRESULT: FAIL\033[0m  (%d passed, %d failed)\n" "$PASS" "$FAIL"; printf '  - %s\n' "${FAILED[@]}"; exit 1; fi
