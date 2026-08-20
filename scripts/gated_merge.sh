#!/usr/bin/env bash
# gated_merge.sh — flock-guarded multi-branch consolidation with conflict-abort,
# collection-gate, and per-ref manifest (stop/skip strategies).
#
# Env vars:
#   BASE            integration branch name (required)
#   BRANCHES        space-separated list of branch/commit refs (required)
#   MERGE_STRATEGY  stop-on-conflict (default) | skip-conflict
#   MANIFEST        path to write the output manifest file (required)
#   GIT_CMD         git binary override (default: git)
#   COLLECT_CMD     collection-check command (default: make test-count)

set -euo pipefail

BASE="${BASE:-}"
BRANCHES="${BRANCHES:-}"
MERGE_STRATEGY="${MERGE_STRATEGY:-stop-on-conflict}"
MANIFEST="${MANIFEST:-}"
GIT_CMD="${GIT_CMD:-git}"
COLLECT_CMD="${COLLECT_CMD:-make test-count}"

LOCK_FILE="${LOCK_FILE:-/tmp/gludd-gated-merge.lock}"

# ---------------------------------------------------------------------------
# Validate required inputs
# ---------------------------------------------------------------------------
if [ -z "$BASE" ]; then
    echo "[gated_merge] ERROR: BASE env var is required" >&2
    exit 1
fi

if [ -z "$BRANCHES" ]; then
    echo "[gated_merge] ERROR: BRANCHES env var is required" >&2
    exit 1
fi

if [ -z "$MANIFEST" ]; then
    echo "[gated_merge] ERROR: MANIFEST env var is required" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Acquire non-blocking flock on fd 9
# Use Python fcntl for portability (macOS lacks the 'flock' CLI command)
# ---------------------------------------------------------------------------
exec 9>"$LOCK_FILE"
cleanup() {
    exec 9>&-
}
trap cleanup EXIT

if ! python3 - 9 <<'PYEOF'
import fcntl, sys
fd = int(sys.argv[1])
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    sys.exit(0)
except (IOError, OSError):
    sys.exit(1)
PYEOF
then
    echo "[gated_merge] ERROR: could not acquire lock on $LOCK_FILE — another merge is running" >&2
    exit 1
fi

echo "[gated_merge] lock acquired: $LOCK_FILE"
echo "[gated_merge] BASE=$BASE MERGE_STRATEGY=$MERGE_STRATEGY"
echo "[gated_merge] BRANCHES: $BRANCHES"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Associative-array-like manifest: use arrays of parallel key/value.
MANIFEST_REFS=()
MANIFEST_STATUSES=()

record() {
    local ref="$1"
    local status="$2"
    MANIFEST_REFS+=("$ref")
    MANIFEST_STATUSES+=("$status")
    echo "[gated_merge] $ref -> $status"
}

write_manifest() {
    : > "$MANIFEST"
    local i=0
    while [ $i -lt ${#MANIFEST_REFS[@]} ]; do
        echo "${MANIFEST_REFS[$i]} ${MANIFEST_STATUSES[$i]}" >> "$MANIFEST"
        i=$((i + 1))
    done
    # Append HEAD sha
    local head_sha
    head_sha=$($GIT_CMD rev-parse HEAD 2>/dev/null || echo "unknown")
    echo "HEAD $head_sha" >> "$MANIFEST"
    echo "[gated_merge] manifest written to $MANIFEST"
}

reset_to_base() {
    echo "[gated_merge] resetting to BASE=$BASE"
    $GIT_CMD reset --hard "$BASE"
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
FAILED=0
REMAINING_REFS=()

# Split BRANCHES into array
for ref in $BRANCHES; do
    REMAINING_REFS+=("$ref")
done

PROCESSED_COUNT=0
TOTAL=${#REMAINING_REFS[@]}

for ref in "${REMAINING_REFS[@]}"; do
    echo "[gated_merge] attempting merge: $ref"

    # Attempt merge
    if $GIT_CMD merge --no-ff "$ref"; then
        # Merge succeeded — run collection check
        echo "[gated_merge] merge OK for $ref — running collect: $COLLECT_CMD"
        if eval "$COLLECT_CMD"; then
            record "$ref" "MERGED"
            PROCESSED_COUNT=$((PROCESSED_COUNT + 1))
        else
            echo "[gated_merge] collection FAILED after merging $ref" >&2
            record "$ref" "FAIL"
            # Mark all remaining refs as SKIPPED
            SKIP_IDX=$((PROCESSED_COUNT + 1))
            while [ $SKIP_IDX -lt $TOTAL ]; do
                record "${REMAINING_REFS[$SKIP_IDX]}" "SKIPPED"
                SKIP_IDX=$((SKIP_IDX + 1))
            done
            reset_to_base
            FAILED=1
            break
        fi
    else
        # Merge failed — abort cleanly
        echo "[gated_merge] conflict on $ref — aborting" >&2
        $GIT_CMD merge --abort || true
        record "$ref" "CONFLICT"

        if [ "$MERGE_STRATEGY" = "stop-on-conflict" ]; then
            # Mark all remaining refs as SKIPPED
            SKIP_IDX=$((PROCESSED_COUNT + 1))
            while [ $SKIP_IDX -lt $TOTAL ]; do
                record "${REMAINING_REFS[$SKIP_IDX]}" "SKIPPED"
                SKIP_IDX=$((SKIP_IDX + 1))
            done
            reset_to_base
            # Write manifest before exiting
            write_manifest
            echo "[gated_merge] DONE (stopped on conflict)"
            exit 0
        else
            # skip-conflict: continue with next ref
            echo "[gated_merge] skip-conflict mode: continuing past $ref"
        fi
        PROCESSED_COUNT=$((PROCESSED_COUNT + 1))
    fi
done

write_manifest

if [ "$FAILED" -eq 1 ]; then
    echo "[gated_merge] DONE with ERRORS (collection failure)" >&2
    exit 1
fi

echo "[gated_merge] DONE — all refs processed"
exit 0
