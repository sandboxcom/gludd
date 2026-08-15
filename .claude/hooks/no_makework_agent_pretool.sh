#!/usr/bin/env bash
# PreToolUse(Agent): BLOCK make-work / thrash agent dispatches.
#
# WHY: the orchestrator was caught spawning agents that did NO real work --
# duplicate "standdown" / "keep-alive" / retry-the-same-call agents whose only
# purpose was to game the agent-floor count. That is forbidden. Agents must do
# DISTINCT, REAL work. Being below the floor is ACCEPTABLE; dispatching junk to
# fill it is NOT. This hook enforces that in code.
#
# It DENIES an Agent dispatch when EITHER:
#  (1) MAKE-WORK PATTERN: the prompt matches a known thrash/standdown/keep-alive/
#      floor-filler pattern, or
#  (2) DUPLICATE: the same (normalized) prompt was already dispatched within the
#      last 10 minutes -- the "multiples of the same subagent" anti-pattern.
#
# SAFE BY CONSTRUCTION: matcher is the Agent tool, which only the MAIN orchestrator
# can call (subagents have no Agent tool), so this can NEVER wedge a subagent's own
# work. FAIL-OPEN on any parse/runtime error (exit 0 = allow).

LOG="${TMPDIR:-/tmp}/gludd-agent-dispatch-log"
WINDOW=600

input="$(cat 2>/dev/null || echo '{}')"

prompt="$(printf '%s' "$input" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); ti=d.get("tool_input",{}) or {}
    print(((ti.get("prompt","") or "")+" "+(ti.get("description","") or "")).strip())
except Exception:
    print("")' 2>/dev/null)"

# Cannot read the prompt -> fail open (never block on uncertainty).
[ -z "$prompt" ] && exit 0

low="$(printf '%s' "$prompt" | tr '[:upper:]' '[:lower:]')"

emit_deny() {
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$1" 2>/dev/null
  exit 0
}

# (1) MAKE-WORK / THRASH PATTERNS -> deny.
case "$low" in
  *standdown*|*"stay alive"*|*"keep alive"*|*"keep-alive"*|*"retry the same"*|*"do nothing else"*|*"gludd-quota-block"*|*"refill the floor"*|*"emergency single action"*|*"emergency."*|*"to reach the floor"*|*"keep the count"*|*"satisfy the floor"*|*"fill the floor"*|*"just to keep"*)
    emit_deny "DISPATCH BLOCKED (anti-make-work): this agent prompt matches a thrash/standdown/keep-alive/floor-filler pattern. Agents MUST do distinct, REAL work. Being below the floor is acceptable; spawning junk to fill it is not. Do the actual work, or stay below floor."
    ;;
esac

# (2) DUPLICATE detection: fingerprint = sha1 of whitespace-normalized prompt.
fp="$(printf '%s' "$low" | tr -s '[:space:]' ' ' | python3 -c 'import sys,hashlib; print(hashlib.sha1(sys.stdin.read().encode()).hexdigest())' 2>/dev/null)"
[ -z "$fp" ] && exit 0

now="$(date +%s 2>/dev/null || echo 0)"

if [ -f "$LOG" ]; then
  dup="$(python3 - "$LOG" "$fp" "$now" "$WINDOW" <<'PY' 2>/dev/null
import sys
log, fp, now, win = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
try:
    for line in open(log):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == fp and (now - int(parts[0])) < win:
            print("DUP"); break
except Exception:
    pass
PY
)"
  if [ "$dup" = "DUP" ]; then
    emit_deny "DISPATCH BLOCKED (anti-make-work): this prompt is a DUPLICATE of one dispatched in the last 10 minutes. Do not spawn multiple identical agents. Give each agent a DISTINCT task, or do the work once."
  fi
fi

# Record this (real, distinct) dispatch. Append is atomic for short lines.
printf '%s %s\n' "$now" "$fp" >> "$LOG" 2>/dev/null
exit 0
