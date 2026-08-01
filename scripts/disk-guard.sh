#!/usr/bin/env bash
# Disk guard — ensures disk usage stays below THRESHOLD%.
# Usage:
#   scripts/disk-guard.sh guard    Check + clean if above threshold (default)
#   scripts/disk-guard.sh check    Check only, exit 1 if above threshold
set -euo pipefail

THRESHOLD="${GLUDD_DISK_THRESHOLD:-95}"
TARGET_DIR="${2:-/Users/shawnwilson/gludd}"
GLUDD_ROOT="/Users/shawnwilson/gludd"
CLEANUP_LOG="${GLUDD_DISK_CLEANUP_LOG:-/tmp/gludd-disk-cleanup.log}"
DISK_GUARD_UV_LOCK_TIMEOUT="${DISK_GUARD_UV_LOCK_TIMEOUT:-15}"
DISK_GUARD_UV_MAX_SECONDS="${DISK_GUARD_UV_MAX_SECONDS:-120}"
DISK_GUARD_UV_HEARTBEAT_SECONDS="${DISK_GUARD_UV_HEARTBEAT_SECONDS:-5}"
GLUDD_NODE_CACHE_DIRS=(
  "/tmp/gludd-npm-cache"
  "/tmp/gludd-npm-cache-public-v1"
)

get_usage_pct() {
  df -Pk "$TARGET_DIR" 2>/dev/null | awk 'END {gsub(/%/,""); print $5}' || echo "0"
}

active_project_validation() {
  pgrep -f "${GLUDD_ROOT}/.*(pytest|mypy|ruff)" >/dev/null 2>&1
}

clean() {
  echo "Cleaning pip cache..."
  pip3 cache purge 2>/dev/null && echo "  pip cache purged" || echo "  pip cache purge skipped (no pip3)"

  echo "Pruning unused uv cache entries..."
  if command -v uv >/dev/null 2>&1; then
    local uv_pid uv_elapsed=0 uv_rc=""
    UV_LOCK_TIMEOUT="$DISK_GUARD_UV_LOCK_TIMEOUT" uv cache prune &
    uv_pid=$!
    while kill -0 "$uv_pid" 2>/dev/null; do
      sleep "$DISK_GUARD_UV_HEARTBEAT_SECONDS"
      if ! kill -0 "$uv_pid" 2>/dev/null; then
        break
      fi
      uv_elapsed=$((uv_elapsed + DISK_GUARD_UV_HEARTBEAT_SECONDS))
      echo "UV_CACHE_PRUNE_HEARTBEAT pid=$uv_pid elapsed_s=$uv_elapsed max_s=$DISK_GUARD_UV_MAX_SECONDS"
      if [[ "$uv_elapsed" -ge "$DISK_GUARD_UV_MAX_SECONDS" ]]; then
        echo "UV_CACHE_PRUNE_TIMEOUT pid=$uv_pid elapsed_s=$uv_elapsed"
        kill "$uv_pid" 2>/dev/null || true
        wait "$uv_pid" 2>/dev/null || true
        uv_rc=124
        break
      fi
    done
    if [[ -z "$uv_rc" ]]; then
      if wait "$uv_pid"; then
        uv_rc=0
      else
        uv_rc=$?
      fi
    fi
    if [[ "$uv_rc" -eq 0 ]]; then
      echo "  uv cache pruned"
    else
      echo "  uv cache prune skipped rc=$uv_rc"
    fi
  else
    echo "  uv not found — skipping"
  fi

  echo "Cleaning pytest cache..."
  rm -rf "${GLUDD_ROOT}/.pytest_cache" 2>/dev/null && echo "  .pytest_cache removed" || true

  echo "Cleaning __pycache__ directories..."
  find "$GLUDD_ROOT" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null && echo "  __pycache__ dirs removed" || true

  echo "Cleaning .mypy_cache..."
  rm -rf "${GLUDD_ROOT}/.mypy_cache" 2>/dev/null && echo "  .mypy_cache removed" || true

  echo "Cleaning .ruff_cache..."
  rm -rf "${GLUDD_ROOT}/.ruff_cache" 2>/dev/null && echo "  .ruff_cache removed" || true

  echo "Cleaning namespaced Node download caches..."
  local cache_dir
  for cache_dir in "${GLUDD_NODE_CACHE_DIRS[@]}"; do
    if [[ -d "$cache_dir" ]]; then
      rm -rf -- "$cache_dir"
      echo "  removed $cache_dir"
    else
      echo "  absent $cache_dir"
    fi
  done

  echo "Preserving shared pytest and Gludd test roots; they may belong to active namespaced runs."
}

check_only() {
  local pct
  pct=$(get_usage_pct)
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") disk_usage_pct=$pct threshold=$THRESHOLD"

  if [[ "$pct" -ge "$THRESHOLD" ]]; then
    echo "DISK ABOVE THRESHOLD: ${pct}% >= ${THRESHOLD}%"
    return 1
  fi

  echo "DISK OK: ${pct}% < ${THRESHOLD}%"
  return 0
}

check_and_clean() {
  local pct
  pct=$(get_usage_pct)
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") disk_usage_pct=$pct threshold=$THRESHOLD"

  if [[ "$pct" -lt "$THRESHOLD" ]]; then
    echo "DISK OK: ${pct}% < ${THRESHOLD}%"
    return 0
  fi

  if active_project_validation; then
    echo "DISK_CLEANUP_DEFERRED active project pytest/mypy/ruff validation is running"
    return 1
  fi

  echo "DISK ABOVE ${THRESHOLD}% (${pct}%) — cleaning..." | tee -a "$CLEANUP_LOG"

  clean

  pct=$(get_usage_pct)
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") disk_usage_pct_after=$pct"

  if [[ "$pct" -ge "$THRESHOLD" ]]; then
    echo "FATAL: disk still above ${THRESHOLD}% after cleanup (${pct}%)" | tee -a "$CLEANUP_LOG"
    return 1
  fi

  echo "DISK CLEANUP SUCCESSFUL (now ${pct}%)" | tee -a "$CLEANUP_LOG"
  return 0
}

MODE="${1:-guard}"
case "$MODE" in
  check|disk-check)
    check_only
    ;;
  guard|disk-guard|*)
    check_and_clean
    ;;
esac
