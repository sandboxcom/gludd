#!/usr/bin/env bash
# PreToolUse(*) floor enforcer — prompts to RUN subagents BEFORE a main-thread
# tool executes when the live count is below the floor. Complements the
# PostToolUse enforcer (agent_floor_posttool.sh): PostToolUse catches a drain
# AFTER a tool ran; this catches it BEFORE the next main-thread tool runs, so the
# orchestrator is pushed to delegate-first rather than doing inline work under a
# breach. A hook cannot dispatch (only the model can) — this is a forcing SIGNAL.
# FAST + FAIL-OPEN: short probe; any error -> emit nothing, never wedge a tool.

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-10}"
CEILING="${CLAUDE_AGENT_CEILING:-12}"

live="$(cd /Users/shawnwilson/gludd 2>/dev/null && \
  FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.5}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-75}" \
  python3 scripts/agent_liveness.py --count 2>/dev/null)"

case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open (silent)
esac

# DEAD-BAND CONTROLLER (rev 2026-06-17): only nudge BELOW the floor; stay SILENT in
# the hold band [FLOOR, CEILING) so the orchestrator can do main-thread work in
# peace. This is an ADVISORY refill nudge — it does NOT block the current tool and
# is NOT a "drop everything" command. Refill INTO the band, then HOLD.
if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((TARGET - live)); [ "$deficit" -lt 1 ] && deficit=1
  msg="AGENT-FLOOR (advisory): ${live} live, below floor ${FLOOR} (band ${FLOOR}-${CEILING}). When convenient, dispatch up to ${deficit} disjoint Agent call(s) to refill toward ${TARGET}, then HOLD — while live stays inside the ${FLOOR}-${CEILING} band do NOT dispatch more; just continue your work. Prefer a Workflow for sustained parallel work (steady pool). This does not block the current tool."
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null
fi
exit 0
