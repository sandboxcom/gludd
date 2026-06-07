# Agentic Harness - Agent Rules

## CRITICAL: Pre-Response Stop Audit (READ BEFORE EVERY RESPONSE)

**Before sending ANY text response to the user, you MUST run this checklist:**

1. Check `todowrite` state. Are there items in `pending` or `in_progress`?
2. If yes → you MUST make a tool call, NOT send text. Your response must include at least one tool invocation that continues work.
3. The ONLY exception: ALL items are `completed` or `cancelled`.
4. If you catch yourself writing a completion summary, status report, or "done" message — STOP. Replace it with a tool call.

**This is a HARD block. Text-only responses while work remains are a policy violation.**

## CRITICAL: Instruction-Following Priority

**When the user gives a specific instruction that contradicts your current plan, you MUST follow the instruction IMMEDIATELY, before anything else.**

Examples of overriding instructions:
- "fix this bug FIRST before continuing" → fix the bug, do not continue other work
- "please address this message and continue" → address the message AND continue work
- "codify a process to do X" → codify X immediately, do not start other features
- "correct your code so that..." → fix your code NOW, not later

DO NOT do both simultaneously if the instruction says "first" or "before".
DO NOT start a new feature before fixing the thing the user just complained about.
DO NOT continue your own plan when the user redirects you.

Cop behavior patterns that trigger this (DO NOT DO THESE):
- "X passed, Y failed, Z skipped — committed" as final message
- "All done. Everything is complete." as final message
- A table/summary of completed work followed by no tool calls
- "Ready for review" or "Waiting for your feedback"
- Any message ending in "Done." with pending todos

CORRECT: If asked for status, respond briefly (1-2 lines) then IMMEDIATELY make a tool call.
CORRECT: After committing, immediately start the next pending task.
CORRECT: Never send text without also continuing work via a tool call.

## CRITICAL: Premature-Stop Audit Policy

**At the start of EVERY session, before doing any other work, you MUST:**

1. **Read `BUGS.md`** at the project root. This file tracks all premature-stop incidents.
2. **Audit your own previous session** for premature stops by reading SESSION.md and
   cross-referencing the "Next Steps" section. If Next Steps contains items that existed
   before the last commit, you stopped prematurely.
3. **Fix the root cause guardrail** before continuing with any project work.
4. **Log the incident** in `BUGS.md` with: date, what you stopped before finishing,
   why the guardrail failed, and what you fixed.

**A premature stop is ANY session exit where:**
- Your todo list had items in `pending` or `in_progress` state.
- SESSION.md "Next Steps" lists work that was identified but not started.
- You reported status/progress instead of continuing work.
- You asked "should I continue?" or equivalent.
- You listed remaining work and stopped without completing it.

**Every premature stop is a BUG.** Bugs in your own process are no different from bugs
in code — they must be tracked, root-caused, and fixed before moving on.

