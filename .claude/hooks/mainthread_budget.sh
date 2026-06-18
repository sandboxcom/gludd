#!/usr/bin/env bash
# MAIN-THREAD DELEGATION BUDGET (rev 2026-06-17)
#
# THE GAP THIS CLOSES: the agent_floor_* hooks only look at the live-subagent
# COUNT. They never measure how much WORK the orchestrator is doing inline on the
# main thread. The repeated failure the user keeps catching is the main thread
# grinding through a long serial streak of Read/Edit/Write/Bash calls — diagnosing,
# editing, committing — WITHOUT delegating, which is exactly what drains the floor
# and stalls multitasking. A floor-count hook is silent during that streak because
# it only fires on drain/turn-end.
#
# This hook adds the missing signal: it counts CONSECUTIVE main-thread tool calls
# since the last delegation (Agent / Workflow / Task), and once the streak is long
# AND the floor is below target, it escalates a "delegate the rest NOW" message.
# Every Agent/Workflow dispatch RESETS the streak — so the moment the orchestrator
# delegates, the pressure releases. This operationalizes "keep processes off the
# main thread" as a measured forcing function, not just a count.
#
# It is ADVISORY (additionalContext), never a block — per the user's "use hooks so
# they aren't a hindrance". FAIL-OPEN on any error (exit 0, emit nothing).
#
# Wiring (settings.json):
#   PostToolUse  matcher "*"  -> increments on main-thread tools, resets on delegation
#   PreToolUse   matcher "*"  -> escalates before the next main-thread tool when over budget

set -o pipefail 2>/dev/null || true

STREAK_FILE="${GLUDD_MAINTHREAD_STREAK_FILE:-/tmp/gludd-mainthread-streak}"
THRESHOLD="${GLUDD_MAINTHREAD_THRESHOLD:-8}"   # inline calls in a row before we nag
TARGET="${CLAUDE_AGENT_TARGET:-10}"
REPO_DIR="/Users/shawnwilson/gludd"

input="$(cat 2>/dev/null || echo '{}')"

# Parse hook_event_name + tool_name from the hook JSON (python3 is infra, allowed
# inside a hook). Fall back to empty on any parse error -> fail open.
read -r EVENT TOOL <<EOF2
$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(" "); sys.exit(0)
ev = d.get("hook_event_name") or d.get("hookEventName") or ""
tn = d.get("tool_name") or d.get("toolName") or ""
print(ev, tn)
' 2>/dev/null)
EOF2

[ -z "$TOOL" ] && exit 0

# Tool classification.
case "$TOOL" in
  Agent|Workflow|Task|SendMessage) CLASS="delegate" ;;
  Read|Edit|Write|Bash|MultiEdit|NotebookEdit|Grep|Glob) CLASS="mainthread" ;;
  *) CLASS="other" ;;
esac

read_streak() {
  local v
  v="$(cat "$STREAK_FILE" 2>/dev/null)"
  case "$v" in ''|*[!0-9]*) echo 0 ;; *) echo "$v" ;; esac
}

case "$EVENT" in
  PostToolUse)
    if [ "$CLASS" = "delegate" ]; then
      echo 0 > "$STREAK_FILE" 2>/dev/null || true   # delegation releases the pressure
    elif [ "$CLASS" = "mainthread" ]; then
      cur="$(read_streak)"
      echo $((cur + 1)) > "$STREAK_FILE" 2>/dev/null || true
    fi
    exit 0
    ;;
  PreToolUse)
    [ "$CLASS" = "mainthread" ] || exit 0
    streak="$(read_streak)"
    [ "$streak" -ge "$THRESHOLD" ] || exit 0

    # Only nag when the floor is actually below target — inline work is fine when
    # a healthy pool is already running.
    live="$(cd "$REPO_DIR" 2>/dev/null && \
      FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.5}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-75}" \
      python3 scripts/agent_liveness.py --count 2>/dev/null)"
    case "$live" in ''|*[!0-9]*) exit 0 ;; esac
    [ "$live" -lt "$TARGET" ] || exit 0

    # Re-arm to fire again after a few more inline calls (periodic, not every call).
    rearm=$((THRESHOLD - 3)); [ "$rearm" -lt 0 ] && rearm=0
    echo "$rearm" > "$STREAK_FILE" 2>/dev/null || true

    msg="MAIN-THREAD BUDGET: ${streak} main-thread tool calls in a row with no delegation, and only ${live} subagent(s) live (target ${TARGET}). THIS is the grind-inline pattern that drains multitasking. Hand the remaining chunk to an Agent or a Workflow NOW instead of doing it inline — that resets this budget. (If subagent dispatch is blocked by a rate-limit/quota, this is expected; resume delegating once it clears.)"
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
