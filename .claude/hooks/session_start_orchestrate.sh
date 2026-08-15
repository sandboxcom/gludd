#!/usr/bin/env bash
# SessionStart hook: codifies the orchestration so it resumes AUTOMATICALLY on
# every Claude start/restart (requested 2026-06-17). A hook can only inject
# CONTEXT — it cannot call the Agent/Workflow tools itself — so this emits a
# standing instruction that the model acts on at the very first turn: refill the
# agent floor and (re)launch the orchestration workflow for any pending work.
# FAIL-OPEN: any error prints nothing and exits 0 so a hook bug never wedges
# session start.
set +e

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-8}"
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
context="[orchestration auto-start] Session (re)started. STANDING MULTITASKING POLICY -- binding EVERY turn from open to close: (1) MAINTAIN THE AGENT FLOOR: keep at least ${FLOOR} concurrent async subagents alive AT ALL TIMES. Currently ${live} live. In EVERY response, if below ${FLOOR}, FIRST dispatch async Agent calls to refill BEFORE doing main-thread work. The pool is DECOUPLED from the current task -- do NOT let it drain when the active task goes sequential (gate/commit/edits/waiting). (2) THERE IS ALWAYS REAL WORK to hold the floor: the open security/audit backlog (docs/audit/), test-coverage gaps, perf + async + error-handling audits, adversarial review of in-flight changes, the codebase-audit findings. NEVER claim 'no work left' -- dispatch read-only auditors/reviewers/proposers on the standing backlog. (3) USE async Agent dispatches, NOT the Workflow tool: Workflows surface a permission prompt that BLOCKS the operator and stops work -- they are FORBIDDEN here. (4) The floor is live-tunable via /tmp/gludd-floor-override (currently ${FLOOR}). Commits are make-only; never run two gates at once. See docs/MULTITASKING_POLICY.md. ACT ON ITEM 1 NOW, before anything else."
python3 -c 'import json,sys; ctx=sys.argv[1]; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":ctx}}))' "$context" 2>/dev/null
exit 0
