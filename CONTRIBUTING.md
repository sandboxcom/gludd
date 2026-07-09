# Contributing

This guide is the operational contract for **both AI executors and human
contributors** working in this repo. It distills the binding rules from
`AGENTS.md` (the full agent policy) and the Makefile target catalogue into one
place. When this document and `AGENTS.md` disagree, `AGENTS.md` wins — fix this
document.

A separate `docs/CONTRIBUTING.md` covers the Ansible collection layout (roles,
modules, molecule scenarios). This document covers process, shell, commit, and
guardrail policy.

References to the authoritative source:
- `AGENTS.md § Mechanical Contract` — the 10-rule summary that governs agent behavior.
- `AGENTS.md § Bash Command Policy`, `§ TDD Policy`, `§ Commit-After-Green Policy`,
  `§ No-Commit-Bypass Policy`, `§ Don't Push Every Commit`.
- `docs/CLAUDE.md` — harness-specific rules mirrored from `AGENTS.md`.

---

## 1. Quick start

Set up the project for the first time:

```sh
make init        # create dirs + install deps via uv
make sync        # resync uv dependencies against pyproject.toml
make bootstrap   # init + lint + test + healthcheck (smoke-tests the install)
```

Day-to-day, the smallest loop is:

```sh
make collect-check   # collection-error gate (fast; run before every commit)
make lint            # ruff
make typecheck       # mypy
make test-iso TESTFILE=tests/unit/test_foo.py   # isolated single-file test run
```

If any of those fail, the work is not ready to commit. See §3.

---

## 2. Shell policy — `make` targets only

**Every shell command in this repo MUST be `make <target>`.** Bare commands
(`ls`, `git`, `python`, `uv`, `find`, `cat`, `rm`, …) are denied by
`opencode.json` permission rules and by `.opencode/plugin/enforce-make.ts`.

The following shell metacharacters are **forbidden** because they chain commands
and bypass the make layer:

