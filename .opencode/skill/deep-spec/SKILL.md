---
name: spec.md
version: 4.1.0
description: Spec-Driven Development framework that guides tasks from intention to implementation through a 3-stage pipeline (drafts → active → archive) using the A-B-C documentation flow (APPROACH, BUSINESS_CONTEXT, COMPLETION_REPORT). Use when the user says "Initialize spec.md", "Create task", "Approve task", "Complete task", "Discard task", "Revise task", "Map codebase", "Diagram architecture", "Interview task", "Refinar tarefa", or mentions spec.md, Review Gate, spec-driven development, TDD planning, or the `.spec.md/` folder.
---

# spec.md Framework (Spec-Driven Development)

## TL;DR

```
drafts/ ──(Approve)──> active/ ──(execute → Review Gate)──> archive/
```

Each task carries **A-B-C docs**: `APPROACH.md` (how), `BUSINESS_CONTEXT.md` (why + ACs),
`COMPLETION_REPORT.md` (evidence).

Bootstrap: `"Initialize spec.md"` scaffolds `.spec.md/`.

---

## Pipeline Diagram

```
                        ┌─────────────┐
                        │  drafts/    │
           "Create task" │  (planning) │
        ┌──────────────>│             │
        │               └──────┬──────┘
        │                      │
        │             "Approve task"
        │                      │
        │               ┌──────▼──────┐     "Discard task"
        │               │  active/    │<─── (draft only)
        │               │ (executing) │
        │               │             │
        │               │  ┌───────┐  │
        │               │  │ TDD   │  │
        │               │  │ loop  │  │
        │               │  └───┬───┘  │
        │               │      │      │
        │               │  Execution │
        │               │  complete  │
        │               │      │      │
        │               │ ┌────▼────┐ │
        │               │ │ Review  │ │
        │               │ │ Gate    │─┼─── feedback loop ("Revise task")
        │               │ │[IN REVIEW]│<── (Review Rounds)
        │               │ └────┬────┘ │
        │               │      │      │
        │               └──────┼──────┘
        │                      │
        │             "Complete task"
        │                      │
        │               ┌──────▼──────┐
        │               │  archive/   │
        └───────────────│  (done /    │
                        │  discarded) │
                        └─────────────┘
```

### State machine

| From | Trigger | To | Side effects |
|---|---|---|---|
| (none) | `"Create task"` | `drafts/` | Creates `APPROACH.md`, `BUSINESS_CONTEXT.md`, `COMPLETION_REPORT.md` (Status: DRAFT) |
| `drafts/` | `"Approve task"` | `active/` | Moves folder; Status → IN PROGRESS; TDD execution begins same turn |
| `drafts/` | `"Discard task"` | `archive/` | Status → DISCARDED; indexed in `memory.md` |
| `active/` | (execution complete) | `active/` [IN REVIEW] | Status → IN REVIEW; Review Gate opened |
| `active/` [IN REVIEW] | `"Revise task"` | `active/` [IN PROGRESS] | Review Round appended to APPROACH; TDD resumes |
| `active/` [IN REVIEW] | `"Complete task"` | `archive/` | Status → DONE; indexed in `memory.md` |
| `active/` [IN PROGRESS] | (deviation discovered) | `active/` [IN PROGRESS] | Deviation appended to APPROACH; re-approval requested |

---

## Complete Folder Structure

