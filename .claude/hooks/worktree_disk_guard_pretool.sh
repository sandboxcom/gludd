#!/usr/bin/env bash
# PreToolUse(Agent) — worktree venv cap + disk-free guard — 2026-06-22
#
# FIRES ONLY when the Agent dispatch has isolation == "worktree".
# Non-worktree dispatches and all other tools: exit 0 silently.
#
# DENIES when EITHER:
#   (a) worktree .venv count > SAFE_CAP (default 8, env GLUDD_WORKTREE_CAP)
#   (b) disk free < MIN_FREE_GB (default 5.0, env GLUDD_MIN_FREE_GB)
#
# Count venvs by globbing .claude/worktrees/*/.venv
# Get free space via shutil.disk_usage on the repo root.
#
# ENV OVERRIDES (test seams):
#   GLUDD_WORKTREE_CAP        — venv count cap (int, default 8)
#   GLUDD_MIN_FREE_GB         — minimum free disk GB (float, default 5.0)
#   GLUDD_DISK_FREE_OVERRIDE  — inject fake free_gb (skips df)
#   GLUDD_VENV_COUNT_OVERRIDE — inject fake venv count (skips glob)
#   GLUDD_REPO_DIR            — repo root (default /Users/shawnwilson/gludd)
#
# FAIL-OPEN: any error -> exit 0 (never block on hook bug).
# NEVER exit non-zero.

SAFE_CAP="${GLUDD_WORKTREE_CAP:-8}"
MIN_FREE_GB="${GLUDD_MIN_FREE_GB:-5.0}"
REPO_DIR="${GLUDD_REPO_DIR:-/Users/shawnwilson/gludd}"

input="$(cat 2>/dev/null || echo '{}')"

# Step 1: only act on worktree-isolated Agent calls
is_worktree="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    iso = (d.get("tool_input") or {}).get("isolation") or ""
    print("yes" if iso == "worktree" else "no")
except Exception:
    print("no")
' 2>/dev/null)"

[ "$is_worktree" = "yes" ] || exit 0

# Step 2: measure free disk and venv count (env overrides for tests)
if [ -n "${GLUDD_DISK_FREE_OVERRIDE}" ]; then
    free_gb="${GLUDD_DISK_FREE_OVERRIDE}"
    venv_count="${GLUDD_VENV_COUNT_OVERRIDE:-0}"
else
    read -r free_gb venv_count <<EOF2
$(python3 -c "
import shutil, pathlib
try:
    st = shutil.disk_usage('${REPO_DIR}')
    free_gb = round(st.free / (1024**3), 2)
except Exception:
    free_gb = 999.0

try:
    wt_root = pathlib.Path('${REPO_DIR}/.claude/worktrees')
    venv_count = sum(
        1 for d in wt_root.glob('*/.venv') if d.is_dir()
    ) if wt_root.is_dir() else 0
except Exception:
    venv_count = 0

print(free_gb, venv_count)
" 2>/dev/null || echo '999.0 0')
EOF2
fi

# Validate — fail open if non-numeric
case "$free_gb" in ''|*[!0-9.]*) exit 0 ;; esac
case "$venv_count" in ''|*[!0-9]*) exit 0 ;; esac

# Step 3: evaluate and emit deny if over cap or under min free
python3 - "$free_gb" "$venv_count" "$SAFE_CAP" "$MIN_FREE_GB" <<'PYEOF'
import sys, json

free_gb    = float(sys.argv[1])
venv_count = int(float(sys.argv[2]))
safe_cap   = int(sys.argv[3])
min_free   = float(sys.argv[4])

reasons = []

if venv_count > safe_cap:
    reasons.append(
        f"worktree venv count {venv_count} exceeds cap {safe_cap} "
        f"(each ~320MB; {venv_count * 320}MB total). "
        f"Clean with: make clean-worktree-venvs"
    )

if free_gb < min_free:
    reasons.append(
        f"disk free {free_gb:.1f}GB < minimum {min_free}GB required for a new worktree venv (~320MB). "
        f"Free space first: make clean-worktree-venvs or make clean-tmp"
    )

if not reasons:
    sys.exit(0)

combined = " | ".join(reasons)
full_reason = (
    f"worktree venv disk near cap — {combined}. "
    f"Use a non-isolated agent (omit isolation='worktree') or clean first."
)
out = {"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": full_reason,
}}
print(json.dumps(out))
PYEOF

exit 0
