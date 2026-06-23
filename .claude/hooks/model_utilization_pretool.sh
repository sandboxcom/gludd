#!/usr/bin/env bash
# PreToolUse(Agent) model-utilization ENFORCER — 2026-06-19
#
# GOAL: hold sonnet:non-sonnet dispatch ratio at or above a configured target
# (default 10:1, i.e. target_share=0.91).
#
# MODES:
#   ENFORCING (new, primary):
#     Sonnet dispatches: ALWAYS allowed; recorded; exit 0.
#     Non-sonnet dispatches: compute projected share after allowing this call.
#       - Projected share >= target  → ALLOW (opus/haiku headroom exists)
#       - Projected share <  target  → DENY  via permissionDecision=deny
#     Grace: if rolling window has < 3 entries before this dispatch, ALLOW.
#
#   Advisory fallback (applies when config file absent / expired / unreadable):
#     Default 10%-band advisory nudge (same as the prior advisory-only behaviour).
#
# CONFIG FILE (.claude/sonnet_ratio_target):
#   {"target_share": 0.91, "until_epoch": <unix-timestamp>}
#   "until_epoch" is ignored for enforcement (enforcement is always on when the
#   file exists and is parseable).  "target_share" sets the floor (default 0.91).
#
# ENV OVERRIDES (for tests / CI):
#   GLUDD_MODEL_UTIL_STATE     — override state file path
#   GLUDD_MODEL_UTIL_WINDOW    — override window size (default 20)
#   GLUDD_SONNET_TARGET_CONFIG — override config file path
#   GLUDD_SONNET_TARGET_SHARE  — float override for target_share (beats config file)
#   GLUDD_MODEL_UTIL_ENFORCE   — set to "0" to disable enforcement (advisory only)
#
# Activate with: make set-sonnet-target HOURS=24 SHARE=0.91
#
# FAIL-OPEN: any error → exit 0, no output.  NEVER exit non-zero.
# Sonnet dispatches NEVER blocked.

STATE_FILE="${GLUDD_MODEL_UTIL_STATE:-/tmp/gludd-model-util.json}"
WINDOW="${GLUDD_MODEL_UTIL_WINDOW:-20}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_CONFIG="${GLUDD_SONNET_TARGET_CONFIG:-$REPO_ROOT/.claude/sonnet_ratio_target}"

# Read stdin once; fail open if unavailable.
input="$(cat 2>/dev/null || echo '{}')"

# Extract the model field from tool_input.  An ABSENT/empty model is NOT sonnet:
# omitting `model` makes the subagent INHERIT THE PARENT MODEL (opus in this
# session), so it must be gated like any other non-sonnet dispatch. Only a
# genuine parse failure fails open (→ "sonnet", allowed).
model="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    m = (d.get("tool_input") or {}).get("model") or ""
    print(m.strip())   # absent/empty -> "" -> treated non-sonnet (inherited opus)
except Exception:
    print("sonnet")   # genuine parse failure -> fail open
' 2>/dev/null || echo 'sonnet')"

# All logic in one python3 call to avoid partial-state risk.
python3 - "$STATE_FILE" "$WINDOW" "$model" "$TARGET_CONFIG" \
         "${GLUDD_SONNET_TARGET_SHARE:-}" \
         "${GLUDD_MODEL_UTIL_ENFORCE:-1}" <<'PYEOF' 2>/dev/null
import sys, json, os
from datetime import datetime

state_file  = sys.argv[1]
window      = int(sys.argv[2])
model       = sys.argv[3]
target_cfg  = sys.argv[4]
env_share   = sys.argv[5]   # empty string if env var not set
enforce_env = sys.argv[6]   # "0" disables enforcement

DEFAULT_TARGET = 0.91   # 10:1 sonnet:non-sonnet
GRACE_MIN      = 3      # entries needed before enforcement kicks in

# --- Only an EXPLICIT model:"sonnet" counts as sonnet. An empty/absent model
# inherits the parent (opus) and is therefore NON-sonnet — it must be gated so
# the operator is forced to set model:"sonnet" explicitly to earn headroom. ---
is_sonnet = (model == "sonnet")

# --- Load existing window (BEFORE appending current dispatch) ---
try:
    with open(state_file) as fh:
        data = json.load(fh)
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
except Exception:
    history = []

pre_count = len(history)   # window length BEFORE this dispatch

# --- Read target config ---
cfg_target = DEFAULT_TARGET
try:
    with open(target_cfg) as fh:
        cfg = json.load(fh)
    cfg_target = float(cfg.get("target_share", DEFAULT_TARGET))
except Exception:
    cfg_target = DEFAULT_TARGET

# env override beats config file
if env_share:
    try:
        cfg_target = float(env_share)
    except Exception:
        pass

target = cfg_target
enforce = (enforce_env != "0")

