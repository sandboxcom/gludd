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
| `watchdog.ts` | background daemon that detects idle sessions and auto-resets streak counters. |

Opt-out knobs (`GLUDD_FLOOR_ENFORCE`, `GLUDD_NO_WAIT_ENFORCE`,
`GLUDD_SESSION_START_ENFORCE`, `GLUDD_TODO_GUARD_ENFORCE`, …) exist for
focused single-file work. They are **off by default** — never disable a
guardrail to work around friction; narrow the check instead.

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
