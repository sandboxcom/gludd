#!/usr/bin/env bash
# PreToolUse(Agent) disk-discipline guard — 2026-06-18
#
# Fires ONLY when an Agent call has isolation="worktree" (the expensive kind that
# creates a ~320MB .venv).  Silent on non-worktree agents (which is most of them).
#
# Two thresholds:
#   DANGER_GB  (default 2.5) — advisory warn: finishing this dispatch may exhaust disk.
#   HARD_FLOOR_GB (default 1.0) — block: ENOSPC is imminent; dispatching is unsafe.
#
# Also counts existing worktree venvs; if at/over WORKTREE_CAP (default 6), warns.
# (Each venv ≈ 320MB; 6 × 320MB = 1.92GB, the empirically-safe cap from the incident.)
#
# ENV OVERRIDES (for tests / CI):
#   GLUDD_DISK_DANGER_GB      — override DANGER_GB threshold
#   GLUDD_DISK_HARD_FLOOR_GB  — override HARD_FLOOR_GB threshold
#   GLUDD_WORKTREE_CAP        — override cap on venv count
#   GLUDD_DISK_FREE_OVERRIDE  — inject a fake free_gb value (skips df entirely)
#   GLUDD_VENV_COUNT_OVERRIDE — inject a fake venv count (skips glob entirely)
#
# FAIL-OPEN: any python3/df error -> exit 0 (silent), never block on a hook bug.
# CONTEXT-EFFICIENT: emits nothing on non-worktree agents or healthy-disk worktree calls.

DANGER_GB="${GLUDD_DISK_DANGER_GB:-2.5}"
HARD_FLOOR_GB="${GLUDD_DISK_HARD_FLOOR_GB:-1.0}"
WORKTREE_CAP="${GLUDD_WORKTREE_CAP:-6}"
REPO_DIR="${GLUDD_REPO_DIR:-/Users/shawnwilson/gludd}"

input="$(cat 2>/dev/null || echo '{}')"

# Step 1: is this a worktree-isolated agent call?  If not, exit silently.
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

# Step 2: measure free disk (GB) and worktree-venv count.
# Env overrides allow tests to inject fake values without real disk/fs changes.
if [ -n "${GLUDD_DISK_FREE_OVERRIDE}" ]; then
    free_gb="${GLUDD_DISK_FREE_OVERRIDE}"
    venv_count="${GLUDD_VENV_COUNT_OVERRIDE:-0}"
else
    read -r free_gb venv_count <<EOF2
$(python3 -c "
import os, shutil, pathlib
# Free disk on the repo volume
try:
    st = shutil.disk_usage('${REPO_DIR}')
    free_gb = round(st.free / (1024**3), 2)
except Exception:
    free_gb = 999.0  # unknown -> fail open

# Count existing worktree venvs (each = ~320MB when materialised)
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

# Validate — fail open if python3 returned non-numeric
case "$free_gb" in ''|*[!0-9.]*) exit 0 ;; esac
case "$venv_count" in ''|*[!0-9]*) exit 0 ;; esac

# Step 3: evaluate thresholds and emit the appropriate signal.
printf '%s' "$input" | python3 - "$free_gb" "$venv_count" "$DANGER_GB" "$HARD_FLOOR_GB" "$WORKTREE_CAP" <<'PYEOF'
import sys, json

# consume stdin (not used further but keeps the pipe clean)
try:
    sys.stdin.read()
except Exception:
    pass

free_gb    = float(sys.argv[1])
venv_count = int(float(sys.argv[2]))
danger_gb  = float(sys.argv[3])
hard_floor = float(sys.argv[4])
cap        = int(sys.argv[5])

msgs = []
block = False

if free_gb < hard_floor:
    block = True
    msgs.append(
        f"DISK CRITICAL ({free_gb:.1f}GB free < hard floor {hard_floor}GB): dispatching a "
        f"worktree agent would almost certainly cause ENOSPC, which DEADLOCKS every Bash call "
        f"(the harness can't write its output file). Run `make clean-worktree-venvs && make clean-tmp` "
        f"first (reclaims worktree .venvs ~320MB each + /tmp gate scratch). "
        f"This dispatch is BLOCKED until disk is freed."
    )
elif free_gb < danger_gb:
    msgs.append(
        f"DISK WARNING ({free_gb:.1f}GB free, danger zone < {danger_gb}GB): a worktree agent "
        f"creates a ~320MB .venv. Consider running `make clean-worktree-venvs` to reclaim space "
        f"from finished worktrees before dispatching more. Do NOT dispatch a large batch."
    )

if venv_count >= cap:
    msgs.append(
        f"WORKTREE-CAP WARNING: {venv_count} existing worktree .venvs found (cap={cap}, ~{venv_count*320}MB). "
        f"Run `make clean-worktree-venvs` after integrating finished worktrees before adding more. "
        f"Prefer non-isolated agents for read-only work — they share the main checkout's venv."
    )

if not msgs:
    sys.exit(0)  # healthy -> silent

combined = " | ".join(msgs)
if block:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": combined,
    }}
else:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": combined,
    }}
print(json.dumps(out))
PYEOF

exit 0
