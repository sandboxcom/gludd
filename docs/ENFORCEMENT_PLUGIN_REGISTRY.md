# Enforcement Plugin Registry

> **Source of truth:** `opencode.json` `plugin` array. Every active plugin is
> documented here. Operators use this file to understand what each plugin
> blocks and how to disable a misbehaving guardrail without disabling all of
> them.
>
> **Verify currency:** `make test-specific TESTFILE='tests/unit/test_enforcement_registry'`
> fails if any plugin in `opencode.json` is missing from this document.

## Total: 31 active plugins

| # | Plugin | Hook(s) | What it blocks | Disable env var |
|---|--------|---------|----------------|-----------------|
| 1 | `enforce-session-start.ts` | `tool.execute.before`, `experimental.chat.system.transform` | Edit/write/bash (and other non-read, non-dispatch tools) until the session has made ≥10 parallel task/agent/workflow dispatches AND read TASKS.md/BUGS.md/ratchet.yml/SESSION.md. Hard-deny after `120s` with 0 dispatches. | `GLUDD_SESSION_START_ENFORCE=0` |
| 2 | `enforce-make.ts` | `tool.execute.before`, `experimental.text.complete`, `experimental.chat.system.transform`, `session.idle` | Non-`make` bash commands AND any bash command containing shell metacharacters (`\|`, `;`, `&&`, `\|\|`, `$()`, backticks, `>`, `<`, `2>&1`, `{}`, `!`, `\\`). Injects the bash policy + 4-step diagnosis into the system prompt. | `GLUDD_MAKE_ENFORCE=0` |
| 3 | `enforce-floor.ts` | `tool.execute.before`, `experimental.text.complete` | All non-dispatch tools (incl. read/grep/glob) after 5 consecutive non-dispatch calls in a 30s window (grinding block), OR when the live subagent count drops below the floor. | `GLUDD_FLOOR_ENFORCE=0` |
| 4 | `enforce-delegate.ts` | `tool.execute.before` | Edit/write/bash after 2 consecutive non-dispatch calls (mainthread streak); serial read-only investigations past `GLUDD_READ_GRIND_DENY_COUNT` (default 10). | `GLUDD_MAINTHREAD_STREAK_ENFORCE=0` (streak), `GLUDD_MODEL_UTIL_ENFORCE=0` (utilization), `GLUDD_READ_GRIND_*` tunables |
| 5 | `enforce-multitask.ts` | `tool.execute.before` | Task/agent/workflow dispatch waves containing fewer than 10 parallel dispatches when pending work exists (unchecked TASKS.md or ratchet entries); also blocks zero-dispatch streaks. | `GLUDD_MULTITASK_FLOOR_ENFORCE=0` |
| 6 | `enforce-stop.ts` | `experimental.text.complete`, `tool.execute.before`, `session.idle` | Text-only responses (0 tool calls) when `hasRealPendingWork()` is true (unchecked TASKS.md items, non-empty ratchet.yml, red gate, unreleased tags, CI not green). The `text.complete` block is UNBYPASSABLE — `GLUDD_STOP_ENFORCE=0` disables only the non-text hooks. Stop-pattern make targets (commit/push/release) with pending work are also blocked. | `GLUDD_STOP_ENFORCE=0` (non-text hooks only) |
| 7 | `enforce-deadline.ts` | `tool.execute.before` | Task/agent/workflow dispatch whose elapsed wall-clock exceeds `GLUDD_TASK_TIMEOUT_MS` (default 300000ms = 5min); emits `TASK DEADLINE EXCEEDED` warning and records to `/tmp/gludd-task-stale.json`. | `GLUDD_TASK_DEADLINE_ENFORCE=0` (block), `GLUDD_TASK_DEADLINE_ENABLED=0` (detection) |
| 8 | `enforce-enhancement-ratio.ts` | `tool.execute.before`, `experimental.text.complete` | Task/agent/workflow dispatch waves with >50% fix dispatches when ≥2 dispatches are in the wave (forces ≥50% enhancement work per wave). | `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0` |
| 9 | `enforce-clean-tree.ts` | `tool.execute.before` | Task/agent/workflow dispatch when `git status --porcelain` is non-empty (dirty tree would cause pre-commit stash conflicts on push). | `GLUDD_CLEAN_TREE_ENFORCE=0` |
| 10 | `enforce-commit-lock.ts` | `tool.execute.before`, `tool.execute.after` | Concurrent git commit operations — acquires `/tmp/gludd-commit.lock` before any commit-shaped make target, releases on completion. Stale lock >2min is auto-reclaimed. | `GLUDD_COMMIT_LOCK_ENFORCE=0` |
| 11 | `enforce-verified-claims.ts` | `experimental.text.complete`, `tool.execute.before` | Outgoing text containing done-words (landed, committed, pushed, fixed, passing, shipped, done, complete, green, resolved, deployed, verified, passed, working) without machine-produced evidence (commit hash, `VERIFIED <branch>@<sha>`, `CI GREEN\|RED\|PENDING`, `N passed`, `=== GATE: PASSED ===`, `Collection OK`). | `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` |
| 12 | `enforce-no-suppressions.ts` | `tool.execute.before` | Edit/write containing lint-suppression comments: `# noqa`, `# type: ignore`, `# pylint: disable=...`, `# fmt: off/skip/on`, `# isort:skip`. Allowlisted: `src/general_ludd/security/fix_not_disable.py` and `tests/unit/test_type_safety_guardrails.py` (the patterns appear there as DATA, not as live suppressions). | `GLUDD_NO_SUPPRESSIONS_ENFORCE=0` |
| 13 | `enforce-no-wait.ts` | `tool.execute.before` | Bash `sleep N && make ...`, `make gate-tail` (follows forever), `make gate-status-check`, `make ci-wait` issued from the main thread; Task/agent/workflow dispatches whose prompt contains CI-poll anti-pattern phrases ("poll CI until terminal", "wait for CI green", "loop on make ci-verdict", "every N seconds ... up to N iterations"). | `GLUDD_NO_WAIT_ENFORCE=0` |
| 14 | `enforce-deletion-gate.ts` | `tool.execute.before` | Edit/write that deletes files (wholesale file removal requires explicit operator approval). | `GLUDD_DELETION_GATE_ENFORCE=0` |
| 15 | `enforce-batch-push.ts` | `tool.execute.before` | Bash push targets (`git-push-sandboxcom`, `git-push-branch`, `batch-push`, `development-push`) while a CI run is in-flight on the same branch (prevents CI cancellation thrash). | `GLUDD_BATCH_PUSH_ENFORCE=0` |
| 16 | `enforce-depth.ts` | `tool.execute.before` | Task/agent/workflow dispatch exceeding the configured nesting depth limit (prevents infinite subagent recursion). | `GLUDD_DEPTH_ENFORCE=0` |
| 17 | `enforce-tdd.ts` | `tool.execute.before` | Edit/write to `src/general_ludd/**/*.py` when no corresponding test file exists yet at `tests/unit/test_<module>.py` (or `test_general_ludd_<module>.py`). Forces test-first workflow at editor time. Allowlist: `__init__.py`, `*.pyi`, `protocols.py`, `typing.py`, `type_defs.py`, `_types.py`. | `GLUDD_TDD_ENFORCE=0` |
| 18 | `enforce-objective.ts` | `tool.execute.before` | Edit/write/bash when the configured PRIMARY OBJECTIVE for the session is unmet (prevents tangential work from displacing the top-priority directive). | `GLUDD_OBJECTIVE_ENFORCE=0` |
| 19 | `enforce-anti-essay.ts` | `experimental.text.complete`, `tool.execute.before` | Essay-length text (>50 words or >3 paragraphs by default) and status-summary patterns (bolded `**What changed?**`/`**Status:**` headers, "here's what was done", "session N summary") when pending work exists and the text carries no evidence. | `GLUDD_ANTI_ESSAY_ENFORCE=0` |
| 20 | `enforce-branch-discipline.ts` | `tool.execute.before` | Bash push/merge to master or release branches issued from inside a git worktree (worktree-isolated agents must not advance shared branch tips — see AGENTS.md "Branch discipline HARD GATE"). | `GLUDD_BRANCH_DISCIPLINE_ENFORCE=0` |
| 21 | `enforce-test-integrity.ts` | `tool.execute.before` | Edit/write to test files containing CI anti-patterns (skip/xfail markers added without justification, hardcoded CI run IDs, flaky-test suppression, `@pytest.mark.skip` on failing tests). | `GLUDD_TEST_INTEGRITY_ENFORCE=0` |
| 22 | `enforce-worktree.ts` | `tool.execute.before` | Bash push/merge/tag operations issued from inside a git worktree (broader than `enforce-branch-discipline` — blocks any shared-branch mutation from an isolated checkout). | `GLUDD_WORKTREE_ENFORCE=0` |
| 23 | `enforce-audit.ts` | `experimental.text.complete` | Text containing done-words when TASKS.md has unchecked items OR `config/ratchet.yml` has entries AND the text lacks machine-produced evidence (commit hash, test counts, gate-pass marker, CI verdict). Companion to `enforce-verified-claims.ts`. | `GLUDD_AUDIT_ENFORCE=0` |
| 24 | `enforce-context.ts` | `tool.execute.before` | All non-read tools when `SESSION.md` has not been updated in >24h (stale session context — forces a session-persistence refresh before further mutations). Read/grep/glob excluded. | `GLUDD_CONTEXT_ENFORCE=0` |
| 25 | `enforce-deliverable.ts` | `tool.execute.before` | Task/agent/workflow dispatch whose prompt lacks a concrete deliverable directive (must end with "Do NOT just report problems. Fix them." or equivalent — prevents status-check subagents from consuming floor slots). | `GLUDD_DELIVERABLE_ENFORCE=0` |
| 26 | `enforce-no-ci-poll.ts` | `tool.execute.before` | More than `GLUDD_CI_POLL_MAX` (default 3) consecutive CI-poll make targets (`ci-status`, `ci-verdict`, `ci-view`, `ci-await`, `ci-verdict-safe`, `gate-status-check`, `verify-release-completeness`, `release-view`) without an intervening productive mutation. Also: more than `GLUDD_STAGNANT_MAX` (default 5) consecutive stagnant read-only operations (incl. direct `read`/`glob`/`grep` tool calls). | `GLUDD_STAGNANT_ENFORCE=0` (stagnant detector); CI-poll detector has no env disable (intentional — polling is always an anti-pattern) |
| 27 | `enforce-release-deadline.ts` | `tool.execute.before` | Bash release operations (`release-cut`, `git-tag-push`, `release-create`, `release-deploy`) issued after the configured release deadline window has elapsed. | `GLUDD_RELEASE_DEADLINE_ENFORCE=0` |
| 28 | `watchdog.ts` | `event` | Not a blocker — observes `session.created`/`session.deleted` events to write/remove `.gate-logs/watchdog.pid`. Background daemon (`make watchdog-auto`) reads this PID to detect idle sessions and inject CONTINUE directives. | `GLUDD_WATCHDOG_ENABLED=0` |
| 29 | `enforce-floor-v2.ts` | `tool.execute.before`, `experimental.text.complete` | Tracks session-wide dispatched-minus-completed work and denies non-dispatch tools while the configured cumulative floor is deficient. | `GLUDD_FLOOR_V2_ENFORCE=0` |
| 30 | `enforce-directives.ts` | `tool.execute.before`, `experimental.text.complete` | Enforces explicit numeric, prohibition, completion, and all-items user directives; blocks commit/push or completion claims while a matched directive remains unmet. | `GLUDD_DIRECTIVE_ENFORCE=0` |
| 31 | `enforce-task-tracking.ts` | `tool.execute.before`, `experimental.text.complete`, `experimental.chat.system.transform` | Denies implementation edits until TASKS.md has been updated for the work, then emits escalating stale-task reminders and injects the task-tracking directive. | `GLUDD_TASK_TRACKING_ENFORCE=0` |

