#!/usr/bin/env bash
# PreToolUse(*): HARD-ENFORCE the subagent floor in code.
#
# THE BUG THIS FIXES: a hook cannot dispatch agents -- only the model can. The
# floor was therefore only enforced at Stop (turn-end). That let the orchestrator
# drain to 0 live MID-TURN by running long inline main-thread sequences
# (Read/Bash/Edit chains) with nothing forcing a refill until it tried to stop.
# Result: the floor the user asked for was repeatedly NOT maintained.
#
# THE FIX: gate inline work on a met floor. When live < FLOOR, BLOCK (deny)
# non-Agent tool calls so the only way forward is to dispatch Agent task(s)
# (which ARE always allowed) and refill. Inline grinding below floor becomes
# impossible; the floor is maintained continuously, not just at Stop.
#
# SAFETY -- this must NEVER wedge the session:
#  - Agent tool calls ALWAYS pass (that is the refill path) and reset the grace counter.
#  - GRACE: the first ${GRACE} consecutive sub-floor non-Agent calls pass, so a single
#    essential main-thread op (e.g. one git step) is never blocked; only SUSTAINED
#    inline grinding below floor is denied.
#  - QUOTA ESCAPE: if /tmp/gludd-quota-block exists and is fresh (<10min), allow --
#    dispatching is futile against a rate-limit/quota wall (the one sanctioned reason
#    to be below floor). Touch that file to signal it.
#  - FAIL-OPEN on any probe/parse error (exit 0 = allow). A hook bug can never wedge.

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
GRACE=3
STATE="${TMPDIR:-/tmp}/gludd-subfloor-count"
QUOTA="/tmp/gludd-quota-block"
REPO="/Users/shawnwilson/gludd"

input="$(cat 2>/dev/null || echo '{}')"
tool="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_name",""))' 2>/dev/null)"

# SUBAGENT EXEMPTION (FAIL-SAFE -- THE BUG FIX). This blocking hook is inherited
# by SUBAGENTS too, but a subagent has NO Agent tool to refill with, so blocking
# it is ALWAYS an unrecoverable wedge (and it can even wedge the main thread via
# the shared grace counter). Therefore enforce ONLY on the MAIN orchestrator
# session: block ONLY when this call's session_id POSITIVELY matches the main
# session id recorded at SessionStart (/tmp/gludd-main-session). In EVERY other
# case -- no record yet, empty id, or a different (subagent) session -- exit 0.
# Fails SAFE: until the main session is positively identified, this hook is a pure
# no-op and can never wedge anything.
sid="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
main_sid=""
[ -f /tmp/gludd-main-session ] && main_sid="$(cat /tmp/gludd-main-session 2>/dev/null)"
if [ -z "$main_sid" ] || [ -z "$sid" ] || [ "$sid" != "$main_sid" ]; then
  exit 0
fi

# Agent dispatches are always allowed -- they are how you refill. Reset grace.
if [ "$tool" = "Agent" ]; then
  rm -f "$STATE" 2>/dev/null
  exit 0
fi

# Quota/rate-limit wall -> allow (refill is futile; sanctioned exception).
if [ -f "$QUOTA" ]; then
  now="$(date +%s 2>/dev/null || echo 0)"
  mt="$(stat -f %m "$QUOTA" 2>/dev/null || stat -c %Y "$QUOTA" 2>/dev/null || echo 0)"
  [ $((now - mt)) -lt 600 ] && exit 0
fi

# Ground-truth live count (shared probe). Fail-open if undeterminable.
live="$(cd "$REPO" 2>/dev/null && python3 scripts/agent_liveness.py --count 2>/dev/null)"
case "$live" in
  ''|*[!0-9]*) exit 0 ;;
esac

# Floor met -> allow and clear grace.
if [ "$live" -ge "$FLOOR" ]; then
  rm -f "$STATE" 2>/dev/null
  exit 0
fi

# Below floor: count consecutive sub-floor non-Agent calls.
n=0
[ -f "$STATE" ] && n="$(cat "$STATE" 2>/dev/null || echo 0)"
case "$n" in *[!0-9]*) n=0 ;; esac
n=$((n + 1))
printf '%s' "$n" > "$STATE" 2>/dev/null

# Grace window: allow a few essential inline calls before clamping down.
if [ "$n" -le "$GRACE" ]; then
  exit 0
fi

# Sustained inline grinding below floor -> DENY this tool call.
reason="FLOOR ENFORCED IN CODE: only ${live} subagent(s) live (floor ${FLOOR}); this is inline call #${n} below floor. Dispatch Agent task(s) to refill to >=${FLOOR} NOW -- Agent calls are ALWAYS allowed and will reset this gate -- then retry this ${tool}. Read-only auditors/proposers count toward the floor. If a rate-limit/quota is genuinely blocking dispatch, that is the one sanctioned exception: signal it by creating /tmp/gludd-quota-block and this gate will stand down for 10 minutes."
python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null && exit 0

# python3 failed -> fail OPEN (never wedge, never error).
exit 0
