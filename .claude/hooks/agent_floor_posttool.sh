#!/usr/bin/env bash
# PostToolUse(*) MID-TURN floor enforcer (#79/#78).
#
# THE GAP THIS CLOSES: the Stop hook (agent_floor_stop.sh) only fires at turn-END.
# While the orchestrator is mid-turn — churning through a flood of task-
# notifications, reading agent results, applying edits — a wave of subagents can
# all complete and drain the live count to zero, and NOTHING forces a refill until
# the orchestrator finally tries to stop. That is exactly the repeated floor-breach
# the user kept catching. This hook runs after EVERY tool call and, when the live
# subagent count is below the floor, injects a forcing directive into the model's
# context so the breach is caught at the moment it happens, not at turn-end.
#
# A hook CANNOT call the Agent tool itself — only the model can dispatch — so this
# is a forcing SIGNAL, not an auto-dispatcher. It pairs with the model's
# dispatch-first discipline and the Stop-hook backstop.
#
# FAST + FAIL-OPEN: uses a short probe (0.6s) with a short recent-write tail (4s)
# so a JUST-completed agent (frozen transcript) is not miscounted as live, while
# keeping per-tool latency low. Any error -> emit nothing, never wedge/slow a turn.

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-10}"
CEILING="${CLAUDE_AGENT_CEILING:-12}"

live="$(cd /Users/shawnwilson/gludd 2>/dev/null && \
  FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.6}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-75}" \
  python3 scripts/agent_liveness.py --count 2>/dev/null)"

case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open (silent)
esac

# DEAD-BAND CONTROLLER (rev 2026-06-17): advisory only, fires BELOW the floor and is
# SILENT in the hold band [FLOOR, CEILING). It does NOT block further main-thread
# work — the real floor guarantee is the Stop hook at turn-end. The lesson behind
# this rev: heavy MAIN-THREAD work is what drains the floor, so the fix is to
# DELEGATE (prefer a Workflow for sustained parallel work), not to nag harder.
if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((TARGET - live)); [ "$deficit" -lt 1 ] && deficit=1
  msg="AGENT-FLOOR (advisory): ${live} live, below floor ${FLOOR} (band ${FLOOR}-${CEILING}). A drain happened. Consider delegating the next chunk of work to ${deficit} disjoint agent(s) / a Workflow to refill toward ${TARGET}, then HOLD inside the band. Doing this work inline on the main thread is what drains the floor — delegate it instead. Advisory, not a block."
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null
fi
exit 0
