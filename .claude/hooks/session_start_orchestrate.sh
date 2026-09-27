#!/usr/bin/env bash
# SessionStart hook: codifies the orchestration so it resumes AUTOMATICALLY on
# every Claude start/restart (requested 2026-06-17). A hook can only inject
# CONTEXT — it cannot call the Agent/Workflow tools itself — so this emits a
# standing instruction that the model acts on at the very first turn: refill the
# agent floor and (re)launch the orchestration workflow for any pending work.
# FAIL-OPEN: any error prints nothing and exits 0 so a hook bug never wedges
# session start.
set +e

FLOOR="${CLAUDE_AGENT_FLOOR:-0}"
TARGET="${CLAUDE_AGENT_TARGET:-3}"
REPO="/Users/shawnwilson/gludd"
# Live floor override (operator retunes the floor mid-session; see agent_floor_stop.sh).
if [ -r /tmp/gludd-floor-override ]; then
  _fov="$(cat /tmp/gludd-floor-override 2>/dev/null)"
  case "$_fov" in ''|*[!0-9]*) : ;; *) FLOOR="$_fov"; TARGET="$_fov" ;; esac
fi

live="$(cd "$REPO" 2>/dev/null && python3 scripts/agent_liveness.py --count 2>/dev/null)"
case "$live" in ''|*[!0-9]*) live="0";; esac

# Emit context as a hookSpecificOutput JSON object — plain-text stdout is NOT valid
# JSON and causes the harness to surface a "hook error" even on exit 0. We use
# python3 json.dumps so the orchestration text is always correctly escaped regardless
# of what characters appear in the variables (URLs, em-dashes, quotes, etc.).
# FAIL-OPEN: if python3 fails for any reason, emit nothing and exit 0 (safe).
if [ "$FLOOR" -eq 0 ]; then
  context="[orchestration auto-start] Session (re)started with no mandatory subagent floor. Own coherent work inline and delegate only independently useful, disjoint tasks. Never invent filler work to satisfy a count. The hard concurrency ceiling remains active. Commits are make-only; never run two gates at once."
else
  context="[orchestration auto-start] Session (re)started with an operator-configured subagent floor of ${FLOOR}; ${live} are currently live. Refill only with independently useful, disjoint tasks, up to the configured target ${TARGET}. Never invent filler work to satisfy the floor. Commits are make-only; never run two gates at once."
fi
python3 -c 'import json,sys; ctx=sys.argv[1]; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":ctx}}))' "$context" 2>/dev/null
exit 0
