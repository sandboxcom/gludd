#!/usr/bin/env bash
# azure_event_guard.sh — monitors Azure Activity Log to guard gludd smoke tests
#
# Detects expensive resource creation, duplicate names (loops), and wrong-account
# usage.  Integrates with the smoke-test PID file to kill runaway processes.
#
# ENV VARS (all required for operation):
#   AZURE_SUBSCRIPTION_ID     expected subscription UUID
#   AZURE_TENANT_ID           expected tenant UUID (optional, for audit)
#   AZURE_RESOURCE_GROUP      expected resource group name
#
# MODES:
#   --once     One-shot check; exit 0=clean, 1=violation, 2=auth/config error
#   --watch    Poll every 60s; exit 1 on first violation
#
# USAGE:
#   bash scripts/azure_event_guard.sh --once
#   bash scripts/azure_event_guard.sh --watch  (background via make)
#
# EXIT CODES:
#   0  clean — no bad resources
#   1  VIOLATION — expensive GPU, duplicate names, or wrong-account resource
#   2  auth/config error — missing env vars, az CLI not logged in
#   3  usage error — invalid arguments

set -euo pipefail

# ---- Config ----
LOOKBACK_MINUTES="${AZURE_EVENT_GUARD_LOOKBACK:-5}"
POLL_INTERVAL="${AZURE_EVENT_GUARD_INTERVAL:-60}"
SMOKE_PID_FILE="${SMOKE_PID_FILE:-.smoke-test.pid}"
VIOLATION_LOG="${VIOLATION_LOG:-/tmp/gludd-azure-violations.log}"

# Resource types that signal resource creation to watch.
WATCHED_OPERATIONS=(
  "Microsoft.Compute/virtualMachines/write"
  "Microsoft.ContainerInstance/containerGroups/write"
  "Microsoft.ContainerService/managedClusters/write"
  "Microsoft.Network/networkInterfaces/write"
  "Microsoft.Network/publicIPAddresses/write"
  "Microsoft.Resources/deployments/write"
)

# SKU/VM-size prefixes that cost >$10/hr.  Matches via grep regex (start-of-string).
EXPENSIVE_TYPE_PATTERN='^(Standard_NC|Standard_ND|Standard_NV|Standard_HB|Standard_HC|Standard_H|Standard_A100|Standard_M|Standard_L|Standard_G)'

# ---- Helpers ----
_die() {
  local code="$1"; shift
  printf '[azure_event_guard] ERROR: %s\n' "$*" >&2
  exit "$code"
}

_check_auth() {
  if [ -z "${AZURE_SUBSCRIPTION_ID:-}" ]; then
    _die 2 'AZURE_SUBSCRIPTION_ID not set'
  fi
  if [ -z "${AZURE_RESOURCE_GROUP:-}" ]; then
    _die 2 'AZURE_RESOURCE_GROUP not set'
  fi
  if ! command -v az >/dev/null 2>&1; then
    _die 2 'azure-cli (az) not found — install: brew install azure-cli && az login'
  fi
  if ! az account show --query id -o tsv >/dev/null 2>&1; then
    _die 2 'az not logged in — run: az login --tenant "$AZURE_TENANT_ID"'
  fi
}

_fetch_events() {
  local start_time
  start_time="$(date -u -v-${LOOKBACK_MINUTES}M '+%Y-%m-%dT%H:%M:%SZ')"
  az monitor activity-log list \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --start-time "$start_time" \
    --output json 2>/dev/null || return 1
}

# Extract resourceName + resourceType + operationName + subscriptionId + resourceGroup
# from activity log JSON (array of event objects).
# Returns tab-separated fields: resourceName\tresourceType\toperationName\tsubscriptionId\tresourceGroup
_parse_events() {
  local events_json="$1"
  printf '%s' "$events_json" | python3 -c '
import json, sys
events = json.load(sys.stdin)
for ev in events:
    rn = ev.get("resourceName") or (ev.get("resourceId") or "").rsplit("/",1)[-1]
    rt = ev.get("resourceType") or (ev.get("resourceId","//").split("/providers/")[-1].rsplit("/",1)[0] if "/providers/" in (ev.get("resourceId") or "") else "")
    sub = ev.get("subscriptionId") or ""
    rg = ev.get("resourceGroupName") or ""
    op = ev.get("operationName") or (ev.get("authorization",{}).get("action",""))
    if rn and op:
        print(f"{rn}\t{rt}\t{op}\t{sub}\t{rg}")
' 2>/dev/null
}

_kill_smoke_test() {
  if [ -f "$SMOKE_PID_FILE" ]; then
    local pid
    pid="$(cat "$SMOKE_PID_FILE" 2>/dev/null)" || return
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
      printf '[azure_event_guard] VIOLATION — killed smoke test PID %s\n' "$pid" >&2
    fi
  fi
}

