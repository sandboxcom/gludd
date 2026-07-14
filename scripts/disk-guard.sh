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

get_usage_pct() {
  df -h "$TARGET_DIR" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}' || echo "0"
}

clean() {
  echo "Cleaning pip cache..."
  pip3 cache purge 2>/dev/null && echo "  pip cache purged" || echo "  pip cache purge skipped (no pip3)"

  echo "Cleaning uv cache..."
  if command -v uv &>/dev/null; then
    uv cache clean 2>/dev/null && echo "  uv cache cleaned" || echo "  uv cache clean skipped"
  else
    echo "  uv not found — skipping"
  fi

  echo "Cleaning pytest cache..."
  rm -rf "${GLUDD_ROOT}/.pytest_cache" 2>/dev/null && echo "  .pytest_cache removed" || true
  rm -rf /tmp/pytest-of-* 2>/dev/null && echo "  /tmp/pytest-of-* removed" || true
  rm -rf /private/tmp/pytest-of-* 2>/dev/null && echo "  /private/tmp/pytest-of-* removed" || true

  echo "Cleaning __pycache__ directories..."
  find "$GLUDD_ROOT" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null && echo "  __pycache__ dirs removed" || true

  echo "Cleaning .mypy_cache..."
  rm -rf "${GLUDD_ROOT}/.mypy_cache" 2>/dev/null && echo "  .mypy_cache removed" || true

  echo "Cleaning .ruff_cache..."
  rm -rf "${GLUDD_ROOT}/.ruff_cache" 2>/dev/null && echo "  .ruff_cache removed" || true

  echo "Cleaning gludd tmp files..."
  rm -rf /tmp/gludd-iso-* 2>/dev/null && echo "  /tmp/gludd-iso-* removed" || true
  rm -rf /tmp/gludd-gate-basetemp 2>/dev/null && echo "  gludd-gate-basetemp removed" || true
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
