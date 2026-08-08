# E2E Test Project — Agent Rules

## CRITICAL: 10-Agent Dispatch Floor (HARD)
Every dispatch wave MUST contain exactly 10 task/agent dispatches when pending work exists.
Never send fewer than 10 dispatches in a wave. The floor IS the ceiling.

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
- Exactly 10 subagents per wave, dispatched in ONE message
- Each subagent task = make taskN (trivial, sub-second operation)
- All subagents operate on disjoint files only
- No long-running tasks, no gate runs, no CI polling

## Depth Rules
- Layer 0 (orchestrator): dispatches exactly 10 agents, ingests results
- Layer 1 (agent/subagent): does exactly one trivial `make taskN` operation and returns
- Subagents MUST NOT dispatch further subagents

## Bash = Make Only
All bash commands MUST use `make <target>`. No bare commands, no pipes, no redirects.

## Completion
Work is done when ALL 18 TASKS.md items are checked `[x]`.
Then send exactly: "ALL DONE" and stop.
