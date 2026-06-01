# Agentic Harness - Agent Rules

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

**Anti-Stop Patterns — these are policy violations:**
- Listing remaining tasks and asking "Want me to proceed?" or "What priority?"
- Answering a status question and then stopping instead of resuming work
- Saying "X is done. Next steps are A, B, C." and then stopping
- Asking "Should I continue?" when there are clearly pending tasks

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
