# E2E Test Project — Agent Rules

## 10-Agent Dispatch Floor (HARD)
Every dispatch wave MUST contain exactly 10 task/agent dispatches when pending work exists.
Never send fewer than 10 dispatches in a wave. The floor IS the ceiling.

## Never-Stop Rule (HARD)
Never send a text-only response while TASKS.md has unchecked items.
Every response MUST include at least one tool call. No summaries, no status reports.

## Dispatch Wave Format
- Exactly 10 subagents per wave, dispatched in ONE message
- Each subagent task = simple, sub-second operation
- No long-running tasks, no gate runs, no CI polling
- All subagents operate on disjoint files only

## Subagent Task Constraints
- Complete in under 5 seconds
- File writes limited to 10 lines max
- Output tokens: <50 per subagent
- Subagents can dispatch 2 layers deep (agent→subagent→worker, 3x total nesting)
- Layer 3 workers do exactly ONE trivial operation and return

## Depth Rules
- Layer 0 (orchestrator): dispatches 10 agents, ingests results
- Layer 1 (agent): does one simple task OR dispatches 10 workers
- Layer 2 (worker): does exactly one trivial operation, returns immediately
- Max nesting: 3 layers (orchestrator→agent→worker)
- Workers MUST NOT dispatch further subagents

## Bash = Make Only
All bash commands MUST use `make <target>`. No bare commands, no pipes, no redirects.

## Completion
Work is done when ALL 10 TASKS.md items are checked `[x]`.
Then send exactly: "ALL DONE" and stop.
