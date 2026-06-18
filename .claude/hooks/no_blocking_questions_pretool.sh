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
# CONTRACT: PreToolUse deny via stdout JSON
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}
# + exit 0. NEVER exit 1 (a non-zero PreToolUse exit is a HOOK ERROR shown to the user —
# the disruptive failure mode documented in BUGS.md). Built with python3 json.dumps so the
# reason is always valid/escaped. FAIL-OPEN on any internal error (exit 0, no output =
# allow) so a hook bug can never wedge the session.
#
# CONTEXT-EFFICIENT: registered with matcher "AskUserQuestion" in settings.json, so it
# only runs (and only emits output) when a blocking question is actually attempted —
# zero context cost on every other tool call.

# Consume stdin (the tool input) so the pipe never breaks; content is not needed — every
# AskUserQuestion is denied.
_input="$(cat 2>/dev/null || true)"

reason="BLOCKING QUESTION DENIED — user standing directive: never interrupt work to ask. DEFAULT TO ACTION: choose the most reasonable option yourself, state in one line the assumption you are making, and PROCEED. Do NOT re-attempt the question. For a genuinely destructive/irreversible external action, state the plan + the risk and proceed (or note it and continue with the safe default) rather than blocking — the user will redirect you if needed. Keep moving."

python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null && exit 0

# python3 unavailable / failed -> fail OPEN (allow). Never wedge, never error.
exit 0