_log_violation() {
  local reason="$1"; shift
  local detail="$*"
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '[%s] VIOLATION: %s | %s\n' "$ts" "$reason" "$detail" | tee -a "$VIOLATION_LOG" >&2
}

# ---- Core check ----
_check_events() {
  local events raw parsed
  raw="$(_fetch_events)" || {
    printf '[azure_event_guard] WARNING: activity log fetch failed (auth/network?)\n' >&2
    return 2
  }

  parsed="$(_parse_events "$raw")"
  if [ -z "$parsed" ]; then
    return 0  # no events — clean
  fi

  # --- Duplicate detection ---
  local duplicate_names
  duplicate_names="$(printf '%s\n' "$parsed" | cut -f1 | sort | uniq -d)"
  if [ -n "$duplicate_names" ]; then
    _log_violation 'DUPLICATE_RESOURCE' "names=$duplicate_names"
    return 1
  fi

  # Use a tempfile for the parsed lines so we can loop them.
  local tmp
  tmp="$(mktemp)" || _die 1 'failed to create temp file'
  printf '%s\n' "$parsed" > "$tmp"

  local violations=0
  while IFS=$'\t' read -r rname rtype rnop sub rg; do
    # --- Cost guard: expensive GPU/VM types ---
    # rtype should contain the full resource type path (e.g. Microsoft.Compute/virtualMachines)
    # rname may contain the VM size/SKU in some events; check both.
    if echo "${rname} ${rtype}" | grep -qE "$EXPENSIVE_TYPE_PATTERN" 2>/dev/null; then
      _log_violation 'EXPENSIVE_RESOURCE' "name=$rname type=$rtype"
      violations=$((violations + 1))
    fi

    # --- Account verification: wrong subscription ---
    if [ -n "$sub" ] && [ "$sub" != "$AZURE_SUBSCRIPTION_ID" ]; then
      _log_violation 'WRONG_SUBSCRIPTION' "expected=$AZURE_SUBSCRIPTION_ID got=$sub"
      violations=$((violations + 1))
    fi

    # --- Account verification: wrong resource group ---
    if [ -n "$rg" ] && [ "$rg" != "$AZURE_RESOURCE_GROUP" ]; then
      _log_violation 'WRONG_RESOURCE_GROUP' "expected=$AZURE_RESOURCE_GROUP got=$rg"
      violations=$((violations + 1))
    fi

    # --- Check operation is a known creation operation ---
    local matched=0
    for op in "${WATCHED_OPERATIONS[@]}"; do
      if [ "$rnop" = "$op" ]; then matched=1; break; fi
    done
    if [ "$matched" -eq 0 ]; then
      continue  # not a creation — skip detailed checks for this event
    fi
  done < "$tmp"
  rm -f "$tmp"

  if [ "$violations" -gt 0 ]; then
    _kill_smoke_test
    return 1
  fi
  return 0
}

# ---- Mode: --once ----
run_once() {
  printf '[azure_event_guard] one-shot check (lookback=%s min)\n' "$LOOKBACK_MINUTES"
  _check_events
  local rc=$?
  case $rc in
    0) printf '[azure_event_guard] CLEAN — no violations\n' ;;
    1) printf '[azure_event_guard] VIOLATIONS DETECTED (see log)\n' >&2 ;;
    2) printf '[azure_event_guard] AUTH/CONFIG ERROR\n' >&2 ;;
  esac
  exit $rc
}

# ---- Mode: --watch ----
run_watch() {
  printf '[azure_event_guard] starting watch mode (interval=%ss, lookback=%s min)\n' "$POLL_INTERVAL" "$LOOKBACK_MINUTES"
  while true; do
    _check_events
    local rc=$?
    if [ "$rc" -ne 0 ]; then
      printf '[azure_event_guard] watch: violation (rc=%s), exiting\n' "$rc" >&2
      exit 1
    fi
    printf '[azure_event_guard] poll clean at %s\n' "$(date -u '+%H:%M:%S')"
    sleep "$POLL_INTERVAL"
  done
}

# ---- Main ----
main() {
  local mode=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --once)  mode='once';;
      --watch) mode='watch';;
      --help|-h)
        printf 'Usage: %s --once|--watch\n' "$0"
        printf '  --once    One-shot check; exit 0=clean, 1=violation, 2=auth/config error\n'
        printf '  --watch   Poll every POLL_INTERVAL seconds; exit 1 on first violation\n'
        exit 0
        ;;
      *) _die 3 "unknown argument: $1 (use --once or --watch)" ;;
    esac
    shift
  done

  if [ -z "$mode" ]; then
    _die 3 'no mode specified — use --once or --watch'
  fi

  _check_auth

  case "$mode" in
    once)  run_once ;;
    watch) run_watch ;;
  esac
}

main "$@"
