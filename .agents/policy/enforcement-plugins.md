## CRITICAL: Enforcement Plugin Reference (Session-Start Self-Awareness)

Before making ANY tool call, the agent MUST know which plugins are active and what they block. Run `make list-plugins` at session start for the current roster.

### Plugin Tool-Execute Blocks (in priority order)

Plugins fire in opencode.json registration order. Earlier plugins win on ties.

| Plugin | What it blocks | Disable via |
|--------|---------------|-------------|
| enforce-context.ts | ALL tools when SESSION.md stale >24h (↳ reads excluded) | GLUDD_CONTEXT_ENFORCE=0 |
| enforce-multitask.ts | ALL non-dispatch tools when <3 dispatches (↳ reads excluded) | GLUDD_MULTITASK_FLOOR_ENFORCE=0 |
| enforce-delegate.ts | edit/write/bash after 2 consecutive calls; read-grind after serial reads | GLUDD_MAINTHREAD_STREAK_ENFORCE=0 |
| enforce-floor.ts | ALL non-dispatch tools after 5 calls in 30s (↳ reads excluded) | GLUDD_FLOOR_ENFORCE=0 |
| enforce-session-start.ts | edit/write/bash until >=3 dispatches made | GLUDD_SESSION_START_ENFORCE=0 |
| enforce-make.ts | non-make bash commands | (hard-coded ON) |
| enforce-clean-tree.ts | task/agent dispatch on dirty git tree | GLUDD_CLEAN_TREE_ENFORCE=0 |
| enforce-tdd.ts | edit/write to src/ when no test file exists | GLUDD_TDD_ENFORCE=0 |
| enforce-no-suppressions.ts | edit/write with # noqa / # type: ignore | GLUDD_NO_SUPPRESSIONS_ENFORCE=0 |
| enforce-no-wait.ts | bash sleep/ci-wait/gate-tail on main thread | GLUDD_NO_WAIT_ENFORCE=0 |
| enforce-deadline.ts | task dispatch past timeout (5min default) | GLUDD_TASK_DEADLINE_ENFORCE=0 |
| enforce-depth.ts | task/agent dispatch exceeding depth limit | GLUDD_DEPTH_ENFORCE=0 |
| enforce-enhancement-ratio.ts | task/agent dispatch when fix% > 50% | GLUDD_ENHANCEMENT_RATIO_ENFORCE=0 |
| enforce-batch-push.ts | bash push while CI pending | GLUDD_BATCH_PUSH_ENFORCE=0 |
| enforce-branch-discipline.ts | bash push/merge from worktree | GLUDD_BRANCH_DISCIPLINE_ENFORCE=0 |
| enforce-worktree.ts | bash push/merge/tag from inside worktree | GLUDD_WORKTREE_ENFORCE=0 |
| enforce-deletion-gate.ts | edit/write that deletes files | GLUDD_DELETION_GATE_ENFORCE=0 |
| enforce-objective.ts | edit/write/bash when PRIMARY OBJECTIVE unmet | GLUDD_OBJECTIVE_ENFORCE=0 |
| enforce-verified-claims.ts | bash push without verification | GLUDD_VERIFIED_CLAIMS_ENFORCE=0 |
| enforce-commit-lock.ts | concurrent git operations | GLUDD_COMMIT_LOCK_ENFORCE=0 |
| enforce-task-tracking.ts | edit/write to src/ when TASKS.md unchanged | GLUDD_TASK_TRACKING_ENFORCE=0 |
| enforce-test-integrity.ts | edit/write with CI anti-patterns | GLUDD_TEST_INTEGRITY_ENFORCE=0 |

### Text-Output Plugins (fire on response, not on tool calls)

| Plugin | What it blocks | Disable via |
|--------|---------------|-------------|
| enforce-stop.ts | text-only responses when work pending | (hard-coded ON) |
| enforce-anti-essay.ts | essay-length responses when work pending | GLUDD_ANTI_ESSAY_ENFORCE=0 |
| enforce-audit.ts | done-words in text when work pending | GLUDD_AUDIT_ENFORCE=0 |

### Quick Reference

```bash
make list-plugins              # Full roster with hooks and block conditions
GLUDD_FLOOR_ENFORCE=0 make ... # Temporarily disable floor enforcement
GLUDD_SESSION_START_ENFORCE=0  # Disable session-start gate (Q&A sessions)
GLUDD_MAINTHREAD_STREAK_ENFORCE=0  # Disable delegate streak block
make verify-enforcement        # Check all plugins are healthy
```

**Key insight:** plugins that only block `edit/write/bash` or `task/agent` do NOT block `read`/`grep`/`glob` calls. The agent can always read files to diagnose blocked edits. Plugins marked "↳ reads excluded" explicitly skip read tools.


