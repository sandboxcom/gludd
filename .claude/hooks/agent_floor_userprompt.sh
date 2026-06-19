#!/usr/bin/env bash
# UserPromptSubmit floor/ceiling status — injects the live subagent count and the
# run / don't-run directive at the START of every user turn, so the orchestrator
# begins each turn already aware of the band (below floor -> run more; at/over
# ceiling -> let drain). Complements the mid-turn (PostToolUse), pre-tool
# (PreToolUse) and turn-end (Stop) enforcers with a turn-START prompt.
# FAIL-OPEN: any error -> emit nothing.

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-8}"
CEILING="${CLAUDE_AGENT_CEILING:-10}"

live="$(cd /Users/shawnwilson/gludd 2>/dev/null && \
  FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.6}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-12}" \
  python3 scripts/agent_liveness.py --count 2>/dev/null)"

case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open (silent)
esac

if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((TARGET - live)); [ "$deficit" -lt 1 ] && deficit=1
  msg="AGENT-FLOOR status (turn start): ${live} live, below floor ${FLOOR} (band ${FLOOR}-${CEILING}). DELEGATE-FIRST: hand the next chunk to ${deficit} disjoint agent(s) / a Workflow to refill toward ${TARGET} rather than grinding it out inline — heavy main-thread work is what drains the floor. Then HOLD inside the band (do not re-dispatch while ${FLOOR}<=live<${CEILING})."
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}\n' "$msg"
elif [ "$live" -ge "$CEILING" ]; then
  msg="AGENT-CEILING status (turn start): ${live} subagent(s) streaming (ceiling ${CEILING}, target ${TARGET}). Do NOT dispatch more this turn; let the in-flight wave drain back toward ${TARGET}."
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}\n' "$msg"
fi
exit 0