```
.spec.md/
├── AGENTS.md              # Auto-generated project context (tech stack, coding standards, testing, personas)
├── memory.md              # Index of all completed/discarded tasks + lessons learned
│
├── specs/
│   ├── drafts/            # Tasks being planned (no code written yet)
│   │   └── <task-name>/
│   │       ├── APPROACH.md
│   │       ├── BUSINESS_CONTEXT.md
│   │       ├── COMPLETION_REPORT.md
│   │       └── OPEN_QUESTIONS.md        # (optional) blocking decisions for user
│   │
│   ├── active/            # Tasks being executed (code IS being written)
│   │   └── <task-name>/
│   │       ├── APPROACH.md              # ## Execution Plan (immutable) + ## Deviations + ## Review Rounds
│   │       ├── BUSINESS_CONTEXT.md
│   │       ├── COMPLETION_REPORT.md     # Status: [IN PROGRESS] / [IN REVIEW] / [DONE]
│   │       └── OPEN_QUESTIONS.md        # (optional) moves with the folder
│   │
│   └── archive/           # Completed or discarded tasks
│       └── <task-name>/
│           ├── APPROACH.md
│           ├── BUSINESS_CONTEXT.md
│           ├── COMPLETION_REPORT.md     # Status: [DONE] or [DISCARDED]
│           └── OPEN_QUESTIONS.md        # (optional) with all questions answered
│
├── templates/             # A-B-C document templates
│   ├── APPROACH.template.md
│   ├── BUSINESS_CONTEXT.template.md
│   └── COMPLETION_REPORT.template.md
│
├── hooks/                 # Compiled validation hooks (.mjs)
│   ├── list.mjs
│   ├── track.mjs
│   ├── repair.mjs
│   └── validate.mjs
│
└── tracking.json          # Machine-readable registry of all tasks
```

---

## APPROACH.md (how)

### Template

```markdown
# APPROACH — <Task Name>

**Created:** <YYYY-MM-DD>
**Size:** Small | Medium | Large
**Status:** DRAFT | IN PROGRESS | IN REVIEW

## Architecture Overview

<1-3 sentences describing the approach at a high level.>

## Execution Plan

1. **Step 1 — <Step name>**
   - Files: `<files to create or modify>`
   - Tests added: `<test names or AC references>`
   - Done when: `<verifiable condition>`

2. **Step 2 — <Step name>**
   - Files: `<files>`
   - Done when: `<condition>`

3. **Step 3 — Final validation**
   - Lint, typecheck, CI green
   - All acceptance criteria met

## Risks & Mitigations

- **Risk:** <what could go wrong>
  **Mitigation:** <how we handle it>

## Dependencies

- Depends on: `<other task>` (or "None")

## Deviations

<!-- Added mid-flight when the plan must change -->
<!-- Format: date, what changed, why, re-approved by -->

## Review Rounds

<!-- Added at Review Gate for post-implementation iteration -->
<!-- Format: Round N — date — feedback → delta steps → outcome -->
```

### Filled example — "Add exponential backoff to job retry"

