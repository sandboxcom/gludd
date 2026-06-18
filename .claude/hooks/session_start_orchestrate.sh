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
TARGET="${CLAUDE_AGENT_TARGET:-10}"
REPO="/Users/shawnwilson/gludd"

live="$(cd "$REPO" 2>/dev/null && python3 scripts/agent_liveness.py --count 2>/dev/null)"
case "$live" in ''|*[!0-9]*) live="0";; esac

# Emit context as a hookSpecificOutput JSON object — plain-text stdout is NOT valid
# JSON and causes the harness to surface a "hook error" even on exit 0. We use
# python3 json.dumps so the orchestration text is always correctly escaped regardless
# of what characters appear in the variables (URLs, em-dashes, quotes, etc.).
# FAIL-OPEN: if python3 fails for any reason, emit nothing and exit 0 (safe).
context="[orchestration auto-start] Session (re)started. Standing orchestration policy is ACTIVE: 1. AGENT FLOOR: maintain ${FLOOR}-${TARGET} concurrent subagents on disjoint work. Currently ${live} live -- if below ${FLOOR}, dispatch toward ${TARGET} now (read-only proposers / commit-bootstrap writers; worktree only for concurrent mutation). 2. WORKFLOW: for any multi-step batch (backlog drain, feature build), prefer the deterministic Workflow tool over manual bursts -- it holds the pool steady. Saved workflows live in ${REPO}/.claude/workflows/ (e.g. ansible_ai_promise.js -> name 'ansible-ai-promise-feature'). Re-launch via Workflow({scriptPath:'.claude/workflows/<file>.js'}) or {name:'<name>'}. 3. PENDING WORK: verified-but-unmerged feature branches await a consolidated gated merge (see memory gludd-gated-merge-workflow); the security backlog is in docs/audit/NEW_FINDINGS_2026-06-16.md. Resume both. 4. COMMITS are make-only: feature branches use 'make commit-bootstrap' (no-gate); never make ship/gate mid-fleet (basetemp stampede). Act on item 1 before ending the first turn."
python3 -c 'import json,sys; ctx=sys.argv[1]; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":ctx}}))' "$context" 2>/dev/null
exit 0
