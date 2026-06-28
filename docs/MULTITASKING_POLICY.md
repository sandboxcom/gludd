# Multitasking / Subagent Orchestration Policy

Codified 2026-06-24. How the orchestrator maintains a steady pool of subagents
doing real project work in this repo. Companion to `.claude/hooks/*` (enforcement)
and `.opencode/plugin/*.ts` (mirror).

## Mechanism (what actually works)

- **Use the async `Agent` tool, NOT the `Workflow` tool.** Workflows surface a
  permission prompt that BLOCKS until the operator responds — the opposite of
  steady-state. Async `Agent` dispatches run silently in the background.
- **Maintain the pool by re-dispatching every turn.** Subagents complete and the
  pool drains; the discipline is to launch a fresh batch (and refill toward the
  floor) in each response while real disjoint work remains. The Stop hooks
  (`agent_floor_stop.sh` with `GLUDD_FLOOR_ENFORCE=1`, `no_wait_stop.sh` with
  `GLUDD_NO_WAIT_ENFORCE=1`) enforce this at turn-end.
- **The live count must be trustworthy.** `scripts/agent_liveness.py` is the
  ground-truth counter. It was fixed 2026-06-24 (see
  `memory/gludd-multitasking-hooks-fix.md`): terminal-detection now matches the
  real `.output`/`agent-*.jsonl` format (an `assistant` message with no pending
  `tool_use` = done), and plain-text bash background `.output` files are excluded.
  Verify any time with `make liveness-debug`.

## Floor / band

- `CLAUDE_AGENT_FLOOR=10`, `CLAUDE_AGENT_CEILING=16` (`.claude/settings.json`).
  Refill toward the band when below floor; HOLD inside it; let drain above ceiling.
- A hard steady-N needs N disjoint VALUABLE tasks at every instant. When the real
  backlog can't sustain N, SAY SO and either lower `CLAUDE_AGENT_FLOOR` or accept
  trough-dips — do NOT manufacture filler (that produced this repo's stale-branch
  and stale-worktree sprawl).

## Implementing vs. exploratory

- The pool must do **implementation**, not only audit/exploration. Deploy
  subagents that write code + tests and apply fixes — not just read-only proposers.
- Read-only proposers/auditors are fine for discovery, but every discovery wave
  should be followed by an **implementation wave** that lands the fixes.

## Isolation: worktree vs. non-isolated

- `isolation: "worktree"` gives each agent its own checkout — required when agents
  MUTATE files in parallel and would conflict, or to keep work off the main tree
  during a gate. BUT each worktree builds a ~320MB `.venv`; the disk guard caps
  concurrent worktrees (~6) and counts stale worktree DIRS (not just venvs).
  `make clean-worktree-venvs` clears venvs only; prune stale worktree dirs too.
- **Non-isolated** agents share the main checkout's venv (no disk blowup). Use them
  for read-only work, and for parallel implementation when each agent owns
  DISTINCT files (no git races) — but they all sit on one branch, so they must NOT
  commit individually; the orchestrator commits centrally.

## Commit discipline

- Make-only: feature branches via `make commit-bootstrap` (no-gate) or the gated
  `make test-and-commit`. Never run two gates / pytest at once (basetemp stampede).
  The local full gate OOMs here — CI is the real gate.
