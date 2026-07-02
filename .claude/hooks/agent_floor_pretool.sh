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
# Live floor override (see agent_floor_stop.sh): a valid integer in this file wins.
if [ -r /tmp/gludd-floor-override ]; then
  _fov="$(cat /tmp/gludd-floor-override 2>/dev/null)"
  case "$_fov" in ''|*[!0-9]*) : ;; *) FLOOR="$_fov" ;; esac
fi
# Live ceiling override (parallel to the floor override): retune max subagents
# mid-session. TARGET clamped so it never exceeds the ceiling.
if [ -r /tmp/gludd-ceiling-override ]; then
  _cov="$(cat /tmp/gludd-ceiling-override 2>/dev/null)"
  case "$_cov" in ''|*[!0-9]*) : ;; *) CEILING="$_cov" ;; esac
fi
[ "$TARGET" -gt "$CEILING" ] && TARGET="$CEILING"

# RATE-LIMIT (rev 2026-06-24): this advisory fires on PreToolUse(*) AND
# PostToolUse(*) — twice per tool call — and with no cooldown a single model
# response with N tool calls floods context with 2N identical floor advisories
# (the exact hindrance the user flagged with "fix your multitasking code"). Emit at
# most once per COOLDOWN secs via a lock SHARED with agent_floor_posttool.sh, so the
# floor signal still surfaces periodically (plus the turn-start userprompt status and
# the turn-end Stop backstop) but never repeats every call. Repair, not disable: the
# real floor guarantee is the Stop hook, which is untouched. Checked BEFORE the
# liveness probe so it also saves the probe latency when suppressed.
_FLOOR_COOLDOWN="${GLUDD_FLOOR_ADVISORY_COOLDOWN:-60}"
_FLOOR_LOCK="${GLUDD_FLOOR_ADVISORY_LOCK:-/tmp/gludd-floor-advisory-lastemit}"
_now="$(date +%s 2>/dev/null || echo 0)"
_last="$(cat "$_FLOOR_LOCK" 2>/dev/null)"; case "$_last" in ''|*[!0-9]*) _last=0 ;; esac
if [ "$_now" -ne 0 ] && [ "$((_now - _last))" -lt "$_FLOOR_COOLDOWN" ]; then exit 0; fi

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
  msg="AGENT-FLOOR (advisory): ${live} live, below floor ${FLOOR} (band ${FLOOR}-${CEILING}). When convenient, dispatch up to ${deficit} disjoint Agent call(s) to refill toward ${TARGET}, then HOLD — while live stays inside the ${FLOOR}-${CEILING} band do NOT dispatch more; just continue your work. Use async Agent dispatches, NOT the Workflow tool (Workflows prompt + block the operator). This does not block the current tool."
  echo "$_now" > "$_FLOOR_LOCK" 2>/dev/null || true   # arm the shared cooldown only when we actually emit
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null
fi
exit 0