**Root cause categories to check:**
- Missing or weak guardrail (plugin hook doesn't detect the stop pattern)
- Guardrail is advisory only (console.warn) not blocking (throw/inject)
- System prompt doesn't mention the specific stop pattern
- AGENTS.md doesn't codify the specific pattern as forbidden
- No mechanism to detect pending todos at session boundary

**This is enforced by:**
- This AGENTS.md section — proactive instruction to audit on session start
- `.opencode/plugin/enforce-make.ts` — `chat.response.transform` hook detects stop patterns
- `BUGS.md` — persistent bug tracking for process failures

## CRITICAL: Task Completion Policy

**You MUST complete ALL requested work before stopping. No exceptions.**

1. If given a sprint, objective list, or multi-step task, work through EVERY
   step until all are complete or genuinely blocked.
2. Do NOT stop early to report status. Do NOT pause to ask if the user wants
   you to continue when instructions were explicit.
3. Do NOT treat infrastructure/tooling setup as the deliverable. Guardrails,
   hooks, and make targets exist to support the real work.
4. Do NOT get sidetracked. If you catch yourself spending time on something
   that is not the requested work, refocus immediately.
5. After completing one objective, immediately start the next. No victory laps.
6. Only stop when ALL objectives are complete or you hit a hard blocker you
   cannot fix (missing credentials, environment you cannot change).

**Anti-Stop Patterns — EVERY ONE of these is a policy violation:**
- Listing remaining tasks and asking "Want me to proceed?" or "What priority?"
- Listing findings/gaps/audit results and asking "Want me to start building?"
- Answering a status question and then stopping instead of resuming work
- Saying "X is done. Next steps are A, B, C." and then stopping
- Asking "Should I continue?" when there are clearly pending tasks
- Presenting a plan or analysis and waiting for approval before implementing
- Saying "Here's what needs to be done" and then NOT doing it immediately
- Asking any question that is really "should I do my job?" in disguise

**The ONLY valid response to identifying work that needs to be done is to DO IT.**
Never ask. Never wait. Just do the work. If the user wants you to stop,
THEY will tell you. Until then, keep working.

**When asked for status:** Answer briefly, then RESUME WORK immediately.
Do not ask for permission. Do not wait for acknowledgment.

**Self-Directed Work Rule: When you identify a gap, bug, or missing
integration while working, you MUST fix it immediately. Do NOT stop to ask
the user whether to proceed. Do NOT list the gap and wait for approval.
If you found it, you own it. Fix it, test it, commit it, then continue
with the original task. The only exception is if fixing it would require
credentials, payment, or environment changes you cannot make.**

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — injects completion policy into system prompt
- This AGENTS.md section — proactive instruction
- If you stopped early: RESUME WORK NOW.

## CRITICAL: No-Manual-Default Policy

**Every process MUST be fully automated. No step may require manual intervention by default.**

When you build a feature (downloader, installer, bundler, bootstrapper, etc.):

1. **No "run X manually" instructions.** Everything must be triggered by a `make` target or daemon initialization.
2. **No "config required" defaults.** Every config value must have a safe, working default. The system must boot without any user-created config files.
3. **No "download on request" workflows.** If a binary or resource is needed, it must be prefetched during the build cycle (`make dist`), not downloaded at first use.
4. **No dead-code isolation.** Every class in `src/` must be importable and instantiatable from daemon startup, even if function calls are deferred lazily.
5. **No check-only gateways.** Verify/download scripts must do the action, not just report "not done." If `make bundle-binaries` runs, it must bundle. If a healthcheck runs, it must remedy if possible.

**Manual default is a BUG. Fix it immediately.**

This is enforced by:
- The `completion_audit` in `make preflight` — flags unused classes
- The `no-manual-default` check in this section
- Plugin guardrail in `enforce-make.ts`

## Meta-Rule: Guardrail Policy

When you introduce ANY new restriction or policy on agent behavior, you MUST
implement it at all three layers. Single-layer restrictions are insufficient.

1. **Config permission** (`opencode.json` `permission` block) - hard gate
2. **Runtime hook** (`.opencode/plugin/*.ts`) - contextual error with guidance
3. **Agent prompt** (`AGENTS.md` prominent section) - proactive instruction

Every guardrail must have all three. If you catch yourself adding only one or
two, stop and add the missing layers before continuing. See the
`guardrail-pattern` skill for the full pattern and checklist.

## CRITICAL: Bash Command Policy

**You MUST only run `make <target>` commands in bash. Never run any other command directly.**

- ALLOWED: `make test`, `make lint`, `make init`, `make sync`, etc.
- DENIED: `uv run ...`, `python3 ...`, `pip install ...`, `git ...`, `which ...`, `ls ...`, `cat ...`, `find ...`, `rm ...`, or any other direct command.

**Shell metacharacters are FORBIDDEN:**

| Character | Name | Why forbidden |
|-----------|------|---------------|
| `\|` | Pipe | Chains commands, bypasses make |
| `;` | Semicolon | Runs multiple commands |
| `&&` | And | Chains commands conditionally |
| `\|\|` | Or | Chains commands conditionally |
| `()` | Subshell | Runs commands in subprocess |
| `$()` | Command substitution | Embeds command output |
| `` ` `` | Backtick | Command substitution |
| `>` / `<` | Redirect | Pipes output to files |
| `2>&1` | Redirect stderr | Chains stderr to stdout |
| `{}` | Brace expansion | Generates arguments |
| `!` | History expansion | Accesses previous commands |

**If you need ANY of these, create a Makefile target.** Make targets ARE allowed to use metacharacters internally.

VIOLATIONS (all will be blocked by the plugin):
- `make test-unit 2>&1 | tail -20`
- `cd /foo && make test`
- `make test; make lint`
- `$(cat file)`
- `make test || true`
- `.venv/bin/python -m pytest ...`
- `cd /path && .venv/bin/python ...`

This is enforced by:
- `opencode.json` permission rules (hard deny on non-make bash)
- `.opencode/plugin/enforce-make.ts` (blocks metacharacters + non-make commands)
- This AGENTS.md section (proactive reminder)

## CRITICAL: TDD Policy

**You MUST write a failing test BEFORE writing implementation code. No exceptions.**

Workflow for every change:
1. Identify the behavior you need.
2. Write a test that fails because the behavior does not exist yet.
3. Run `make test-unit` — confirm the test fails.
4. Write the minimal implementation to make the test pass.
5. Run `make test-unit` — confirm the test passes.
6. Refactor if needed, keeping tests green.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — prints TDD reminder when you edit files under `src/`
- This AGENTS.md section — proactive instruction
- The guardrail-pattern skill — reusable pattern reference

Do not skip steps. Do not write implementation and then retroactively add tests.
Do not mark work complete unless a test proves the behavior exists.

## CRITICAL: Commit-After-Green Policy

**You MUST commit your work after tests pass and the change is complete. Do not leave green work uncommitted.**

Workflow:
1. Tests pass for the change you made.
2. Run `make test-and-commit` — this runs the full test suite and commits only if all tests pass.
3. If you want a descriptive message, run `make test-and-commit MSG="your message"`.

If you notice uncommitted changes that are test-green, stop what you are doing
and commit them before starting new work.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — prints commit reminder after test runs pass
- `Makefile` `test-and-commit` target — atomic test-then-commit
- This AGENTS.md section — proactive instruction

## CRITICAL: Evidence-Based Response Policy

Every factual claim MUST have supporting evidence from a tool call, file read, URL fetch, or test result.
- If you say "X tests pass", cite the make output.
- If you say "file Y contains Z", cite the file path and line number.
- If you say "opencode supports X", cite the URL or docs page you fetched.
- Unsupported claims are policy violations.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — injects evidence policy into system prompt
- `src/general_ludd/review/evidence_checker.py` — runtime claim auditing
- This AGENTS.md section — proactive instruction

## Project Overview

This is the general-ludd-agent project: an autonomous coding system with Ansible runners and multi-model AI agents.

- Primary language: Python 3.11+
- Package manager: uv (preferred), pip (fallback)
- Test runner: pytest
- Linter: ruff
- Type checker: mypy
- Worker: FastAPI + Gunicorn + uvicorn-worker
- Database: PostgreSQL (Alembic migrations)
- Secrets: OpenBao + hvac
- Playbook execution: Ansible Runner
- Testing strategy: TDD, Molecule for Ansible content

## Key Make Targets

### Testing
- `make test` - Run full test suite with coverage
- `make test-unit` - Run unit tests only
- `make test-e2e` - Run end-to-end tests
- `make test-guardrails` - Test guardrail infrastructure
- `make test-and-commit` - Run tests then commit if green (`MSG="msg"` for custom message)

### Quality
- `make lint` - Run ruff linter
- `make lint-fix` - Run ruff with auto-fix
- `make typecheck` - Run mypy
- `make healthcheck` - Verify imports work
- `make qa` - Run lint + typecheck + test + healthcheck
- `make validate` - Full validation including ansible syntax

### Setup
- `make init` - Set up the project (dirs + deps)
- `make sync` - Sync uv dependencies
- `make bootstrap` - init + lint + test + healthcheck
- `make clean` - Remove build artifacts

### Git (use ONLY these — NEVER raw git commands)
- `make git-status` - Show git status
- `make git-diff` - Show diff stats
- `make git-staged` - Show staged changes
- `make git-log` - Show recent commits
- `make git-init` - Initialize git repo
- `make git-add FILES='f1 f2 ...'` - Stage specific files
- `make git-add-all` - Stage all changes
- `make git-commit MSG='message'` - Commit staged changes with message
- `make git-reset FILES='HEAD~1'` - Reset to ref (soft by default)
- `make git-branch MSG='name'` - Create branch
- `make git-checkout MSG='branch'` - Switch branch
- `make git-merge MSG='branch'` - Merge branch with --no-ff

### Feature Branch Workflow
- `make feature-start MSG='feature/short-name'` - Create and switch to feature branch
- `make feature-done MSG='feature/short-name'` - Test, merge to master with --no-ff

## CRITICAL: Session Persistence Policy

**You MUST maintain `SESSION.md` at the root of the project. Read it at session start to restore context. Update it after every logical unit of work (feature, fix, test suite). Never leave it stale.**

The file must contain:
- Last updated date
- Current test suite status (pass/fail/skip counts, coverage)
- Last commit hash
- Completed objectives/features
- Known gaps
- Next steps

This ensures you NEVER have to ask "what did we do so far?" — read SESSION.md.

This is enforced by:
- This AGENTS.md section — proactive instruction
- The General Ludd agent's own `AgentBehavior.session_persistence` flag — agents self-enforce
- The `BehaviorRenderer` includes session persistence rules in rendered system prompts

## Working Conventions

- TDD: write failing tests first (enforced by plugin + policy)
- Small, testable increments
- Keep the event loop thin
- Ansible playbooks are the tool-call boundary
- Never force-push
- Never run non-make commands in bash (enforced by plugin + policy)
- Commit after tests pass (enforced by plugin + policy)
- When adding any new guardrail, apply all three layers (enforced by meta-rule)
- **Feature branches**: Start a branch per feature with `make feature-start`, commit small green increments onto it, then `make feature-done` to merge with --no-ff after full test suite passes
- **Atomic commits**: Each commit should represent one logical change (one test file, one feature, one fix). Never batch unrelated changes into a single commit.

## CRITICAL: Self-Audit Policy

**After completing any significant body of work, you MUST perform a full self-audit before declaring it done.**

### Full Self-Audit Checklist

Run through EVERY item below. Do NOT skip any. Fix all gaps immediately.

1. **Conversation History Audit**: Read ALL user messages from the opencode conversation
   database at `~/.local/share/opencode/opencode.db` (table: `message`, join `part` for content).
   Extract every explicit request. Cross-reference each against implementation.

2. **Dead Code Audit**: For every new class/module you created, search the ENTIRE `src/` tree
   for imports of that class. If it is only imported in test files, it is dead code — wire it
   into the daemon, event loop, worker, or relevant subsystem.

3. **Wiring Audit**: For every new field added to a schema/model:
   - Is it populated at creation time? (check the daemon endpoints and event loop)
   - Is it propagated through the pipeline? (check JobSpec construction in EventLoop)
   - Is it consumed at the destination? (check Worker endpoints)
   - Is it returned in API responses? (check daemon response dicts)

4. **Migration Audit**: For every new SQLAlchemy model or column:
   - Does an Alembic migration file exist in `alembic/versions/`?
   - Does the migration revision chain link correctly? (`down_revision` references previous)
   - Does `downgrade()` reverse `upgrade()` completely?

5. **Test Level Audit**: Verify tests exist at ALL three levels:
   - **Unit tests** (`tests/unit/`): Test individual functions/classes in isolation
   - **Integration tests** (`tests/integration/`): Test 2+ subsystems together (e.g., EventLoop + DB)
   - **E2E tests** (`tests/e2e/`): Test through the daemon API as a user would

6. **Gap Audit**: For every feature area, check:
   - Does the daemon endpoint exist? Does it support the new field?
   - Does the CLI expose the feature? (`--project`, etc.)
   - Does logging include the new context? (project_id in log records)
   - Are secrets scoped? (per-project secret paths)
   - Is the config per-project? (project-level config overrides)

7. **Cross-Interface Completeness Audit**: For every NEW feature or capability added:
   - If added to CLI, is it ALSO available in the TUI? (e.g., project add → TUI project view)
   - If added to daemon API, is there a CLI command AND a TUI action?
   - If added as a config option, is there a daemon endpoint AND a CLI flag?
   - If added to one view, is it accessible from ALL relevant views?
   - **Pattern**: "CLI get project add" → MUST also have TUI project management.
     "CLI get dispatch_mode" → TUI must show and allow setting it.
   - **Anti-pattern**: Declaring a feature done because it exists in ONE interface.

8. **Evidence**: After completing the audit, run `make test` and cite the pass count.
   Run `make lint` and `make typecheck` and cite the results.

### How to Execute

```
1. Read opencode.db messages (or re-read the conversation history)
2. For each user request, grep the src/ tree for implementation
3. For each implementation class, grep for usage (imports) outside test/
4. For each schema field, trace it: daemon -> event_loop -> worker -> response
5. For each DB model, check alembic/versions/ for migration
6. Check tests/unit/, tests/integration/, tests/e2e/ for coverage
7. Fix all gaps, run make test, commit green
```

This is enforced by:
- This AGENTS.md section — proactive instruction
- The session persistence policy — SESSION.md tracks known gaps