# --- Main-model detection (mirrors enforce-delegate.ts detectMainModel) ---
# When the parent/main thread is not an expensive (opus-class) model, there is
# no cost asymmetry to optimize and the harness may not expose model:"sonnet"
# on the Task tool — enforcement is skipped (record-only) so dispatch is never
# blocked by an unsatisfiable ratio.
def _detect_main_model():
    env_m = (os.environ.get("GLUDD_MAIN_MODEL") or os.environ.get("OPENCODE_MODEL") or "").strip().lower()
    if env_m:
        return env_m
    # Config file: explicit GLUDD_MAIN_MODEL_FILE REPLACES the default path
    # (either/or, not both) — so tests can isolate from the host's
    # .claude/main_model by pointing the env var at a nonexistent path.
    cfg_path = os.environ.get("GLUDD_MAIN_MODEL_FILE") or os.path.join(os.getcwd(), ".claude", "main_model")
    if cfg_path and os.path.isfile(cfg_path):
        try:
            with open(cfg_path) as fh:
                v = fh.read().strip().lower()
                if v:
                    return v
        except Exception:
            pass
    # opencode.json model field
    try:
        with open(os.path.join(os.getcwd(), "opencode.json")) as fh:
            cfg = json.load(fh)
            m = (cfg.get("model") or cfg.get("defaultModel") or "").strip().lower()
            if m:
                return m
    except Exception:
        pass
    return ""

_EXPENSIVE_MARKERS = ("opus", "claude-3-opus", "claude-opus", "o1", "o3", "gpt-4", "gpt-4o")

def _main_model_is_expensive():
    m = _detect_main_model()
    if not m:
        return True  # fail-safe: unknown -> preserve old enforcement
    return any(e in m for e in _EXPENSIVE_MARKERS)

main_expensive = _main_model_is_expensive()

# ----------------------------------------------------------------
# ENFORCEMENT GATE (non-sonnet dispatches only)
# ----------------------------------------------------------------
if not is_sonnet and enforce and main_expensive:
    # Grace: if we have fewer than GRACE_MIN samples, allow unconditionally.
    if pre_count < GRACE_MIN:
        # Still record and move on — no denial, no advisory.
        history.append(model)
        if len(history) > window:
            history = history[-window:]
        try:
            tmp = state_file + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"history": history}, fh)
            os.replace(tmp, state_file)
        except Exception:
            pass
        sys.exit(0)

    # Compute PROJECTED share if we ALLOW this non-sonnet dispatch.
    projected = history + [model]
    if len(projected) > window:
        projected = projected[-window:]
    proj_total  = len(projected)
    proj_sonnet = sum(1 for m in projected if m == "sonnet")
    proj_share  = proj_sonnet / proj_total if proj_total > 0 else 0.0

    if proj_share < target:
        # DENY — record the denial attempt but do NOT append to history
        # (the dispatch didn't happen; we record nothing so the ratio is intact).
        target_pct = round(target * 100)
        now_sonnet  = sum(1 for m in history if m == "sonnet")
        now_share   = now_sonnet / len(history) if history else 0.0
        now_pct     = round(now_share * 100)
        headroom_needed = round(target * (len(history) + 1)) - now_sonnet
        reason = (
            f"MODEL-RATIO ENFORCER: dispatching '{model}' would drop sonnet share "
            f"to {round(proj_share*100)}% (window={proj_total}, target={target_pct}%). "
            f"Current share {now_pct}% leaves no non-sonnet headroom right now. "
            f"Set model:'sonnet' EXPLICITLY (omitting model inherits the parent "
            f"model = opus, which counts as non-sonnet). "
            f"~{headroom_needed} more sonnet dispatch(es) will restore headroom."
        )
        out = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}
        print(json.dumps(out))
        sys.exit(0)
    # else: headroom exists → fall through to record + allow

# ----------------------------------------------------------------
# Record this dispatch (allowed sonnet OR allowed non-sonnet with headroom)
# ----------------------------------------------------------------
history.append(model)
if len(history) > window:
    history = history[-window:]

try:
    tmp = state_file + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"history": history}, fh)
    os.replace(tmp, state_file)
except Exception:
    pass  # fail open

# --- Compute shares for advisory/info output ---
total        = len(history)
sonnet_count = sum(1 for m in history if m == "sonnet")
non_sonnet   = total - sonnet_count
sonnet_share = sonnet_count / total if total > 0 else 1.0
non_share    = non_sonnet / total if total > 0 else 0.0

# ----------------------------------------------------------------
# Advisory nudge (only for allowed non-sonnet dispatches that are
# getting close to the ratio limit — helpful heads-up)
# ----------------------------------------------------------------
if not is_sonnet and main_expensive:
    # How much headroom remains?
    # How many more non-sonnet dispatches can we fit before hitting the ceiling?
    # projected share was already >= target (we're past the gate).
    # Emit an advisory if we're within 1 non-sonnet dispatch of the limit.
    next_projected = history + [model]
    if len(next_projected) > window:
        next_projected = next_projected[-window:]
    np_total  = len(next_projected)
    np_sonnet = sum(1 for m in next_projected if m == "sonnet")
    np_share  = np_sonnet / np_total if np_total > 0 else 1.0
    if np_share < target:
        target_pct  = round(target * 100)
        cur_pct     = round(sonnet_share * 100)
        msg = (
            f"MODEL-RATIO: non-sonnet headroom nearly spent "
            f"(sonnet {cur_pct}%, target {target_pct}%). "
            "The NEXT non-sonnet dispatch will be denied — use model:'sonnet'."
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
