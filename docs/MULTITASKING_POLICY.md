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

## ⚠️ KNOWN GAP: cross-process git locking does not work inside worktrees

**Verified 2026-07-14** — `src/general_ludd/git_automation/locking.py:120-131`
(`_git_dir()`) and `:267-280` (`git_repo_lock`). `_git_dir()` decides where to
place the cross-process lock file by checking
`os.path.isdir(repo_path + "/.git")`. **Inside a git worktree, `.git` is a
FILE** (it contains `gitdir: <main-repo>/.git/worktrees/<name>`), not a
directory, so `os.path.isdir` is `False`, `_git_dir()` returns `None`, and
`git_repo_lock` **silently skips the cross-process flock entirely**, falling
back to an in-process `threading.RLock` keyed on the worktree's own realpath.

Because gludd runs each worktree agent as a **separate OS process** (not a
thread), that in-process fallback lock provides **zero cross-process
protection**. Net effect: right now, **concurrent git operations issued by
separate worktree-agent processes against the same underlying repository are
completely unserialized** — nothing prevents two processes from interleaving
writes to the shared object database / ref store.

**Blast radius — be precise about what is and isn't affected:**
- **Fine, unaffected:** read-only git ops (status/diff/log/show), and any
  non-git file editing work happening in parallel across worktrees. These
  never touch the lock path and were never protected by it anyway.
- **At risk:** concurrent **MUTATING** git operations — commit, merge, tag,
  push, or any ref-writing operation — issued from more than one worktree
  agent PROCESS at the same time against this repo.

**This matters directly because of this repo's standing policy to run 5+
concurrent worktree agents (see "Floor / band" above).** A wide agent-worktree
wave is exactly the scenario where this gap can bite.

**Practical mitigation until the fix lands:**
1. Each agent committing inside its OWN worktree/branch (`make git-commit`
   scoped to that worktree) is low-risk in practice — different worktrees
   normally write to different branch refs — but is NOT currently protected
   by the intended lock if two agents' git commands happen to race on shared
   state (packed-refs, object database maintenance, etc).
2. **Never run `make agent-merge`, `make agent-merge-dev`, `git-tag-push`, or
   `make git-push-sandboxcom` from more than one place at a time.** These are
   the operations that mutate SHARED branch tips (`master`/`development`) and
   are the highest-risk case. Keep them serialized through the orchestrator on
   the main checkout, one at a time — this is already this repo's stated
   pattern ("the orchestrator merges from the main checkout... one integrator
   agent drains finished worktree commits"); the point of this caveat is that
   there is currently **no mechanical enforcement** backing that pattern —
   only discipline.
3. Do not dispatch two subagents in the same wave that both perform mutating
   git operations (merge/tag/push) against this repo, even if they target
   different branches.

**The fix** (not yet implemented): switch `_git_dir()` to
`git rev-parse --git-common-dir`, which correctly resolves to the shared
`.git` directory from inside any worktree. Specced in
`docs/design/NEXT_RELEASE_BETA2_SPEC.md`.

## Commit discipline

- Make-only: feature branches via `make commit-bootstrap` (no-gate) or the gated
  `make test-and-commit`. Never run two gates / pytest at once (basetemp stampede).
  The local full gate OOMs here — CI is the real gate.
