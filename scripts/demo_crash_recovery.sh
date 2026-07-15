#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${DEMO_ENV_FILE:-$REPO_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

for command_name in temporal uv python3 pgrep; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 1
  fi
done

export TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-localhost:7233}"
export TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
export TEMPORAL_TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-lakebase-agent-task-queue}"

# The delay is opt-in in normal runs and deliberately enabled here. It gives the
# presenter a repeatable window after income verification and before policy lookup.
export DEMO_TOOL_DELAY_SECONDS="${DEMO_TOOL_DELAY_SECONDS:-2}"

APPLICANT_ID="${DEMO_APPLICANT_ID:-applicant-borderline}"
LOAN_AMOUNT="${DEMO_LOAN_AMOUNT:-25000}"
LOAN_PURPOSE="${DEMO_LOAN_PURPOSE:-debt_consolidation}"
MAX_TURNS="${DEMO_MAX_TURNS:-6}"
KILL_AFTER_TOOL_COUNT="${DEMO_KILL_AFTER_TOOL_COUNT:-2}"
POST_KILL_POINT_DELAY_SECONDS="${DEMO_POST_KILL_POINT_DELAY_SECONDS:-0.3}"
KILL_POINT_TIMEOUT_SECONDS="${DEMO_KILL_POINT_TIMEOUT_SECONDS:-90}"
RECOVERY_TIMEOUT_SECONDS="${DEMO_RECOVERY_TIMEOUT_SECONDS:-150}"
WORKER_WARMUP_SECONDS="${DEMO_WORKER_WARMUP_SECONDS:-2}"
START_TEMPORAL_SERVER="${DEMO_START_TEMPORAL_SERVER:-auto}"
KEEP_WORKER_RUNNING="${DEMO_KEEP_WORKER_RUNNING:-1}"

RUN_ID="${DEMO_RUN_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
WORKFLOW_ID="lakebase-agent-$RUN_ID"
LOG_DIR="${DEMO_LOG_DIR:-${TMPDIR:-/tmp}/temporal-lakebase-demo-$RUN_ID}"
mkdir -p "$LOG_DIR"

SERVER_PID=""
WORKER_PID=""
WORKER_LAUNCHER_PID=""
STARTER_PID=""
STARTER_LAUNCHER_PID=""
starter_child_pid=""
PRESERVE_PROCESSES=0

cleanup() {
  if [[ "$PRESERVE_PROCESSES" == "1" ]]; then
    return
  fi

  for pid in "$STARTER_PID" "$STARTER_LAUNCHER_PID" "$WORKER_PID" "$WORKER_LAUNCHER_PID" "$SERVER_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
}

trap cleanup EXIT
trap 'exit 130' INT TERM

temporal_cli() {
  local tls_value="${TEMPORAL_TLS:-}"
  local args=(
    --address "$TEMPORAL_ADDRESS"
    --namespace "$TEMPORAL_NAMESPACE"
  )
  case "$tls_value" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      args+=(--tls)
      ;;
    0|false|FALSE|False|no|NO|No|off|OFF|Off)
      args+=(--tls=false)
      ;;
    "")
      if [[ -n "${TEMPORAL_API_KEY:-}" ]]; then
        args+=(--tls)
      else
        args+=(--tls=false)
      fi
      ;;
    *)
      echo "TEMPORAL_TLS must be true or false, not: $tls_value" >&2
      return 2
      ;;
  esac
  if [[ -n "${TEMPORAL_API_KEY:-}" ]]; then
    args+=(--api-key "$TEMPORAL_API_KEY")
  fi
  temporal "$@" "${args[@]}"
}

temporal_available() {
  temporal_cli workflow list --limit 1 --command-timeout 5s >/dev/null 2>&1
}

use_local_temporal_target() {
  export TEMPORAL_ADDRESS="127.0.0.1:7233"
  export TEMPORAL_NAMESPACE="default"
  export TEMPORAL_TLS="false"
  unset TEMPORAL_API_KEY
}

start_local_temporal() {
  use_local_temporal_target
  echo "Starting a local Temporal development server..."
  temporal server start-dev --ip 127.0.0.1 --port 7233 \
    >"$LOG_DIR/temporal-server.log" 2>&1 &
  SERVER_PID=$!

  local attempt=0
  while ! temporal_available; do
    attempt=$((attempt + 1))
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
      echo "Temporal development server exited before it became ready." >&2
      tail -n 40 "$LOG_DIR/temporal-server.log" >&2 || true
      exit 1
    fi
    if (( attempt >= 60 )); then
      echo "Timed out waiting for the local Temporal development server." >&2
      exit 1
    fi
    sleep 0.5
  done
}

