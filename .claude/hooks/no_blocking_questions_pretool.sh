#!/usr/bin/env bash
# PreToolUse(AskUserQuestion): ENFORCE "never block on questions — default to action".
#
# WHY THIS EXISTS: the user has repeatedly directed "never pause work to ask; default
# to action" (memory gludd-never-block-on-questions). A passive memory did NOT stop the
# relapse — the orchestrator still fired AskUserQuestion mid-flow and interrupted work.
# Per the user's own meta-rule ("memories don't force behavior; hooks almost do"), this
# is the CODE enforcement: deny the AskUserQuestion tool so the model must decide for
# itself, state its assumption, and proceed.
#
# 2026-07-02 HARDENING (user report: "not always working"). Two real weaknesses fixed:
#   (1) NO LOG existed — there was no way to see if the hook fired or a question slipped
#       past, so "check the logs" was impossible. Now EVERY invocation appends a line to
#       $GLUDD_BLOCKING_Q_LOG (default /tmp/gludd-blocking-questions.log). If a question
#       ever reaches the operator, check that log: a MISSING entry ⇒ the hook didn't fire
#       (matcher/registration/subagent-scope issue); a PRESENT entry ⇒ it fired and the
#       deny wasn't honored (harness contract issue).
#   (2) FAIL-OPEN — the old code fell through to `exit 0` with no output (= ALLOW) if
#       python3 hiccuped. Now it FAILS CLOSED: a printf-built deny JSON is emitted as a
#       fallback so the tool is denied even without python3.
#
# CONTRACT: PreToolUse deny via stdout JSON
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}
# + exit 0. NEVER exit non-zero (a non-zero PreToolUse exit is a HOOK ERROR shown to the
# user — the disruptive failure mode documented in BUGS.md).
#
# CONTEXT-EFFICIENT: registered with matcher "AskUserQuestion" in settings.json, so it
# only runs when a blocking question is actually attempted — zero cost on other calls.

_LOG="${GLUDD_BLOCKING_Q_LOG:-/tmp/gludd-blocking-questions.log}"
_ts="$(date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo '?')"

# Consume stdin (the tool input) so the pipe never breaks; content is not needed — every
# AskUserQuestion is denied.
_input="$(cat 2>/dev/null || true)"

reason="BLOCKING QUESTION DENIED — user standing directive: never interrupt work to ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in one line the assumption you are making, and PROCEED. Do NOT re-attempt the question. For a genuinely destructive/irreversible external action, state the plan + the risk and proceed (or note it and continue with the safe default) rather than blocking — the user will redirect you if needed. Keep moving."

# Primary path: python3 json.dumps guarantees valid escaping of the reason.
if _out="$(python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null)" && [ -n "$_out" ]; then
  printf '%s\n' "$_out"
  echo "${_ts} DENY(json) AskUserQuestion" >> "$_LOG" 2>/dev/null || true
  exit 0
fi

# FAIL-CLOSED fallback: hand-built JSON (plain reason, no chars needing escaping) so the
# tool is still DENIED when python3 is unavailable — never fall through to allow.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKING QUESTION DENIED - never interrupt work to ask; default to action, state your assumption, and proceed."}}'
echo "${_ts} DENY(fallback) AskUserQuestion" >> "$_LOG" 2>/dev/null || true
exit 0
