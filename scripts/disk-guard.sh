#!/usr/bin/env bash
# Compatibility entry point for the canonical disk admission and cleanup owners.
# Usage: scripts/disk-guard.sh check|disk-check|guard|disk-guard [repository-root]
set -euo pipefail

if [[ "$#" -gt 2 ]]; then
  echo "Usage: scripts/disk-guard.sh check|disk-check|guard|disk-guard [repository-root]" >&2
  exit 2
fi

MODE="${1:-guard}"
case "$MODE" in
  check|disk-check|guard|disk-guard) ;;
  *) echo "unsupported disk guard mode: $MODE" >&2; exit 2 ;;
esac

# Old policy overrides are not silently mapped to the fixed canonical limits or
# to a different cleanup scope. The owning preflight accepts its own settings.
for option in GLUDD_DISK_THRESHOLD GLUDD_DISK_CLEANUP_LOG \
  DISK_GUARD_UV_LOCK_TIMEOUT DISK_GUARD_UV_MAX_SECONDS \
  DISK_GUARD_UV_HEARTBEAT_SECONDS; do
  if [[ -n "${!option+x}" ]]; then
    echo "$option is unsupported; use canonical disk-cleanup-preflight settings" >&2
    exit 2
  fi
done

GLUDD_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET_DIR="${2:-$GLUDD_ROOT}"
if ! TARGET_DIR="$(cd -- "$TARGET_DIR" && pwd -P)" || [[ "$TARGET_DIR" != "$GLUDD_ROOT" ]]; then
  echo "disk guard target must be the invoking repository root" >&2
  exit 2
fi

case "$MODE" in
  check|disk-check)
    exec "${SYSTEM_PYTHON:-python3}" "$GLUDD_ROOT/scripts/check_disk_usage.py"
    ;;
  guard|disk-guard)
    exec make --no-print-directory -C "$GLUDD_ROOT" disk-cleanup-preflight \
      SYSTEM_PYTHON="${SYSTEM_PYTHON:-python3}" \
      DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY="${DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY-0}" \
      DISK_CLEANUP_PREFLIGHT_DRY_RUN="${DISK_CLEANUP_PREFLIGHT_DRY_RUN-0}" \
      DISK_CLEANUP_RECEIPT_GRACE_SECONDS="${DISK_CLEANUP_RECEIPT_GRACE_SECONDS-1800}"
    ;;
esac
