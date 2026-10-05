# E2E Test Project — Agent Rules

## CRITICAL: Three-Agent Configured Width (HARD)
Every full dispatch wave MUST contain exactly three task/agent dispatches while at least three independent tasks remain.
Never exceed three dispatches in a wave. The final wave may be smaller.

## CRITICAL: Never-Stop Rule (HARD)
Never send a text-only response while TASKS.md has unchecked items.
Every response MUST include at least one tool call. No summaries, no status reports.

## CRITICAL: Low-Token Subagent Work Only
Subagents MUST do TRIVIAL low-token work only. Each subagent:
- Runs exactly one `make taskN` target (writes a 1-line file, sleeps 1s, counts to 10, etc.)
- Returns in under 5 seconds
- Uses <50 output tokens
- Does NOT generate code, write long files, or run heavy compute
- Does NOT dispatch further subagents (orchestrator handles all dispatch)

## Dispatch Wave Format
- Exactly three subagents per full wave, dispatched in one message
- Each subagent task = make taskN (trivial, sub-second operation)
- All subagents operate on disjoint files only
- No long-running tasks, no gate runs, no CI polling

## Depth Rules
- Layer 0 (orchestrator): dispatches up to three agents, ingests results
- Layer 1 (agent): does exactly one trivial `make taskN` operation and returns
- Layers 2-3 (sub-agents): MAY dispatch further subagents up to MAX_DEPTH=4
- 3x deep dispatch is ALLOWED: main -> agent -> agent -> agent (depths 0-3)
- 4x deep dispatch (depth=4) is BLOCKED by enforce-depth.ts

## Bash = Make Only
All bash commands MUST use `make <target>`. No bare commands, no pipes, no redirects.

## Completion
Work is done when ALL 18 TASKS.md items are checked `[x]`.
Then send exactly: "ALL DONE" and stop.