```markdown
# APPROACH — Add exponential backoff to job retry

**Created:** 2026-07-25
**Size:** Medium
**Status:** DRAFT

## Architecture Overview

The current job retry logic retries immediately (fixed 1s delay). Replace with
exponential backoff capped at 10 minutes, with jitter to prevent thundering herd.

## Execution Plan

1. **Step 1 — Add backoff configuration to JobConfig model**
   - Files: `src/general_ludd/db/models.py`, `alembic/versions/*_add_backoff_config.py`
   - Tests added: AC-1 (config validates), AC-2 (defaults applied when unset)
   - Done when: `JobConfig` has `max_retries`, `base_delay_ms`, `max_delay_ms`,
     `jitter_pct` fields; migration runs up and down cleanly.

2. **Step 2 — Implement RetryPolicy calculator**
   - Files: `src/general_ludd/jobs/retry.py` (new)
   - Tests added: AC-3 (delay grows exponentially), AC-4 (delay capped at max),
     AC-5 (jitter within bounds), AC-6 (retries exhausted → terminal)
   - Done when: `RetryPolicy.next_delay(attempt: int) -> int` passes all tests.

3. **Step 3 — Wire RetryPolicy into job dispatcher**
   - Files: `src/general_ludd/dispatcher.py`
   - Tests added: AC-7 (failed job retries with backoff), AC-8 (terminal after
     max_retries exhausted), AC-9 (successful jobs not retried)
   - Done when: integration tests pass — failed job shows correct retry delay.

4. **Step 4 — Final validation**
   - `make lint` — 0 errors
   - `make typecheck` — ≤ baseline
   - `make test` — all pass
   - `make gate` — green

## Risks & Mitigations

- **Risk:** Backoff times too aggressive for time-sensitive operations
  **Mitigation:** Make all delays configurable per job type via JobConfig.
- **Risk:** Clock-skew causes jitter to overflow
  **Mitigation:** Test with edge-case timestamps (year 2038, negative durations).

## Dependencies

- None (standalone feature)
```

---

## BUSINESS_CONTEXT.md (why + acceptance criteria)

### Template

```markdown
# BUSINESS_CONTEXT — <Task Name>

**Created:** <YYYY-MM-DD>

## Problem Statement

<What problem does this solve? Who is affected? What is the current behavior?>

## Stakeholders

- <Role>: <what they care about>

## Acceptance Criteria

### AC-1 — <Short name>
**Given** <initial state>
**When** <action or event>
**Then** <expected outcome>

### AC-2 — <Short name>
**Given** ...
**When** ...
**Then** ...

<!-- Additional ACs as needed -->

## Non-Goals (explicitly out of scope)

- <Thing we are NOT doing, to prevent scope creep>

## References

- <Links to issues, design docs, prior discussions>
```

### Filled example — "Add exponential backoff to job retry"

```markdown
# BUSINESS_CONTEXT — Add exponential backoff to job retry

**Created:** 2026-07-25

## Problem Statement

When a dispatched subagent fails (API timeout, rate limit, worker crash), the
dispatcher retries the job immediately with a fixed 1-second delay. This causes:

1. **Thundering herd on rate-limited APIs** — 10 agents retry simultaneously,
   all hitting the same rate-limit window.
2. **Waste of retry budget** — 5 retries at 1s intervals exhaust in 5 seconds
   without giving the downstream service time to recover.
3. **No jitter** — synchronized retries stay synchronized, amplifying the herd.

## Stakeholders

- Agent dispatcher (reliability): wants jobs to succeed without manual re-dispatch
- API consumer (cost): wants to avoid wasting tokens on doomed retries
- Operator (observability): wants to see retry patterns in logs/monitoring

## Acceptance Criteria

### AC-1 — Backoff config validates on creation
**Given** a JobConfig with `base_delay_ms=100`, `max_delay_ms=60000`, `jitter_pct=20`
**When** the config is validated
**Then** validation passes

### AC-2 — Backoff defaults applied when unset
**Given** a JobConfig with no backoff fields set
**When** the config is loaded
**Then** defaults are: `base_delay_ms=1000`, `max_delay_ms=600000`, `max_retries=5`,
`jitter_pct=25`

### AC-3 — Delay grows exponentially
**Given** a RetryPolicy with `base_delay_ms=1000`
**When** `next_delay(attempt=3)` is called
**Then** the delay is approximately `1000 * 2^3 = 8000ms` (before jitter)

### AC-4 — Delay is capped at max
**Given** a RetryPolicy with `max_delay_ms=60000`
**When** `next_delay(attempt=10)` is called (would produce ~1024s without cap)
**Then** the delay is exactly `60000ms`

### AC-5 — Jitter is within bounds
**Given** a RetryPolicy with `jitter_pct=25` and a calculated delay of `10000ms`
**When** `next_delay()` is called 100 times
**Then** all results are between `7500ms` and `12500ms` (25% jitter)

### AC-6 — Retries exhausted produces terminal result
**Given** a RetryPolicy with `max_retries=3` and a job that has been retried 3 times
**When** `next_delay(attempt=4)` is called
**Then** it raises `RetriesExhausted` with the original error

### AC-7 — Failed job retries with correct delay
**Given** a job that fails with a retryable error
**When** the dispatcher processes the failure
**Then** the job is re-queued with a delay matching the RetryPolicy output for
the current attempt count

### AC-8 — Terminal failure after max retries
**Given** a job that has been retried `max_retries` times and fails again
**When** the dispatcher processes the failure
**Then** the job status is set to `terminal` and no further retries are scheduled

### AC-9 — Successful jobs not retried
**Given** a job that completes successfully
**When** the dispatcher processes the result
**Then** no retry is scheduled; the job status is `completed`

## Non-Goals (explicitly out of scope)

- Dead-letter queue for terminal failures (future feature)
- Circuit-breaker pattern for consistently-failing job types (future feature)
- Custom per-job-type backoff strategies (using configurable defaults only)

## References

- Issue: #427 — "Jobs flood API after transient failure"
- Design doc: `docs/design/retry_backoff.md`
```

---

## COMPLETION_REPORT.md (evidence)

### Template

```markdown
# COMPLETION_REPORT — <Task Name>

**Created:** <YYYY-MM-DD>
**Completed:** <YYYY-MM-DD> (or blank until done)
**Status:** [DRAFT] | [IN PROGRESS] | [IN REVIEW] | [DONE] | [DISCARDED]

## Execution Log

### Step 1 — <Name>
- Files touched: `<list>`
- Tests added: `<list>`
- Decisions: `<notable choices made>`
- Outcome: ✅ done / ⚠️ partial

### Step 2 — <Name>
- Files touched: `<list>`
- Outcome: ✅ done

<!-- Repeat for each step -->

## Acceptance Criteria Traceability

| AC | Test file | Status |
|---|---|---|
| AC-1 | `test_foo.py::test_x` | ✅ |
| AC-2 | `test_foo.py::test_y` | ✅ |

## Review Gate

**Opened:** <YYYY-MM-DD>
**User decision:** pending | approved | changes requested

### Round 1 — <YYYY-MM-DD>
**Feedback:** <user feedback>
**Changes made:** <list>
**Outcome:** ✅ resolved

<!-- Additional rounds as needed -->

## Artifacts

- Commits: `<list of commit hashes>`
- Test results: `N passed, 0 failed, 0 skipped`
- Gate: `=== GATE: PASSED ===`
```

### Filled example (mid-execution)

```markdown
# COMPLETION_REPORT — Add exponential backoff to job retry

**Created:** 2026-07-25
**Status:** [IN PROGRESS]

## Execution Log

### Step 1 — Add backoff configuration to JobConfig model
- Files touched: `src/general_ludd/db/models.py`,
  `alembic/versions/20260725_add_backoff_config.py`
- Tests added: `test_job_config_backoff_defaults.py`,
  `test_job_config_backoff_validation.py`
- Decisions: Used JSON column for backoff config to allow evolution without
  migrations. Pydantic model validates at the application layer.
- Outcome: ✅ done

### Step 2 — Implement RetryPolicy calculator
- Files touched: `src/general_ludd/jobs/retry.py`
- Tests added: `test_retry_policy_exponential_growth.py`,
  `test_retry_policy_max_cap.py`, `test_retry_policy_jitter_bounds.py`,
  `test_retry_policy_retries_exhausted.py`
- Outcome: ✅ done

### Step 3 — Wire RetryPolicy into job dispatcher
- Files touched: `src/general_ludd/dispatcher.py`
- Outcome: ⏳ in progress

## Acceptance Criteria Traceability

| AC | Test file | Status |
|---|---|---|
| AC-1 | `test_job_config_backoff_validation.py::test_valid_config` | ✅ |
| AC-2 | `test_job_config_backoff_defaults.py::test_defaults_applied` | ✅ |
| AC-3 | `test_retry_policy_exponential_growth.py::test_doubles_each_attempt` | ✅ |
| AC-4 | `test_retry_policy_max_cap.py::test_capped_at_max_delay` | ✅ |
| AC-5 | `test_retry_policy_jitter_bounds.py::test_jitter_within_range` | ✅ |
| AC-6 | `test_retry_policy_retries_exhausted.py::test_raises_when_exhausted` | ✅ |
| AC-7 | (pending) | ⏳ |
| AC-8 | (pending) | ⏳ |
| AC-9 | (pending) | ⏳ |

## Review Gate

**Opened:** (not yet)
```

---

## Task Sizing Examples

### Small task APPROACH.md (bullet-list, no diagram)

```markdown
# APPROACH — Add `make verify-state` target

**Created:** 2026-07-25
**Size:** Small
**Status:** DRAFT

## Execution Plan

- Add `verify-state` target to Makefile wrapping `git status` + `git log` +
  `make ci-verdict-safe`
- Files: `Makefile` only
- Tests: verify target exists and exits 0 on clean state
- Done when: `make verify-state` prints status output and exits 0
```

### Medium task APPROACH.md (full A-B-C, structured steps)

The "Add exponential backoff to job retry" example above is a Medium task.

### Large task APPROACH.md (full A-B-C + architecture diagram + split consideration)

```markdown
# APPROACH — Multi-tenant project isolation

**Created:** 2026-07-25
**Size:** Large — consider splitting into 3 Medium tasks
**Status:** DRAFT

## Architecture Overview

Currently, all projects share one daemon process and one database. Multi-tenant
isolation requires: per-project daemon ports, per-project DB schemas, per-project
secret namespaces, and per-project agent pools.

## Execution Plan

### Phase 1 — Per-project daemon ports (Medium, 2 days)
1. Add `project_id` to DaemonConfig
2. Allocate port from range based on project_id hash
3. Wire healthcheck to project-scoped endpoint
4. Files: `daemon.py`, `config/*.yml`, `healthcheck.py`

### Phase 2 — Per-project DB schemas (Medium, 3 days)
1. Add `schema_name` to DatabaseConfig
2. Migration runner scoped to schema
3. Repository layer schema-aware queries
4. Files: `db/models.py`, `db/repository.py`, `alembic/env.py`

### Phase 3 — Per-project agent pools (Medium, 2 days)
1. AgentPool scoped to project_id
2. Floor enforcement per project
3. CI tracking per project
4. Files: `loop.py`, `dispatcher.py`, `agent/pool.py`

## Risks & Mitigations

- **Risk:** Port collision from hash function
  **Mitigation:** Check port availability at startup; fail-fast with clear error.

## Dependencies

- None (phases are independent and can be developed/merged separately)
```

---

## OPEN_QUESTIONS.md

### Template

```markdown
# OPEN QUESTIONS — <Task Name>

Questions that block implementation progress until resolved by the user.

### Q1 — <Title>
**Status:** open | answered
**Asked:** <YYYY-MM-DD>
**Context:** <Why does this need a decision? What are the options?>
**Question:** <The actual question>
**Answer:** <Filled when resolved> (or blank)
**Answered:** <YYYY-MM-DD> (or blank)
```

### Example — 3 questions at different lifecycle stages

```markdown
# OPEN QUESTIONS — Multi-tenant project isolation

### Q1 — Schema isolation strategy
**Status:** answered
**Asked:** 2026-07-25
**Context:** We need to isolate project data in PostgreSQL. Options:
1. Separate databases per project (strongest isolation, Ops burden)
2. Separate schemas per project (good isolation, manageable)
3. Row-level `project_id` column (weakest isolation, simplest)
**Question:** Which isolation level should we use?
**Answer:** Option 2 — separate schemas. Strong enough for our threat model,
manageable for migrations.
**Answered:** 2026-07-26

### Q2 — Port allocation range
**Status:** open
**Asked:** 2026-07-25
**Context:** Per-project daemon ports need a range. We have 10 projects max.
**Question:** What port range should we reserve? (e.g., 8100-8199)
**Answer:**
**Answered:**

### Q3 — Agent pool sharing across projects
**Status:** open
**Asked:** 2026-07-26
**Context:** Currently 10 agents shared across all projects. Options:
1. Dedicated pool per project (guarantees capacity, more expensive)
2. Shared pool with project quotas (efficient, harder to enforce)
**Question:** Per-project pools or shared pool with quotas?
**Answer:**
**Answered:**
```

### Lifecycle rules

- **Only create** `OPEN_QUESTIONS.md` when there is genuinely a blocking decision.
- **One question per entry** — use `### QN — Title` format.
- **Set `Status: answered`** and fill `Answer:` + `Answered:` when the user responds.
- **The file moves with the folder** across `drafts/` → `active/` → `archive/`.
- **If all questions are answered**, the file can be empty or deleted. It is
  harmless to leave it in `archive/` as a record of decisions made.

---

## memory.md

### Format

```markdown
# spec.md Memory

## Completed Tasks

[YYYY-MM-DD] <task-name>: <one-line summary>. Ref: specs/archive/<task-name>

## Discarded Tasks

[YYYY-MM-DD] <task-name>: [discarded] <reason>. Ref: specs/archive/<task-name>

## Lessons

- <Lesson learned from a completed task. Write as a guideline for future work.>
- <Another lesson.>
```

### Example — 5 entries

```markdown
# spec.md Memory

## Completed Tasks

[2026-07-25] add-backoff-retry: Exponential backoff with jitter for job retry.
    Ref: specs/archive/add-backoff-retry

[2026-07-20] fix-git-locking-in-worktrees: Fixed cross-process lock file locator
    to respect git worktree .git-as-file layout. Ref: specs/archive/fix-worktree-locking

[2026-07-18] add-verify-state-target: Makefile target bundling git status + log +
    CI verdict for verification evidence. Ref: specs/archive/add-verify-state

[2026-07-15] fix-enforce-stop-disengage: Disengage now only skips heuristic checks;
    fundamental text-only block never bypassed. Ref: specs/archive/fix-disengage-bypass

[2026-07-10] add-ci-check-cooldown: Machine-enforced cooldown to prevent CI polling.
    Ref: specs/archive/add-ci-cooldown

## Discarded Tasks

[2026-07-22] add-graphql-endpoint: [discarded] REST API sufficient for current
    use cases; GraphQL added complexity without user demand.
    Ref: specs/archive/add-graphql-endpoint

## Lessons

- Git worktree `.git` is a FILE not a directory — `os.path.isdir(repo/.git)`
  fails silently. Use `git rev-parse --git-common-dir` instead.

- Exponential backoff without jitter causes thundering herd on retry. Always
  add jitter (±25%) to the calculated delay.

- `cast(Any, x)` is semantically a no-op — it does not change the runtime type
  and silences mypy without narrowing. Use `cast(ConcreteType, x)` or fix the
  type mismatch.

- `make gate` on the main thread blocks ALL subagent dispatch for 40 minutes.
  Always use `make gate-background` + poll from a subagent.

- Enforcement plugins must check `OPENCODE_SUBAGENT === "1"` at the top of
  every hook function. A missing subagent guard breaks all delegated work.
```

---

## AGENTS.md Bootstrap Content

### Python project (e.g., gludd)

```markdown
# AGENTS.md

## Tech Stack

- Python 3.11+, FastAPI, SQLAlchemy (PostgreSQL), Alembic
- Package manager: uv
- Test runner: pytest with coverage
- Linter: ruff
- Type checker: mypy
- CI: GitHub Actions

## Coding Standards

- TDD: write failing test BEFORE implementation
- Type annotations on ALL function signatures (no `Any`)
- No lint-suppression comments (`# noqa`, `# type: ignore`)
- Commit after green gate (lint + typecheck + tests)

## Testing

- Unit tests in `tests/unit/` — test individual functions/classes
- Integration tests in `tests/integration/` — test 2+ subsystems
- E2E tests in `tests/e2e/` — test through daemon API
- Coverage threshold: 85% per file

## Personas

- Python backend engineer (default)
- DevOps/Ansible specialist (for playbook and provisioning tasks)
- TypeScript plugin engineer (for .opencode/plugin/*.ts files)
```

### TypeScript project

```markdown
# AGENTS.md

## Tech Stack

- TypeScript 5.x, Node 20+, React 18
- Bundler: Vite
- Test runner: Vitest
- Linter: ESLint with strict config
- CI: GitHub Actions

## Coding Standards

- No `any` in function signatures — use `unknown` + type guards
- Prefer `interface` over `type` for object shapes
- Prefer discriminated unions over optional-everything patterns
- No lint-suppression comments

## Testing

- Unit tests: `__tests__/` mirroring `src/` structure
- Component tests: `@testing-library/react`
- E2E tests: Playwright

## Personas

- React/TypeScript frontend engineer (default)
- Node.js API engineer (for backend endpoints)
```

---

## Commands Reference

| Trigger | Command | Pipeline stage | Files touched | State transition | Validation |
|---|---|---|---|---|---|
| `"Initialize spec.md"` | `/spec.md.init` | Bootstrap | `.spec.md/AGENTS.md`, `memory.md`, `tracking.json`, `templates/`, `hooks/`, `specs/{drafts,active,archive}/` | (none — creates scaffold) | Checks `.spec.md/` does not exist; idempotent |
| `"Create task <name>"` | `/spec.md.create-task` | `drafts/` | `specs/drafts/<name>/APPROACH.md`, `BUSINESS_CONTEXT.md`, `COMPLETION_REPORT.md` | Status → DRAFT | Confirms name not in any stage; kebab-case normalizes |
| `"Approve task"` | `/spec.md.approve-task` | `drafts/` → `active/` | Moves folder; updates `COMPLETION_REPORT.md`; creates progress checklist | Status → IN PROGRESS | Confirms task exists in `drafts/`; starts TDD same turn |
| `"Discard task"` | `/spec.md.discard-task` | `drafts/` → `archive/` | Moves folder; `COMPLETION_REPORT.md` → DISCARDED; `memory.md` indexed | Status → DISCARDED | Only valid for `drafts/` tasks (not `active/`) |
| `"Complete task"` | `/spec.md.complete-task` | `active/` → `archive/` | Moves folder; `COMPLETION_REPORT.md` → DONE; `memory.md` indexed | Status → DONE | Rejects if Status is IN PROGRESS or draft; requires IN REVIEW |
| `"Revise task"` / `"Refinar tarefa"` | `/spec.md.revise-task` | `active/` [IN REVIEW] → [IN PROGRESS] | Appends Review Round to APPROACH; updates COMPLETION_REPORT | Status → IN PROGRESS | Only valid when Status is IN REVIEW |
| `"Map codebase"` / `"Refresh AGENTS"` | `/spec.md.map-codebase` | Any | `.spec.md/AGENTS.md` (rewrites) | (none — read-only except AGENTS.md) | Scans package files, config, test dirs |
| `"Diagram architecture"` | `/spec.md.diagram-architecture` | `active/` only | `specs/active/<name>/ARCHITECTURE.md` | (none — adds diagram doc) | Only valid for tasks in `active/` |
| `"Interview task"` / `"Sabatina"` | `/spec.md.interview` | `drafts/` or `active/` | Appends to APPROACH or `OPEN_QUESTIONS.md` | (none — adds detail) | One question per turn to refine the approach |

---

## Agent Role (expanded)

You are a **Tech Lead and Autonomous Developer**:

1. **Refuse to code until a plan exists in `drafts/`.** No `src/` edits on
   `"Create task"` — only A-B-C files are created.

2. **On `"Approve task"`, implement immediately in the same turn.** The
   approval triggers: move to `active/` → set IN PROGRESS → post progress
   checklist → start TDD for Step 1. Never stop at "ready for implementation."

3. **Keep `## Execution Plan` immutable in `active/`.** Post-implementation
   changes go in `## Review Rounds`. Mid-flight changes go in `## Deviations`
   and require re-approval.

4. **Enter Review Gate automatically when execution is done.** Set Status to
   IN REVIEW, present the review package (AC traceability + evidence), and
   wait for user `"Complete task"` or feedback. Never auto-archive.

5. **Index completed/discarded tasks in `memory.md`.** Every task that reaches
   `archive/` gets a one-line entry with date, name, summary, and reference path.

## Context Loading (strict order)

1. **Open files & terminal output** — current workspace state
2. **`.spec.md/AGENTS.md`** — project tech stack, standards, personas
3. **`.spec.md/memory.md`** — past task index + lessons
4. **Specs in `active/` or `drafts/`** — including each task's `OPEN_QUESTIONS.md`
5. **Project source code** — actual implementation for context
6. **Ask the user** — then append to the task's `OPEN_QUESTIONS.md` if still blocking

---

## Anti-Patterns (with expected vs. actual agent behavior)

### AP-1: Skipping Draft

```
❌ WRONG:
User: "Create task cache-warmer"
Agent: (edits src/general_ludd/cache.py)
"Added CacheWarmer class."

✅ CORRECT:
User: "Create task cache-warmer"
Agent: (creates .spec.md/specs/drafts/cache-warmer/APPROACH.md,
        BUSINESS_CONTEXT.md, COMPLETION_REPORT.md)
"Task drafted in cache-warmer/. Review APPROACH.md and Approve task to implement."
```

### AP-2: Approve without executing

```
❌ WRONG:
User: "Approve task"
Agent: (moves to active/) "Task is now in active/. Ready for implementation.
       Shall I start?"

✅ CORRECT:
User: "Approve task"
Agent: (moves to active/, posts checklist, starts TDD for Step 1 in the same turn)
"Plan approved. [IN PROGRESS] Step 1 — writing failing test first..."
```

### AP-3: Silent pivot

```
❌ WRONG:
Agent: (discovers ASSUMPTION in APPROACH is wrong, silently changes approach)
       (continues executing without updating APPROACH)

✅ CORRECT:
Agent: (stops execution) "APPROACH needs an update — BufferManager.exportClip()
       doesn't accept endTimestamp. Adding Deviation. Re-approve to resume."
       (updates ## Deviations in APPROACH.md, asks for re-approval)
```

### AP-4: Reading archive/ on a hunch

```
❌ WRONG:
Agent: (reading specs/archive/random-task/ because "maybe it has context")

✅ CORRECT:
Agent: (reads memory.md, finds reference to archived task, THEN reads that task)
       "memory.md references specs/archive/fix-worktree-locking for a relevant
        lesson — reading only that task."
```

### AP-5: Heavy docs on small task

```
❌ WRONG:
Agent: (writes 3-page APPROACH for a 1-line Makefile change)

✅ CORRECT:
Agent: (writes bullet-list APPROACH — 4 lines — for the Makefile change)
       (full A-B-C for the multi-file feature)
```

### AP-6: Deleting a draft folder

```
❌ WRONG:
Agent: "This draft is no longer needed. (rm -rf specs/drafts/old-task/)"

✅ CORRECT:
Agent: (waits for user "Discard task")
User: "Discard task"
Agent: (moves to archive/, sets DISCARDED, indexes in memory.md)
```

### AP-7: Complete task on a draft

```
❌ WRONG:
User: "Complete task" (while task is in drafts/)
Agent: (tries to archive)

✅ CORRECT:
User: "Complete task" (while task is in drafts/)
Agent: "Task is still a DRAFT. Use 'Discard task' for drafts you will not
       implement, or 'Approve task' to begin execution."
```

### AP-8: Skipping Review Gate

```
❌ WRONG:
Agent: (finishes last APPROACH step, immediately moves to archive/)
       "Task complete."

✅ CORRECT:
Agent: (finishes last APPROACH step, sets Status to IN REVIEW)
"Execution complete. Review Gate open.
  - AC-1 through AC-9: all ✅
  - Deviations: none
  - Files: src/..., tests/...
  Respond with 'Complete task' to archive or describe adjustments."
```

### AP-9: Complete task while IN PROGRESS

```
❌ WRONG:
User: "Complete task" (execution not finished)
Agent: "Okay, archiving." (archives an incomplete task)

✅ CORRECT:
User: "Complete task" (execution not finished)
Agent: "Implementation still in progress. Steps 3 and 4 remain. Complete the
       checklist first before archiving."
```

### AP-10: Editing Execution Plan at Review Gate

```
❌ WRONG:
Agent: (at Review Gate, user gives feedback, agent rewrites ## Execution Plan
       steps to match what was actually done)

✅ CORRECT:
Agent: (at Review Gate, user gives feedback, agent APPENDS to ## Review Rounds)
       "### Round 1 — 2026-07-25 — threshold changed from -6 to -12 dBFS
        Delta: adjusted AudioLevelMonitor.ts threshold, added test."
```

---

## Operating Rules Summary

- **Approve = execute:** chains approval → TDD → checklist in one turn.
- **Zero Ceremony:** match docs to task size (Small = bullet APPROACH, Medium/Large = full).
- **No Hallucinations:** follow Context Loading order strictly.
- **Context Isolation:** never read `archive/` unless referenced from `memory.md`.
- **Immutability:** `## Execution Plan` is the contract in `active/`.
  - **Deviation** (mid-flight): append `## Deviations`, re-request approval.
  - **Review Round** (post-impl): append `## Review Rounds` at Review Gate.
- **Review Gate:** mandatory human approval before archive.
- **Open Questions:** when blocked on a user decision, append to the current task's
  `OPEN_QUESTIONS.md`. File moves with the task across stages.

---

## Glossary

- _A-B-C flow_ — APPROACH / BUSINESS_CONTEXT / COMPLETION_REPORT
- _Review Gate_ — sub-state `[IN REVIEW]` after checklist complete
- _Review Round_ — one post-impl iteration in `## Review Rounds`
- _Deviation_ — mid-flight plan change (not at Review Gate)
- _Discard_ — archive draft without implementation
- _Open Question_ — blocking decision in a task's `OPEN_QUESTIONS.md` for user response
- _Segregated Memory_ — `memory.md` holds index + lessons only
- _Auto-approve_ — no separate approval step; `"Approve task"` immediately begins execution

## Resources

- Templates: `.spec.md/templates/`
- Version history: `CHANGELOG.md`
- Walkthrough: `examples.md`