case "$START_TEMPORAL_SERVER" in
  1|true|always)
    use_local_temporal_target
    if ! temporal_available; then
      start_local_temporal
    fi
    ;;
  0|false|never)
    if ! temporal_available; then
      echo "Temporal is unavailable at $TEMPORAL_ADDRESS and automatic startup is disabled." >&2
      exit 1
    fi
    ;;
  auto)
    if ! temporal_available; then
      case "$TEMPORAL_ADDRESS" in
        localhost:7233|127.0.0.1:7233)
          start_local_temporal
          ;;
        *)
          echo "Temporal Cloud is unavailable at $TEMPORAL_ADDRESS." >&2
          echo "Check TEMPORAL_NAMESPACE, TEMPORAL_API_KEY, and TEMPORAL_TLS in $ENV_FILE." >&2
          exit 1
          ;;
      esac
    fi
    ;;
  *)
    echo "DEMO_START_TEMPORAL_SERVER must be auto, always/true/1, or never/false/0." >&2
    exit 1
    ;;
esac

start_worker() {
  local log_file="$1"
  local child_pid=""
  (
    cd "$REPO_ROOT"
    exec env PYTHONUNBUFFERED=1 uv run python -m lakebase_agentworkflow.worker
  ) >"$log_file" 2>&1 &
  WORKER_LAUNCHER_PID=$!

  sleep "$WORKER_WARMUP_SECONDS"
  if ! kill -0 "$WORKER_LAUNCHER_PID" >/dev/null 2>&1; then
    echo "Worker exited during startup." >&2
    tail -n 60 "$log_file" >&2 || true
    exit 1
  fi

  # uv may either exec Python or remain as its parent. Walk to the leaf so the
  # SIGKILL below always targets the actual worker process, not only a launcher.
  WORKER_PID="$WORKER_LAUNCHER_PID"
  while true; do
    child_pid="$(pgrep -P "$WORKER_PID" 2>/dev/null | head -n 1 || true)"
    if [[ -z "$child_pid" ]]; then
      break
    fi
    WORKER_PID="$child_pid"
  done
}

query_snapshot() {
  temporal_cli workflow query \
    --workflow-id "$WORKFLOW_ID" \
    --type status \
    --command-timeout 5s \
    --output json 2>/dev/null
}

# Temporal CLI releases have emitted both decoded query JSON and a payload
# envelope. This reader accepts either shape and also handles base64 JSON data.
snapshot_value() {
  local field="$1"
  python3 -c '
import base64
import json
import re
import sys

field = sys.argv[1]
raw = sys.stdin.read()
target = re.sub(r"[^a-z0-9]", "", field.lower())

def find(value, depth=0):
    if depth > 12:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) == target:
                return item
        for item in value.values():
            found = find(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[", "\"")):
            try:
                return find(json.loads(stripped), depth + 1)
            except (TypeError, ValueError):
                pass
        if len(stripped) >= 8:
            try:
                decoded = base64.b64decode(stripped, validate=True).decode("utf-8")
                if decoded.lstrip().startswith(("{", "[", "\"")):
                    return find(json.loads(decoded), depth + 1)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                pass
    return None

try:
    document = json.loads(raw)
except json.JSONDecodeError:
    document = raw

value = find(document)
if value is not None:
    print(value if isinstance(value, (str, int, float)) else json.dumps(value))
elif field == "tool_call_count":
    match = re.search(r"tool[_ ]?call[_ ]?count\D+(\d+)", raw, re.IGNORECASE)
    if match:
        print(match.group(1))
elif field == "status":
    match = re.search(
        r"awaiting_review|waiting_for_tool|completed|running|failed|cancelled",
        raw,
        re.IGNORECASE,
    )
    if match:
        print(match.group(0))
' "$field"
}

normalize_state() {
  tr '[:upper:]' '[:lower:]' | tr -d '"[:space:]'
}

echo "Starting worker for borderline applicant $APPLICANT_ID..."
start_worker "$LOG_DIR/worker-before-crash.log"

echo "Starting workflow $WORKFLOW_ID with max_turns=$MAX_TURNS..."
(
  cd "$REPO_ROOT"
  exec env PYTHONUNBUFFERED=1 uv run python -m lakebase_agentworkflow.starter \
    "Underwrite this personal loan" \
    --run-id "$RUN_ID" \
    --applicant-id "$APPLICANT_ID" \
    --loan-amount "$LOAN_AMOUNT" \
    --loan-purpose "$LOAN_PURPOSE" \
    --max-turns "$MAX_TURNS"
) >"$LOG_DIR/starter.log" 2>&1 &
STARTER_LAUNCHER_PID=$!
STARTER_PID="$STARTER_LAUNCHER_PID"
sleep 0.1
while true; do
  starter_child_pid="$(pgrep -P "$STARTER_PID" 2>/dev/null | head -n 1 || true)"
  if [[ -z "$starter_child_pid" ]]; then
    break
  fi
  STARTER_PID="$starter_child_pid"
done