| Char | Name | Char | Name |
|---|---|---|---|
| `\|` | pipe | `$()` | command substitution |
| `;` | semicolon | `` ` `` | backtick |
| `&&` | and | `>` `<` | redirect |
| `\|\|` | or | `2>&1` | stderr redirect |
| `()` | subshell | `{}` | brace expansion |
| `!` | history expansion | | |

If you need any of these, **add a Makefile target** that uses them internally.
Make recipes are the one place metacharacters are allowed.

Blocked (violations):
- `make test-unit 2>&1 | tail -20`
- `cd /foo && make test`
- `make test; make lint`
- `$(cat file)`
- `.venv/bin/python -m pytest ...`

Enforced at three layers (per `AGENTS.md § Guardrail Policy`, every guardrail
has all three):
1. `opencode.json` permission rules — hard deny on non-make bash.
2. `.opencode/plugin/enforce-make.ts` — blocks metacharacters + non-make
   commands, and blocks long foreground ops (`make gate`, bare `make test`).
3. This document + `AGENTS.md § Bash Command Policy` — proactive instruction.

---

## 3. Commit flow

The canonical atomic commit is `make ship-commit`, which runs `collect-check`
→ `git commit` → `batch-push` under the commit lock. Use it for routine work:

```sh
make git-add FILES='src/path/to/file.py tests/unit/test_file.py'
make ship-commit MSG='fix(scope): what changed and why'
```

For staged-only commits (no push), use `make git-commit MSG='...'`. For
test-gated commits, use `make test-and-commit MSG='...'` (runs pytest inline,
its own micro-gate).

**Before every commit**, the gate freshness+green check must hold:

1. `make collect-check` — 0 collection errors. (No commit if non-zero.)
2. `make lint` — ruff clean (or run `make lint-fix`).
3. `make typecheck` — mypy within baseline.
4. `make test-iso TESTFILE=<your-test>` — the test for the change passes.

The recommended pre-commit confidence loop is:

```sh
make gate-lite        # local fast feedback (lint + typecheck + collect + unit @2 workers)
make collect-check
make ship-commit MSG='...'
```

`make gate-lite` writes `.gate-lite-status` but is **not** the gate of record.
The commit-time `_gate-fresh-check` requires a fresh green `make gate` (or the
inline test run from `test-and-commit`). For full pre-commit validation, run
`make gate-background` (see §7) and poll `make gate-status-check` from a subagent.

### Pushing

**Do not push on every commit.** Each push cancels in-flight CI runs; pushing
in a loop turns CI into a cancellation daemon, not a gate. Rules:

- `make batch-push` is the sanctioned push (threshold: 5+ unpushed commits, or
  `COMMIT_THRESHOLD=1` to push immediately). `ship-commit` calls it for you.
- Validate locally first — `gate-lite` + targeted tests are the real gate.
- One CI run in flight at a time. The `_push-rate-guard` blocks pushes when CI
  is pending, within 30 min of the last push, or after 3+ cancelled runs in 2h.
- After pushing, verify the remote advanced: `make verify-remote
  BRANCH=<branch> SHA=<local-HEAD>` prints `VERIFIED <branch>@<sha>` on success.

---

## 4. TDD — failing test first

Every change follows the red-green cycle. No exceptions.

1. Identify the behavior you need.
2. Write a test that fails because the behavior does not exist yet.
3. Run `make test-iso TESTFILE=tests/unit/test_<thing>.py` — confirm it fails.
4. Write the minimal implementation that makes it pass.
5. Run the same test — confirm it passes.
6. Refactor with the test green.

Do not write implementation and then retroactively add tests. Do not mark work
done unless a test proves the behavior exists.

Enforced by:
- `.opencode/plugin/enforce-make.ts` — prints a TDD reminder when you edit
  files under `src/`.
- `AGENTS.md § TDD Policy`.
- The `test-quality` skill (10 rules every test must follow).

---

## 5. Branch workflow

Small, atomic commits land on a feature branch and merge with `--no-ff`:

```sh
make feature-start MSG='feature/short-name'   # create + switch
# ... commit small green increments onto the branch ...
make feature-done  MSG='feature/short-name'   # test, merge to master with --no-ff
```

- One feature per branch. One logical change per commit (one test file, one
  fix, one module).
- Never force-push. Never mutate `master`/`main`/`release/*` from a
  worktree-isolated agent — shared-branch mutations happen on the main checkout
  only (see `AGENTS.md § Branch-landing integrity`).
- Release branches are immutable once their remote tip is CI-green. Use
  `make release-branch-new` and `make release-promote` — never hand-craft tags.

---

## 6. Guardrails — what they enforce

Every guardrail here exists because a past session demonstrated a specific
failure mode. Removing one to silence a warning is itself a bug — narrow the
check instead (see `AGENTS.md § Guardrail Integrity Policy`).

### The gate

`make gate` is the full gate: lint + typecheck + collect-check + tests. It
writes `.gate-status` (PASS/FAIL). **The gate exit code is the single source
of truth — not `SESSION.md`, not self-assessment.**

- `make gate-background` — launches the gate via `nohup`; returns in <1s.
  Writes its log under `.gate-logs/` and its PID to `.gate-background.pid`.
- `make gate-status-check` — non-blocking status probe (running? phase? terminal
  marker? `.gate-status`?).
- `make gate-tail` / `make gate-logs` / `make gate-kill` — follow, list, stop.

Long-running gates must never run in the foreground on the main thread — they
block all parallel dispatch. See `AGENTS.md § Background Operations NEVER Block
Dispatch`.

### Hooks + plugins

The Claude Code shell-hook layer (`.claude/hooks/*.sh`, registered in
`.claude/settings.json`) and the opencode TypeScript layer
(`.opencode/plugin/*.ts`, registered in `opencode.json`) **enforce the same
policies in parallel**. A session in either harness gets the same guardrails.

| Opencode plugin | Enforces |
|---|---|
| `enforce-make.ts` | make-only bash, no metacharacters, concurrent-gate block, `.gate-status` write block, guardrail-integrity across all hook/plugin files. |
| `enforce-floor.ts` | ≥10 live subagents (env `CLAUDE_AGENT_FLOOR`); blocks stops when below the floor. |
| `enforce-delegate.ts` | sonnet-dominant dispatch ratio, worktree disk guards, opt-in force-delegate grind guard, main-thread delegation budget. |
| `enforce-stop.ts` | deferral-pattern (no-wait) block, open-backlog block, session-start orchestration, `AskUserQuestion` deny (no blocking questions), false-done-claim block. |
| `enforce-session-start.ts` | session-start directive at boot + turn-1 read gate. |
| `enforce-deadline.ts` | 5-min wall-clock cap per dispatched task; records overruns. |
| `enforce-deletion-gate.ts` | file-deletion approval gate. |
| `enforce-no-suppressions.ts` | denies `# noqa`, `# type: ignore`, `# pylint: disable`, `# fmt: off/skip`, `# isort:skip` on edit/write. |
| `enforce-no-wait.ts` | denies `sleep N && make …`, `make gate-tail`, and CI-poll subagent dispatches. |
| `enforce-verified-claims.ts` | blocks outgoing text with done-words unless it carries machine-produced evidence. |
| `enforce-clean-tree.ts` | denies task/agent/workflow dispatch when the git working tree is dirty. |
| `enforce-multitask.ts` | denies dispatch waves below the 10-agent floor when ≥3 work items remain. |
| `enforce-commit-lock.ts` | serializes commit-shaped make targets so parallel subagents cannot race the git index. |
| `watchdog.ts` | background daemon that detects idle sessions and auto-resets streak counters. |

Opt-out knobs (`GLUDD_FLOOR_ENFORCE`, `GLUDD_NO_WAIT_ENFORCE`,
`GLUDD_SESSION_START_ENFORCE`, `GLUDD_TODO_GUARD_ENFORCE`,
`GLUDD_VERIFIED_CLAIMS_ENFORCE`, `GLUDD_CLEAN_TREE_ENFORCE`,
`GLUDD_MULTITASK_FLOOR_ENFORCE`, `GLUDD_COMMIT_LOCK_ENFORCE`, …) exist for
focused single-file work. They are **off by default** — never disable a
guardrail to work around friction; narrow the check instead.

### Multitasking floor — 10 agents minimum

The subagent pool must stay at a **minimum of 10 concurrent threads** while
work remains. The pipeline is kept primed by dispatching a replacement the
moment any subagent completes — never draining to zero before refilling.

- Every assistant response containing tool calls must satisfy ONE of: (a) zero
  task/agent/workflow dispatches (pure read/edit/bash, max 2 consecutive), or
  (b) **10+ parallel dispatches in a single message**. A wave of 1–9 dispatches
  is denied when ≥3 work items remain — batch wider or add read-only research
  filler to reach the floor.
- Enforced by `.opencode/plugin/enforce-multitask.ts` (`MIN_DISPATCHES` default
  10, env `GLUDD_MULTITASK_MIN_DISPATCHES`). Below-floor waves are denied; a
  `text.complete` hook injects "DISPATCH SUBAGENTS NOW" when the agent tries to
  stop with unchecked `TASKS.md` items and zero subagents in flight.
- Env `CLAUDE_AGENT_FLOOR` / `GLUDD_MULTITASK_FLOOR_ENFORCE` tune the behavior.
  See `AGENTS.md § Minimum 10 Subagents at All Times` and `§ Pipeline
  Orchestration Model`.

### Verification before claim

**Never write `done` / `landed` / `pushed` / `fixed` / `passing` / `shipped` /
`green` without pasting the machine-produced measurement in the same message.**
A status word without its evidence is indistinguishable from a false claim.

- Run `make verify-state` before any status claim — it bundles `git status` +
  `git log` + HEAD-vs-remote + CI verdict into one read-only output.
- Enforced by `.opencode/plugin/enforce-verified-claims.ts` (`text.complete`
  hook): outgoing text containing done-words is blocked unless it also carries
  an evidence token (commit hash, `VERIFIED <branch>@<sha>`, `CI GREEN|RED|
  PENDING`, `N passed`, `=== GATE: PASSED ===`, `Collection OK`). Fail-open;
  `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` disables.
- See the false-"done"-claims table in §7 and `AGENTS.md § Verification Before
  Claim`.

### Clean tree before dispatch

**Never dispatch a subagent while the git working tree is dirty.** Uncommitted
changes left by a prior subagent cause pre-commit stash conflicts on the next
push, forcing `-nv` (no-verify) bypasses that defeat the lint/secret guards.

- Commit or stash first: `make git-add FILES='...' && make ship-commit
  MSG='...'`, or `make git-stash` (restore with `make git-stash-pop`).
- Enforced by `.opencode/plugin/enforce-clean-tree.ts` (`tool.execute.before`):
  task/agent/workflow dispatch is denied when `git status --porcelain` is
  non-empty. Fail-open; `GLUDD_CLEAN_TREE_ENFORCE=0` disables.
- File-editing subagents get their own isolated checkout via the
  `agent-worktree` targets — `make agent-worktree BRANCH=agent-<name>` (create),
  `make agent-merge BRANCH=agent-<name>` (fan-in `--no-ff`), `make agent-cleanup
  BRANCH=agent-<name>` (remove), `make agent-worktree-list` (diagnostic).
  Read-only research tasks stay on the main checkout. See `AGENTS.md §
  Worktree-per-subagent`.

### CI cooldown — fire-and-forget

Polling CI in a loop does not speed it up and burns a dispatch slot. CI is
checked at **natural breaks**, never watched.

- `make deploy-and-forget` pushes, records the push timestamp, and prints a
  check-back time. Resume real work immediately after it returns.
- `make ci-verdict-safe` is the routine CI status check — it enforces a 10-min
  cooldown (`CI_CHECK_COOLDOWN_SEC`, exit 3 while cooling down). Bare `make
  ci-verdict` is reserved for the release-cut pipeline; `FORCE=1` bypasses the
  cooldown for release-cut only.
- `make ci-cooldown-status` is a read-only probe of the remaining cooldown.
- A "poll CI until terminal" subagent is forbidden — `enforce-no-wait.ts`
  denies CI-poll dispatch patterns. See `AGENTS.md § Machine-Enforced CI Check
  Cooldown` and `§ CI-Poll Subagents Are Forbidden`.

### Commit lock — serialized commits

Parallel subagents running `make ship-commit` race on the git index — one
`git add -A` sweeps another's staged files, producing misattributed commits.

- Commit-shaped make targets acquire a lock so commits serialize. Do not run
  two `ship-commit`s concurrently from different subagents.
- Enforced at two layers: LAYER 1 is the Makefile `_commit-lock-acquire` flock
  wrapper; LAYER 2 is `.opencode/plugin/enforce-commit-lock.ts`, which takes an
  `O_EXCL` lock across the whole bash tool call and denies a second in-flight
  commit (stale locks older than 5 min are auto-broken). Fail-open;
  `GLUDD_COMMIT_LOCK_ENFORCE=0` disables.
- Funnel commits through a single integrator agent, or stagger them. See
  `AGENTS.md § Pipeline Orchestration Model`.

---

## 7. Common pitfalls

### False "done" claims

Never write `done` / `shipped` / `fixed` / `landed` / `✅` without pasting the
measurement that proves it. Authorship is not verification. (See `AGENTS.md §
"'Done' Claims Require Observable Verification Evidence"`.)

| Scope | Required evidence |
|---|---|
| Unit fix | Named passing test + `make test TESTFILE=...` pass count. |
| Local gate | `make gate` green + `.gate-status` PASS. |
| Commit | Commit hash from `make git-log`. |
| Push | `make verify-remote BRANCH=… SHA=…` → `VERIFIED …`. |
| CI green | `make ci-verdict BRANCH=…` → `conclusion: success` + matching headSha. |
| Release | `make verify-release-artifact TAG=…` PASS + `gh release view`. |

### CI polling

Do not poll CI in a loop. CI runs on its own schedule; watching it does not
speed it up and burns a dispatch slot.

- Use `make ci-verdict-safe` (default 10-min cooldown) for routine status.
  Bare `make ci-verdict` is reserved for the release-cut pipeline.
- `make ci-wait` is for `make release-cut` only — never use it as a general
  "block until green" tool.
- After `make batch-push`, resume real work immediately; check CI at the next
  natural break, not in a poll loop.

### Parallel commits

`make ship-commit` acquires the commit lock to serialize the commit+push step.
Do not run two `ship-commit`s concurrently from different subagents — one will
fail to acquire the lock. Stagger them, or funnel commits through a single
integrator agent (see `AGENTS.md § Pipeline Orchestration Model`).

### Other recurring traps

- `make test-failures` historically masked collection ERRORs. Trust the full
  `make test` output; always run `make collect-check` before committing.
- Do **not** trust `SESSION.md` status claims — verify with the gate.
- "Pre-existing failures" are the work, not an excuse to bypass the gate.
  The `commit-no-verify` target exists for pre-commit stash conflicts only —
  not for skipping the gate.
- `make disengage-enforcement` is a one-shot emergency escape when all plugins
  are blocking legitimate work. Use it once, fix the offending plugin code,
  `make write-plugin-manifest`, and restart opencode. It is not a routine
  shortcut.

---

## 8. Target catalogue

The 30 most-used `make` targets. The full list lives in the `Makefile`; run
`make help` (if defined) or read the Makefile for the rest.

### Setup
| Target | What it does |
|---|---|
| `make init` | Create dirs + install deps (uv). |
| `make sync` | Resync uv dependencies against `pyproject.toml`. |
| `make bootstrap` | `init` + lint + test + healthcheck. |
| `make clean` | Remove build artifacts. |

### Testing
| Target | What it does |
|---|---|
| `make test` | Full suite with coverage. (Foreground — prefer `gate-background`.) |
| `make test-unit` | Unit tests only. (Foreground — use `make test-iso TESTFILE=...` for one file.) |
| `make test-e2e` | End-to-end tests. |
| `make test-iso TESTFILE=path` | Isolated single-file pytest run (isolated tmpdir, no cache). |
| `make test-xdist TESTFILE=path` | Same as `test-iso` but with the gate's xdist flags (-n 2 --dist loadgroup). |
| `make test-count` | Collection-error count (must be 0 before every commit). |
| `make test-guardrails` | Test the guardrail infrastructure itself. |
| `make test-and-commit MSG='...'` | Run pytest inline; commit only if green. |

### Quality
| Target | What it does |
|---|---|
| `make lint` | ruff. |
| `make lint-fix` | ruff with auto-fix. |
| `make typecheck` | mypy. |
| `make collect-check` | Fast collection-error gate. |
| `make healthcheck` | Verify imports work. |
| `make qa` | lint + typecheck + test + healthcheck. |
| `make validate` | Full validation incl. ansible syntax. |
| `make gate` | Full gate (lint + typecheck + collect + test); writes `.gate-status`. |
| `make gate-lite` | Local fast gate; writes `.gate-lite-status` (not the gate of record). |

### Background gate
| Target | What it does |
|---|---|
| `make gate-background` | Launch `make gate` via `nohup`; returns in <1s. |
| `make gate-status-check` | Non-blocking status probe. |
| `make gate-tail` | Live tail of the latest gate log. |
| `make gate-kill` | SIGTERM → SIGKILL the background gate. |

### Git (use ONLY these — never raw `git`)
| Target | What it does |
|---|---|
| `make git-status` / `git-diff` / `git-staged` / `git-log` | Read-only git views. |
| `make git-add FILES='f1 f2'` / `git-add-all` | Stage changes. |
| `make ship-commit MSG='...'` | `collect-check` → commit → `batch-push` under the lock. |
| `make git-commit MSG='...'` | Commit staged changes (no push). |
| `make feature-start MSG='feature/x'` | Create + switch to a feature branch. |
| `make feature-done MSG='feature/x'` | Test + merge feature branch with `--no-ff`. |

### CI / release
| Target | What it does |
|---|---|
| `make batch-push` | Sanctioned push (5+ commit threshold; 3-layer rate guard). |
| `make ci-verdict-safe` | CI status with 10-min cooldown (routine checks). |
| `make verify-remote BRANCH=… SHA=…` | Assert remote tip matches expected SHA. |
| `make release-cut TAG=… MSG='…'` | The only sanctioned release command (CI-green gate → tag → release-view). |

---

## 9. Where to read more

- `AGENTS.md` — the full, binding agent policy. This document is a digest of it.
- `docs/CONTRIBUTING.md` — Ansible collection layout (roles/modules/molecule).
- `docs/STABILIZATION_PLAN.md` — the work-package plan this file was cut from (WP-F2).
- `docs/CLAUDE.md` — harness-specific rules mirrored from `AGENTS.md`.
- `TASKS.md` / `BUGS.md` — current task ledger + incident history.