## Hook surface reference

The opencode plugin API exposes these hook surfaces; each plugin registers
for one or more:

| Hook | When it fires | Typical use |
|------|---------------|-------------|
| `experimental.chat.system.transform` | System prompt assembly | Inject directives into the system prompt at boot |
| `tool.execute.before` | Before every tool invocation | Block / deny / mutate tool calls |
| `tool.execute.after` | After every tool invocation (success or error) | Release locks, record metrics |
| `experimental.text.complete` | When the assistant finalizes a text response | Block / rewrite text-only responses |
| `session.idle` | When the session goes idle | Inject resume-work directives |
| `event` | Opencode lifecycle events (session created/deleted) | Side-effects (PID files, cleanup) |

## Disable patterns

Three ways to soften enforcement, in order of blast radius:

### 1. Single-plugin disable (preferred)

Every blocker plugin reads its env var on every hook invocation — no restart
needed for state-file-driven tunables. Plugin **source code** edits DO require
an opencode restart (plugins load once at startup).

```bash
GLUDD_FLOOR_ENFORCE=0 opencode           # one-shot
export GLUDD_MULTITASK_FLOOR_ENFORCE=0   # for the rest of the shell session
```

### 2. Bulk disengage (emergency)

`make disengage-enforcement` writes `/tmp/gludd-watchdog-disengage` which the
plugins consult on every hook. **As of 2026-07-15 this only disables heuristic
checks in `enforce-stop.ts` (COMPLETION_SMELL, COMPLETION_WORDS, QA patterns)
— the fundamental `hasRealPendingWork()` text-only block is NEVER bypassed.**
Disengage auto-expires after `MAX_DISENGAGE_MS`.

### 3. Full plugin layer disable (last resort)

Rename `.opencode/` to disable ALL enforcement:

```bash
mv .opencode .opencode.disabled
```

This collapses the entire guardrail layer — every plugin, including the
unbypassable `enforce-stop.ts` text block. Use only when a plugin source bug
prevents opencode from booting. Restore via `make restore-opencode`.

## Subagent isolation

Every plugin checks `process.env.OPENCODE_SUBAGENT === "1"` at the top of
each hook and returns early when true. Subagents inherit their orchestrator's
enforcement context — the orchestrator manages the floor, not the subagent.

If the env var is not set by the opencode framework, plugins fall back to
checking `/tmp/gludd-subagent-${process.pid}.json`. Run
`make hot-reload-plugins` if subagent enforcement leaks occur.

## See also

- `AGENTS.md` — full policy for every plugin (the "why")
- `make list-plugins` — runtime roster printout
- `make verify-enforcement` — health check across all plugins
- `make test-hook-runtime` — functional tests that invoke each hook
- `docs/ORCHESTRATION.md` — multitasking model and worktree lifecycle
