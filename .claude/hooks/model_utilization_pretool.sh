#!/usr/bin/env bash
# PreToolUse(Agent) model-utilization nudge — 2026-06-18
#
# GOAL: maintain a sonnet-dominant dispatch ratio.  Supports two modes:
#
#   TIME-BOUND TARGET mode (active when .claude/sonnet_ratio_target exists and
#   its until_epoch is in the future):
#     If sonnet_share < target_share → emit time-bound advisory nudge.
#     Silent when at/above target.
#
#   DEFAULT 10%-BAND mode (fallback):
#     If sonnet_share < non_share - 0.10 → emit band advisory nudge.
#     Silent when sonnet is healthy.
#
# ENV OVERRIDES (for tests / CI):
#   GLUDD_MODEL_UTIL_STATE     — override state file path
#   GLUDD_MODEL_UTIL_WINDOW    — override window size (default 20)
#   GLUDD_SONNET_TARGET_CONFIG — override config file path
#   GLUDD_SONNET_TARGET_SHARE  — float override for target_share (beats config file)
#
# Config file format (.claude/sonnet_ratio_target):
#   {"target_share": 0.67, "until_epoch": <unix-timestamp>}
#
# Activate with: make set-sonnet-target HOURS=24 SHARE=0.67
#
# FAIL-OPEN: any error → exit 0, no output.  NEVER exit non-zero.

STATE_FILE="${GLUDD_MODEL_UTIL_STATE:-/tmp/gludd-model-util.json}"
WINDOW="${GLUDD_MODEL_UTIL_WINDOW:-20}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_CONFIG="${GLUDD_SONNET_TARGET_CONFIG:-$REPO_ROOT/.claude/sonnet_ratio_target}"

# Read stdin once; fail open if unavailable.
input="$(cat 2>/dev/null || echo '{}')"

# Extract the model field from tool_input.  Absent/empty/null → "sonnet".
model="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    m = (d.get("tool_input") or {}).get("model") or ""
    m = m.strip()
    print(m if m else "sonnet")
except Exception:
    print("sonnet")
' 2>/dev/null || echo 'sonnet')"

# All logic in one python3 call to avoid partial-state risk.
python3 - "$STATE_FILE" "$WINDOW" "$model" "$TARGET_CONFIG" "${GLUDD_SONNET_TARGET_SHARE:-}" <<'PYEOF' 2>/dev/null
import sys, json, os
from datetime import datetime

state_file = sys.argv[1]
window     = int(sys.argv[2])
model      = sys.argv[3]
target_cfg = sys.argv[4]
env_share  = sys.argv[5]  # empty string if env var not set

# --- load existing window ---
try:
    with open(state_file) as fh:
        data = json.load(fh)
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
except Exception:
    history = []

# --- append current dispatch (BEFORE computing) ---
history.append(model)

# --- trim to rolling window ---
if len(history) > window:
    history = history[-window:]

# --- persist updated state ---
try:
    tmp = state_file + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"history": history}, fh)
    os.replace(tmp, state_file)
except Exception:
    pass  # fail open: don't block if we can't write

# --- compute shares ---
total = len(history)
if total == 0:
    sys.exit(0)

sonnet_count = sum(1 for m in history if m == "sonnet")
non_sonnet   = total - sonnet_count
sonnet_share = sonnet_count / total
non_share    = non_sonnet  / total

# --- Try time-bound target mode ---
cfg_ok      = False
until_epoch = 0
cfg_target  = None

try:
    with open(target_cfg) as fh:
        cfg = json.load(fh)
    until_epoch = int(cfg.get("until_epoch", 0))
    cfg_target  = float(cfg.get("target_share", 0.67))
    cfg_ok = True
except Exception:
    cfg_ok = False

now = int(datetime.now().timestamp())

if cfg_ok and until_epoch > now:
    # Time-bound override is active — use env_share if provided, else cfg_target
    if env_share:
        try:
            target = float(env_share)
        except Exception:
            target = cfg_target
    else:
        target = cfg_target

    if sonnet_share >= target:
        sys.exit(0)  # healthy → silent

    sonnet_pct = round(sonnet_share * 100)
    target_pct = round(target * 100)
    until_str  = datetime.fromtimestamp(until_epoch).strftime('%Y-%m-%d %H:%M')
    msg = (
        f"MODEL-UTIL: sonnet is {sonnet_pct}% of recent dispatches; "
        f"target is {target_pct}% (2:1) until {until_str} — "
        "use model:'sonnet' for upcoming tasks unless one genuinely needs a stronger model."
    )
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }}
    print(json.dumps(out))
    sys.exit(0)

# --- Default: 10%-band mode ---
# Under-utilised: sonnet_share < non_share - 0.10
if sonnet_share >= non_share - 0.10:
    sys.exit(0)  # healthy → silent

sonnet_pct = round(sonnet_share * 100)
non_pct    = round(non_share    * 100)
msg = (
    f"MODEL-UTIL: sonnet is {sonnet_pct}% of recent dispatches "
    f"vs {non_pct}% non-sonnet (last {total} dispatches) — "
    "prefer model:'sonnet' for upcoming tasks "
    "(cost-efficient default; keep sonnet dominant unless a task "
    "genuinely needs a stronger model)."
)
out = {"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": msg,
}}
print(json.dumps(out))
sys.exit(0)
PYEOF

# Fail-open: if python3 produced a non-zero exit (unusual) we still exit 0.
exit 0
