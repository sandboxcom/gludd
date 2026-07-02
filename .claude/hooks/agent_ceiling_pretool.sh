#!/usr/bin/env bash
# PreToolUse(Agent) CEILING enforcer — prompts to NOT run more subagents when the
# live count is already at/over the ceiling. Fires right before an Agent dispatch,
# so over-provisioning (disk ENOSPC + API-overload pressure — see disk-discipline)
# is caught at the moment of dispatch, not only via the response-transform plugin.
# This is the "don't run" counterpart to the floor enforcers. FAST + FAIL-OPEN.

CEILING="${CLAUDE_AGENT_CEILING:-12}"
TARGET="${CLAUDE_AGENT_TARGET:-10}"
# LIVE CEILING OVERRIDE: like /tmp/gludd-floor-override, this lets the operator
# retune the max concurrent subagents mid-session without a restart. A valid
# integer wins over the env default. TARGET is clamped so it never exceeds the
# ceiling (an inverted band would produce nonsensical "drain toward N" advice).
if [ -r /tmp/gludd-ceiling-override ]; then
  _cov="$(cat /tmp/gludd-ceiling-override 2>/dev/null)"
  case "$_cov" in ''|*[!0-9]*) : ;; *) CEILING="$_cov" ;; esac
fi
[ "$TARGET" -gt "$CEILING" ] && TARGET="$CEILING"

live="$(cd /Users/shawnwilson/gludd 2>/dev/null && \
  FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.5}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-12}" \
  python3 scripts/agent_liveness.py --count 2>/dev/null)"

case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open (silent)
esac

if [ "$live" -ge "$CEILING" ]; then
  msg="AGENT-CEILING BREACH (pre-Agent): ${live} subagent(s) already streaming (ceiling ${CEILING}, target ${TARGET}). Do NOT dispatch more right now -- let the in-flight wave drain back toward ${TARGET} first (over-provisioning risks disk ENOSPC + API overload). Work the main thread's own step instead; the floor hooks will prompt again only if the count later dips below the floor."
  # WHY python3 json.dumps: guarantees well-formed JSON (msg may contain em-dashes,
  # parens, etc. that could break bare printf escaping). PreToolUse hooks use only
  # hookSpecificOutput -- not systemMessage -- to inject advisory context.
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null || true
fi
exit 0
