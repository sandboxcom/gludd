#!/usr/bin/env bash
# PreToolUse: DENY any tool/operation that presents a BLOCKING operator prompt
# and halts autonomous work until the operator answers.
#
# WHY THIS EXISTS (user standing directive 2026-07-02): "ensure that you are
# unable to run tools or other operations that present a user prompt and stop
# work until that prompt is answered." A passive rule did not prevent relapse;
# per the meta-rule "hooks force behavior, memories don't", this is the CODE
# gate. The AskUserQuestion blocker is enforced separately by
# no_blocking_questions_pretool.sh; this hook covers the OTHER blocking-prompt
# operations — chiefly the Workflow tool, which surfaces a permission prompt
# that BLOCKS the operator (CLAUDE.md: forbidden here; use async Agent instead).
#
# CONTRACT: PreToolUse deny via stdout JSON + exit 0. NEVER exit non-zero (a
# non-zero PreToolUse exit is a HOOK ERROR shown to the user). Built with
# python3 json.dumps so the reason is always valid/escaped. FAIL-OPEN on any
# internal error (exit 0, no output = allow) so a hook bug can never wedge the
# session.
#
# CONTEXT-EFFICIENT: registered in settings.json with matchers ONLY for the
# blocking-prompt tools, so it runs (and emits) only when one is attempted —
# zero context cost on every other tool call. To gate a newly-discovered
# blocking tool, add its name both to a matcher in settings.json AND (optional)
# to the case below for a tailored reason.

_LOG="${GLUDD_BLOCKING_Q_LOG:-/tmp/gludd-blocking-questions.log}"
_ts="$(date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo '?')"

_input="$(cat 2>/dev/null || true)"
# Extract the tool name from the PreToolUse payload (best-effort; the matcher
# already scoped us to blocking tools, so an empty parse still denies).
tool="$(printf '%s' "$_input" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tool_name",""))
except Exception:
    print("")' 2>/dev/null || true)"

case "$tool" in
  Workflow|workflow)
    reason="WORKFLOW DENIED — the Workflow tool surfaces a permission prompt that BLOCKS the operator and stops all work until answered (CLAUDE.md: Workflows are forbidden here for exactly this reason). Do NOT use it. Decompose the task into DISJOINT async Agent dispatches instead — they run in the background and never block the operator. Dispatch those Agents and proceed."
    ;;
  *)
    reason="BLOCKING-PROMPT OPERATION DENIED (${tool:-unknown}) — this presents a user prompt and halts work until answered, which violates the standing no-block directive. DEFAULT TO ACTION: pick the most reasonable path, state your assumption in one line, and proceed autonomously (or delegate to async Agents). Never run an operator-blocking operation to make a decision you can make yourself."
    ;;
esac

# Primary: python3 json.dumps (guaranteed-valid escaping of the reason).
if _out="$(python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null)" && [ -n "$_out" ]; then
  printf '%s\n' "$_out"
  echo "${_ts} DENY(json) ${tool:-unknown}" >> "$_LOG" 2>/dev/null || true
  exit 0
fi

# FAIL-CLOSED fallback: hand-built deny JSON so the blocking tool is still denied
# even without python3 — never fall through to allow.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKING-PROMPT OPERATION DENIED - this halts work waiting on the operator; default to action or async Agent dispatch and proceed."}}'
echo "${_ts} DENY(fallback) ${tool:-unknown}" >> "$_LOG" 2>/dev/null || true
exit 0