echo "Waiting for $KILL_AFTER_TOOL_COUNT completed tool calls..."
deadline=$((SECONDS + KILL_POINT_TIMEOUT_SECONDS))
observed_tool_count=0
while (( SECONDS < deadline )); do
  snapshot="$(query_snapshot || true)"
  count="$(printf '%s' "$snapshot" | snapshot_value tool_call_count || true)"
  state="$(printf '%s' "$snapshot" | snapshot_value status | normalize_state || true)"

  if [[ "$count" =~ ^[0-9]+$ ]]; then
    observed_tool_count="$count"
    if (( observed_tool_count >= KILL_AFTER_TOOL_COUNT )); then
      break
    fi
  fi

  case "$state" in
    *awaiting_review*|*completed*)
      echo "The workflow reached $state before the crash point." >&2
      echo "Increase DEMO_TOOL_DELAY_SECONDS (current: $DEMO_TOOL_DELAY_SECONDS)." >&2
      exit 1
      ;;
    *failed*|*cancelled*)
      echo "The workflow entered terminal state $state before the crash point." >&2
      exit 1
      ;;
  esac

  if ! kill -0 "$STARTER_LAUNCHER_PID" >/dev/null 2>&1; then
    echo "Starter exited before the crash point." >&2
    tail -n 80 "$LOG_DIR/starter.log" >&2 || true
    exit 1
  fi
  sleep 0.2
done

if (( observed_tool_count < KILL_AFTER_TOOL_COUNT )); then
  echo "Timed out waiting for the crash point; observed $observed_tool_count tool calls." >&2
  tail -n 80 "$LOG_DIR/worker-before-crash.log" >&2 || true
  exit 1
fi

# Let the fast Lakebase projection activities for the second result finish.
# The next loan tool is still inside its demo delay, so policy has not run.
sleep "$POST_KILL_POINT_DELAY_SECONDS"

echo
echo "Crash point reached: credit and income are durable; policy lookup has not completed."
echo "Hard-killing worker PID $WORKER_PID with SIGKILL..."
kill -9 "$WORKER_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$WORKER_LAUNCHER_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if kill -0 "$WORKER_LAUNCHER_PID" >/dev/null 2>&1; then
  kill -9 "$WORKER_LAUNCHER_PID" >/dev/null 2>&1 || true
fi
wait "$WORKER_LAUNCHER_PID" >/dev/null 2>&1 || true
WORKER_PID=""
WORKER_LAUNCHER_PID=""

echo
echo "PRESENTER PAUSE — the application worker is gone, but the workflow still exists."
echo "Temporal history:"
echo "  temporal workflow show --address $TEMPORAL_ADDRESS --namespace $TEMPORAL_NAMESPACE --workflow-id $WORKFLOW_ID --detailed"
echo "Temporal service state (does not require a running worker):"
temporal_cli workflow describe \
  --workflow-id "$WORKFLOW_ID" \
  --command-timeout 5s || true
echo "Lakebase evidence (run against databricks_postgres):"
echo "  SELECT turn, role, name, content, created_at"
echo "  FROM agent_ops.agent_messages WHERE run_id = '$RUN_ID'"
echo "  ORDER BY turn, created_at;"
echo

if [[ -t 0 && "${DEMO_AUTO_CONTINUE:-0}" != "1" ]]; then
  read -r -p "Press Enter to restart the worker and resume the exact execution... " _
else
  echo "Non-interactive mode: continuing without waiting for Enter."
fi

echo "Restarting worker..."
start_worker "$LOG_DIR/worker-after-crash.log"

echo "Polling the workflow query until it reaches AWAITING_REVIEW..."
deadline=$((SECONDS + RECOVERY_TIMEOUT_SECONDS))
final_state=""
while (( SECONDS < deadline )); do
  snapshot="$(query_snapshot || true)"
  state="$(printf '%s' "$snapshot" | snapshot_value status | normalize_state || true)"

  case "$state" in
    *awaiting_review*)
      final_state="awaiting_review"
      break
      ;;
    *completed*)
      final_state="completed"
      break
      ;;
    *failed*|*cancelled*)
      echo "Workflow entered terminal state $state after restart." >&2
      exit 1
      ;;
  esac

  sleep 0.5
done

if [[ -z "$final_state" ]]; then
  echo "Timed out waiting for recovery." >&2
  tail -n 100 "$LOG_DIR/worker-after-crash.log" >&2 || true
  exit 1
fi

echo
if [[ "$final_state" == "awaiting_review" ]]; then
  echo "Recovery proved: $WORKFLOW_ID resumed and is now AWAITING_REVIEW."
  echo "The worker did not replay the two completed tool calls or lose their results."
else
  echo "Recovery proved: $WORKFLOW_ID resumed and completed."
  echo "(This build completed directly instead of enabling the review gate.)"
fi

echo "Logs: $LOG_DIR"
echo "Run ID: $RUN_ID"

if [[ "$KEEP_WORKER_RUNNING" == "1" ]]; then
  PRESERVE_PROCESSES=1
  echo "Worker PID: $WORKER_PID"
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    echo "Local Temporal server PID: $SERVER_PID"
  fi
  if [[ -n "$STARTER_LAUNCHER_PID" ]] && kill -0 "$STARTER_LAUNCHER_PID" >/dev/null 2>&1; then
    echo "Starter observer PID: $STARTER_LAUNCHER_PID"
  fi
  echo "The restarted worker is still running so the presenter can submit the review."
  echo "Set DEMO_KEEP_WORKER_RUNNING=0 to have this script stop background processes."
else
  echo "The restarted worker and any local demo server will stop when this script exits."
fi
