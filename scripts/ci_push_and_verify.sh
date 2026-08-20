#!/usr/bin/env bash
set -euo pipefail

REPO="sandboxcom/gludd"
POLL_INTERVAL="${CI_POLL_SECS:-15}"
MAX_ITERATIONS="${CI_MAX_POLLS:-40}"
DRY_RUN="${CI_DRY_RUN:-0}"

SHA="${1:-}"

usage() {
    echo "Usage: $0 [SHA]"
    echo ""
    echo "  Push to sandboxcom, then poll CI until completion or timeout."
    echo "  Set CI_DRY_RUN=1 to skip the push (verify existing CI only)."
    echo "  Set CI_POLL_SECS to override poll interval (default 15)."
    echo "  Set CI_MAX_POLLS to override max iterations (default 40)."
    echo ""
    echo "Exit codes: 0=PASS, 1=FAIL, 2=TIMEOUT"
    exit 2
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

# ---- resolve SHA ----
if [ -z "$SHA" ]; then
    SHA="$(git rev-parse HEAD)" || {
        echo "[ci-push-and-verify] ERROR: could not resolve HEAD SHA" >&2
        exit 2
    }
fi
SHA_SHORT="$(echo "$SHA" | cut -c1-7)"

# ---- push (unless dry-run) ----
if [ "$DRY_RUN" = "0" ]; then
    echo "[ci-push-and-verify] pushing $SHA_SHORT to sandboxcom..."
    make git-push-sandboxcom || {
        echo "[ci-push-and-verify] ERROR: push failed" >&2
        exit 2
    }
    echo "[ci-push-and-verify] push OK"
else
    echo "[ci-push-and-verify] DRY RUN — skipping push, verifying existing CI on $SHA_SHORT"
fi

# ---- poll CI ----
echo "[ci-push-and-verify] polling CI for $SHA_SHORT (every ${POLL_INTERVAL}s, max ${MAX_ITERATIONS} iterations)..."

POLL_FILE="${POLL_FILE:-/tmp/gludd-ci-poll-${SHA_SHORT}.json}"
cleanup() {
    rm -f "$POLL_FILE"
}
trap cleanup EXIT

for i in $(seq 1 "$MAX_ITERATIONS"); do
    ts="$(date +%H:%M:%S)"

    run_json="$(gh run list --repo "$REPO" --commit "$SHA" --json conclusion,status,databaseId,headSha 2>/dev/null || echo '[]')"
    echo "$run_json" > "$POLL_FILE"

    run_status="$(echo "$run_json" | python3 -c "import sys,json; a=json.load(sys.stdin) or [{}]; d=a[0]; print(d.get('status') or 'unknown')" 2>/dev/null)"
    run_conclusion="$(echo "$run_json" | python3 -c "import sys,json; a=json.load(sys.stdin) or [{}]; d=a[0]; print((d.get('conclusion') or '') if d.get('status')=='completed' else '')" 2>/dev/null)"
    run_id="$(echo "$run_json" | python3 -c "import sys,json; a=json.load(sys.stdin) or [{}]; d=a[0]; print(d.get('databaseId') or '')" 2>/dev/null)"

    echo "[ci-push-and-verify] heartbeat #$i [$ts]: status=$run_status conclusion=${run_conclusion:-pending} run=${run_id:-?}"

    if [ "$run_status" = "completed" ]; then
        if [ "$run_conclusion" = "success" ]; then
            echo ""
            echo "=== CI PASS: $SHA_SHORT run $run_id ==="
            exit 0
        else
            echo ""
            echo "=== CI FAIL: $SHA_SHORT run $run_id conclusion=$run_conclusion ===" >&2
            echo ""
            echo "--- failed jobs ---"
            gh run view "$run_id" --repo "$REPO" --json jobs --jq '.jobs[] | select(.conclusion=="failure") | "  \(.name)"' 2>/dev/null || echo "  (could not list failed jobs)"
            echo ""
            echo "--- failed job logs (first 50 lines each) ---"
            gh run view "$run_id" --repo "$REPO" --log-failed 2>/dev/null | head -50 || echo "  (could not fetch failed logs)"
            exit 1
        fi
    fi

    sleep "$POLL_INTERVAL"
done

# ---- timeout ----
echo ""
echo "=== CI TIMEOUT: $SHA_SHORT after ${MAX_ITERATIONS} iterations (${POLL_INTERVAL}s each) ===" >&2
echo "Last known status: $(python3 -c "import sys,json; a=json.load(sys.stdin) or [{}]; d=a[0]; print(d.get('status') or 'unknown')" < "$POLL_FILE" 2>/dev/null || echo 'unknown')" >&2
echo "Last known conclusion: $(python3 -c "import sys,json; a=json.load(sys.stdin) or [{}]; d=a[0]; print(d.get('conclusion') or '(pending)')" < "$POLL_FILE" 2>/dev/null || echo '(unknown)')" >&2
exit 2
