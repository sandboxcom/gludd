# GLM Remediation Guide — Restore, Verify, Finish, and Guard

> **Audience:** the coding agent (GLM 5.1, Qwen, or DeepSeek) running under opencode in this repo.
> **Author:** independent validation pass, 2026-06-10. Supersedes the *status claims* in `SESSION.md` and continues `GLM_IMPLEMENTATION_GUIDE.md`.
> **Single-prompt usage:** if this file is your only instruction, execute it top to bottom: Phase R0, then R1, then R2, then R3. Do not skip, do not reorder, do not stop until the Section 9 checklist is fully ticked.

---

## 0. Mechanical rules (read first; follow literally)

1. **Only run `make <target>`.** Never `uv`, `pytest`, `python`, `git`, `ls`, `cat`, pipes, `;`, `&&`. Allowed targets are listed in `GLM_IMPLEMENTATION_GUIDE.md` Section 2 and `AGENTS.md`.
2. **Do not trust `SESSION.md`.** It claims "ALL items COMPLETE" and cites latest commit `6d312d2` — that commit does not exist in `make git-log`. The real state is Section 1 of this file, verified against code and gate output on 2026-06-10.
3. **TDD:** failing test first, then code, then `make qa`. Exception: Phase R0 repairs a broken build — there, run the named gate target after each fix instead (the suite cannot collect yet, so no new test can run first).
4. **One task = one commit** via `make git-add FILES='...'` + `make git-commit MSG='<task-id>: ...'`. Run `make test-count` before EVERY commit — if collection errors, you may not commit.
5. **Evidence or it didn't happen.** A task is done only when you paste the exact `make` output proving it into `TASKS.md` (created in R1.4). Claims without a pasted gate result are violations.
6. **Respond and reason in English. Keep reasoning short.** When editing, open the file and find the symbol first; line numbers below are approximate.

---

## 1. Validation results — what the last session actually did

### 1.1 Verified true state (2026-06-10, this validation run)

| Gate | Claimed (SESSION.md) | Actual (observed) |
|------|----------------------|-------------------|
| `make test` | "all green, e2e proof passes" | **0 tests run. 32 collection errors, pytest interrupted in 2.95s** |
| `make test-failures` | — | "No failures" — **false green** (it greps `^FAILED` only; collection `ERROR`s are invisible) |
| `make lint` | "0–2 cosmetic errors" | **1 error** (RUF006, `src/general_ludd/execution/engine.py:288`) |
| `make typecheck` | "0 errors strict" | **49 errors in 17 files** (baseline was 25 in 5 — regression of +24) |
| `make healthcheck` | OK | OK (but it never imports `daemon.py`, which is broken) |
| Latest commit | `6d312d2` "S20 final fixes" | `2272bc2` "feat: H5 subagent dispatcher…" — **`6d312d2` does not exist** |
| coverage `fail_under` | "Phase 4 done" | still **10** (`pyproject.toml:112`) |

### 1.2 Root cause of the broken build

`src/general_ludd/skills/loader.py:6` and `src/general_ludd/skills/fetcher.py:11` import `general_ludd.skills.models`, **which was never created**. The `Skill` class actually lives in `src/general_ludd/skills/skill.py` (and `skills/__init__.py:27` imports it from there). A half-finished refactor was committed without running collection. Because `daemon.py:87` imports the skills package, **the daemon itself cannot import**, and 32 test modules error at collection, so pytest aborts before running anything.

### 1.3 Claimed-done items that are NOT done (verified in code)

| Item | Claim | Reality (evidence) |
|------|-------|--------------------|
| **H5** | "subagent dispatcher instantiated in daemon lifespan" (commit 2272bc2) | `AgentDispatcher.__init__` takes `(registry, executor)` — `daemon.py:482` calls it with `model_gateway=`/`session_factory=` kwargs that don't exist → **TypeError at startup**, swallowed by the lifespan's broad `except` (C6). Default executor still returns `""`. |
| **M7** | "worktree monitor created" (commit e2c77be) | `WorktreeMonitor.__init__` takes `(config, scanner, todo_creator)` — `daemon.py:476` passes `config_dir=` → **TypeError at startup**. |
| **S2** | "AutoBenchmarkRecorder wired" (commits 892e3e6, 343cc4a) | `daemon.py:471` passes an `async_sessionmaker` where `BenchmarkRepository` expects an `AsyncSession`; the recorder call in `engine.py:288` is the open lint error. Unverified by any passing test (suite doesn't collect). |
| **S14** | "Alembic stamp head after SQLite create" | `daemon.py:367` imports `stamp_head` from `db/migrations.py` — **the function does not exist**, and `get_alembic_config()` takes no args but is called with one. The `except` at `daemon.py:371` logs at DEBUG, so this fails silently on every startup. |
| **M1** | "S20 fixed" | `ansible/core_runner.py:195,213` — `_collected_events` initialized empty, never populated; no callback plugin registered. |
| **M6** | "S20 fixed" | `routers/reload.py:75` creates a NEW `AnsibleRunnerAdapter()` instead of refreshing the EventLoop's runner on `app.state`. |
| **M13** | "S20 fixed" | `config/user_config.py:15-22` — UserConfig still has no `secrets`, `projects`, `compute_endpoints`, `rules`, `quality_gates` fields; `daemon.py:438` does `getattr(uc, "queues", [])` for a field that doesn't exist (always `[]`). |
| **M12** | "S20 fixed" | PARTIAL — `pid_outputs` consumed (`loop.py:451`) but `active_jobs` hardcoded 0 (`loop.py:448`) and the `queues` config is always empty (see M13). |
| **M10** | "S20 fixed" | PARTIAL — hardcoded key removed (`integrity/scanner.py:22-36`, env or random fallback), but approvals are still in-memory and nothing reacts to detected changes. |
| **M2/S15** | "deployments real" | PARTIAL — singleton manager on `app.state` exists, but `routers/compute.py:43` sets `mgr.private_data_dir`, an attribute `DeploymentManager` doesn't define (mypy error); behavior unverified. |
| **Phase 4** | "complete" | `fail_under = 10`; `SESSION.md` is fiction; dead `# noqa` imports not audited. |

### 1.4 Items that ARE confirmed or plausible (re-verify in R2.6)

- `BASELINE.md` exists with real numbers (Phase 0) — done.
- **M15**: `secrets/manager.py:219` raises `NotImplementedError` — honest, done.
- **M3** (auth env raises), **M11** (code CLI not-yet-implemented message), **M4/S16**, **S13** partials: commits exist and lint/mypy don't contradict them, but **no test has run since they were committed**. Treat as "plausible, unproven."
- G0–G7, S1–S20, F1–F7 code largely exists (e.g. `execution/engine.py`, `tests/integration/test_full_pipeline_e2e.py`) but the entire suite last *ran* before several sessions of changes. **Every one of these must be re-proven by the gate in R2.6, not assumed.**

### 1.5 Blockers carried into this session

1. **Build broken** (Section 1.2) — blocks everything; fix first.
2. **ZAI API 429 (balance exhausted)** — `make test-live-zai` / `test-zai-identity` will fail/skip until the account is recharged. Do NOT treat this as a code bug; live targets are optional evidence only.
3. **TDD plugin friction**: the test-lookup in `enforce-make.ts` blocks legitimate edits to existing src files when no test file name-matches (SESSION.md notes Prometheus work blocked). Fixed properly in R1.6 — do not weaken the guardrail to get past it.
4. **Plugin changes need an opencode restart** to take effect; after editing `.opencode/plugin/enforce-make.ts`, tell the user a restart is needed, then continue with non-plugin work.
5. **Port 8000 collisions** make some e2e daemon tests environment-sensitive; prefer ASGITransport tests.

### 1.6 Why the false "all done" happened (drives Phase R1)

1. **Every completion guardrail inspects the model's prose, not the repo.** Stop-phrase lists and regexes are evaded by phrasing; nothing ever ran a gate and checked its exit code before allowing "done."
2. **`make test-failures` returns success on a suite that cannot even collect** — the agent's own verification tool lied to it.
3. **`make git-commit` has no gate.** Only `test-and-commit` runs tests; every broken commit went through plain `git-commit`.
4. **The injected system prompt is enormous** (hundreds of lines, much of it TUI-specific) — small/mid models (GLM/Qwen/DeepSeek) lose the load-bearing rules in the noise. The original guide itself warns "GLM degrades with verbose prompts."
5. **Stop-pattern detection punishes vocabulary** ("coverage:", "tests pass", "fixed:") — it teaches the model to avoid words rather than to finish work, and it fires on legitimate mid-work narration.
6. **No per-item evidence ledger** — "37 items done" was never reconciled against per-item proof.

---

## 2. Phase R0 — Restore the build (no feature work until green)

### R0.1 Fix the skills import breakage
- Edit `src/general_ludd/skills/loader.py:6` and `src/general_ludd/skills/fetcher.py:11`: import `Skill` from `general_ludd.skills.skill` (where it lives). Do NOT create a new `models.py` — the canonical home already exists and `skills/__init__.py` uses it.
- Check `fetcher.py`'s `TYPE_CHECKING` import of `CatalogSkillEntry` still resolves.
- **Prove:** `make test-count` shows ~5,400+ collected, **0 errors**. `make healthcheck` passes.
- Commit: `make git-commit MSG='R0.1: fix skills.models import breakage; suite collects again'`.

### R0.2 Fix the open lint error
- `src/general_ludd/execution/engine.py:288` — store the `asyncio.create_task` reference (RUF006): add the task to a module/instance-level set with a done-callback discard, same pattern as `events/bus.py` `_background_tasks`.
- **Prove:** `make lint` → 0 errors.

### R0.3 Fix the daemon wiring that calls nonexistent APIs
These are the commits that claimed H5/M7/S2/S14 — make them real. TDD applies again from here (suite collects now).
1. **S14**: in `src/general_ludd/db/migrations.py` implement `stamp_head(cfg)` (alembic `command.stamp(cfg, "head")`) and make `get_alembic_config(url)` accept the URL and set `sqlalchemy.url`. Change the `except` at `daemon.py:371` to log at WARNING. Tests: stamp on fresh SQLite; config carries the composed URL.
2. **M7**: construct `WorktreeMonitor` with its real signature (`WorktreeMonitorConfig`), wire `todo_creator` to `TodoRepository.create`, expose under the `worktree_monitor` subsystem key the router reads. Test: `/admin/worktree/status` returns monitor data.
3. **H5**: construct `AgentDispatcher(registry=..., executor=...)` with a real executor that calls the model gateway (or, if that is out of scope this pass, do NOT instantiate it with fake kwargs — wire it minimally but honestly and write the gap into `TASKS.md`). Test: dispatcher instantiation in lifespan does not raise; executor invokes the gateway (mocked).
4. **S2**: give `BenchmarkRepository` what it actually accepts (session per call from the factory, committed and closed), or fix the constructor properly. Test: after a mocked job, a `BenchmarkResultModel` row is visible from a fresh session.
- **Prove each:** its new unit test passes + `make typecheck` error count strictly decreases.

### R0.4 Typecheck back to ≤ baseline
- Baseline was 25 errors in 5 files (`BASELINE.md`); current is 49 in 17. After R0.3, fix the remainder (routers/compute.py attrs, db/repository.py `updated_at`, cli.py table variance — use `Sequence`, loop.py:273, daemon.py:709, etc.) until **≤ 25, target 0**.
- **Prove:** `make typecheck` output pasted into `TASKS.md`.

### R0.5 Re-baseline and reconcile the 40 known failures
- Run `make test`. Record exact counts in `BASELINE.md` under a new dated section. The previous baseline had 40 named failures — fix them or classify each (env-dependent ports, live-API) with a one-line reason. Goal: **0 unexplained failures**.
- Commit: `R0.5: re-baseline, suite green` (only when true).

**Phase R0 exit gate:** `make qa` fully green (lint 0, mypy ≤ baseline and shrinking, tests pass, healthcheck OK), `make test-count` 0 errors.

---

## 3. Phase R1 — Guardrails that make false "done" impossible

Build these BEFORE resuming feature work. Every guardrail keeps all three layers (permission, hook, prompt) per `AGENTS.md`. **Never weaken an existing guardrail** — these replace prose-detection with state-verification, which is a sharpening.

### R1.1 Make the truth targets honest (Makefile)
- Fix `test-failures`: must surface failures AND errors AND the exit code. Replace the grep with a target that runs pytest `-q`, captures `FAILED|ERROR` lines and the summary line, and **propagates pytest's exit code** (no `|| echo "No failures"` masking).
- Add `collect-check`: `pytest tests/ --co -q`, **fails on any collection error** (this is the fast pre-commit gate).
- Add `gate`: runs lint, typecheck, collect-check, test; writes one machine-readable line per check to `.gate-status` (check name, pass/fail, counts, epoch timestamp) and exits non-zero if any failed. Add `.gate-status` to `.gitignore`.
- Tests (in `tests/unit/test_guardrails.py`): a deliberately broken temp test tree makes `collect-check` exit non-zero; `gate` writes all four lines; `test-failures` exit code is non-zero when pytest fails. (Invoke make via `subprocess` inside pytest — that is the existing pattern in this file.)

### R1.2 Gate commits on repo state, not prose (Makefile + plugin)
- Makefile: `git-commit` and `repo-commit` first run `collect-check` (fast). If collection fails, the commit is refused with the error output. `feature-done` keeps the full suite.
- Plugin (`tool.execute.before`): on `make git-commit`/`make test-and-commit`, read `.gate-status`; if missing, stale (> 30 min), or red, **throw** with: "Run `make gate` first; commit allowed only on a green, fresh gate." This converts the advisory commit-reminder into a hard, state-based gate.

### R1.3 Completion claims verified against the gate (plugin)
- In `experimental.chat.response.transform`: when a completion claim is detected ("complete", "done", "all items", "finished" near task language), do NOT inject more prose. Instead read `.gate-status`:
  - green + fresh → allow the message through unmodified;
  - red/stale/missing → replace the response with one short instruction: "Gate is red/stale. Run `make gate`, fix, and continue. Completion claims are blocked until the gate is green."
- Keep the pending-todo check: text-only response + pending todos → same replacement. Delete the vocabulary-only triggers that fire on normal narration (`"coverage:"`, `"tests pass"`, `"fixed:"`, `"to summarize"`, …): the trigger is now **state** (pending todos / red gate), not words.

### R1.4 Per-item evidence ledger (`TASKS.md`)
- Create `TASKS.md` at repo root: one line per work item in this guide (R0.1 … R3.4, then carry-over items from Section 1.3/1.4), format:
  `- [ ] <ID> — <title> | evidence:` (evidence filled with the exact make target run + its summary line + commit hash).
- Plugin (`tool.execute.before` on edits to `TASKS.md`): an edit that turns `[ ]` into `[x]` without a non-empty `evidence:` on the same line → **throw**.
- Preflight: add a check that fails when any `[x]` line lacks evidence, and when SESSION.md says "complete" for an item that is `[ ]` in TASKS.md.
- Tests for both checks.

### R1.5 Prompt diet for small models (plugin)
- Rewrite `experimental.chat.system.transform` injection to a **maximum of ~40 lines**, front-loaded, numbered, mechanical — no narrative, no TUI history. Required content, in order:
  1. Only `make <target>`; no metacharacters.
  2. Pending todos ⇒ your next output must contain a tool call.
  3. "Done" requires: `make gate` green + `TASKS.md` evidence line. Nothing else counts.
  4. TDD: failing test first; `make test-count` before every commit.
  5. When you find a gap: fix it now, then continue the list.
  6. Don't trust SESSION.md; trust gate output.
  7. Read `TASKS.md` for current work; read `BUGS.md` before claiming anything is finished.
- Move the long-form rationale into `AGENTS.md` (link, don't inline). The TUI-completeness and 79-test sections move to a skill file under `.opencode/skills/` that loads only when TUI work is in scope.
- Rationale: GLM/Qwen/DeepSeek follow short numbered contracts reliably and lose rules buried in 400-line prompts; deterministic hooks (R1.1–R1.4) carry the enforcement so the prompt no longer has to.

### R1.6 Fix TDD-gate friction without weakening it (plugin)
- Current check: name-match on test files (blocks legitimate edits; passes on coincidental name hits). Replace with: the edit is allowed if (a) a test file referencing the module path/symbol exists, or (b) the edit is to a file modified in the current session after a test edit, or (c) the command context is a refactor explicitly logged: editing requires a prior line appended to `TASKS.md` (`refactor-no-behavior-change: <file> — <reason>`). The throw stays for everything else.
- Tests: legit existing-module edit with referencing test passes; no-test edit still throws; logged refactor passes and leaves an audit line.

### R1.7 AGENTS.md update (prompt layer)
- Add a short section "Completion = green gate + evidence", replacing prose-pattern descriptions: a task may be called complete only with `make gate` green, `TASKS.md` evidence, `make test-count` 0 errors. Add: "`make test-failures` previously masked collection errors — if a gate target ever disagrees with `make test`, the FULL `make test` output is the truth, and fixing the gate target is your first task."
- Document the new targets (`gate`, `collect-check`) in the Key Make Targets section.

**Phase R1 exit gate:** all new guardrail tests pass (`make test-guardrails`), `make qa` green, user told an opencode restart is needed for plugin changes. Each R1 item ticked in `TASKS.md` with evidence.

---

## 4. Phase R2 — Finish the items the last session missed

Work top to bottom. TDD. One commit per item. Tick `TASKS.md` with evidence each time.

- **R2.1 (M1)** Register an ansible callback in `core_runner.py` that populates `_collected_events` with real run events; `stats` returns run stats. Test: running the noop playbook yields non-empty events.
- **R2.2 (M6)** `/admin/playbooks/refresh` refreshes the EventLoop's runner via `app.state._runner` (same instance — `daemon.py:446-447` already stores it). Test: refresh changes what the loop's runner resolves.
- **R2.3 (M13)** Add the missing `UserConfig` fields (`secrets`, `projects`, `compute_endpoints`, `rules`, `quality_gates`, `queues`) wired to their existing consumers (S3/S6/S8/S12 paths), or delete the unconsumed sections from `config/general-ludd.yml`. No documentation fiction. Test: each retained section round-trips from YAML to its consumer.
- **R2.4 (M12)** With `UserConfig.queues` real (R2.3), feed real `active_jobs` (count of ACTIVE todos this tick) into the PID snapshot; dispatch consults `pid_outputs` to cap claims. Test: high load output caps per-tick claims.
- **R2.5 (M10 remainder)** Persist integrity approvals (DB or file), emit an event/todo on detected change. Test: approval survives a new scanner instance.
- **R2.6 Re-verify every previously claimed item** (G0–G7, S1–S20, F1–F7, M2–M15): for each, run its named proof (the test files listed in `GLM_IMPLEMENTATION_GUIDE.md` per item; the spine proof is `tests/integration/test_full_pipeline_e2e.py`) and record pass output in `TASKS.md`. Items whose proof fails get fixed here, ordered C-items → H-items → M-items. **Special attention** (claims contradicted or untested): S15/M2 (`private_data_dir` attr), S13 contract tests, S12 vault round-trip, F1–F7 lifespan integration (SESSION.md itself admits "Integrate new modules (PRDelivery, ToolCallLoop, BudgetManager, etc.) into daemon lifespan" was never done).

---

## 5. Phase R3 — Honesty pass

- **R3.1** Rewrite `SESSION.md` from scratch: real gate numbers, real latest commit (from `make git-log`), per-phase status pointing at `TASKS.md` evidence. Delete every unproven "COMPLETE."
- **R3.2** Raise `fail_under` in `pyproject.toml` from 10 to (observed coverage − 2). Test: `make test` still passes.
- **R3.3** Log this incident in `BUGS.md` (entry exists from the validation pass — extend it with what you fixed and which guardrail now prevents it).
- **R3.4** `make validate` green. Final commit.

---

## 6. Definition of Done (every task, no exceptions)

1. Failing test written first, now passing (named in evidence).
2. `make test-count` → 0 errors. `make gate` → green. (`make qa` acceptable until `gate` exists in R1.1.)
3. Behavior manually confirmed, not just asserted.
4. `TASKS.md` line ticked with evidence (target + summary line + commit hash).
5. Committed via `make git-commit MSG='<ID>: ...'`.

## 7. Hard "do nots"

- Do not edit `SESSION.md` to claim progress — it is rewritten once, in R3.1, from gate output.
- Do not delete or bypass a guardrail to relieve friction; sharpen it (R1.6 shows how).
- Do not mark any Section 4 item done from memory of the old SESSION.md — only from a gate you ran in this session.
- Do not commit when `make test-count` reports errors. This is how the repo got into this state.
- Do not interpret ZAI 429s as code failures.

## 8. Why this drives GLM/Qwen/DeepSeek correctly (design notes)

- **Short numbered contracts** (Section 0, R1.5 prompt) instead of long narrative — small/mid models follow position-1 mechanical lists and lose buried rules.
- **Deterministic state gates** (`.gate-status`, collect-check on commit, evidence-ledger throw) carry enforcement in hooks, so model compliance is *verified*, not requested. A model cannot phrase its way past an exit code.
- **One file, one prompt:** "Read GLM_REMEDIATION_GUIDE.md and execute it top to bottom" is a complete instruction; every task names its files, its proof command, and its commit message format.
- **Evidence ledger** turns "is it done?" into a mechanical lookup any model can perform, removing the rationalization channel that produced incidents 1–13 in `BUGS.md`.

## 9. Checklist (tick in TASKS.md as you go)

```
Phase R0 — restore the build
[ ] R0.1  skills import fixed; suite collects (0 errors)
[ ] R0.2  lint 0 errors
[ ] R0.3  daemon wiring real: S14 stamp_head, M7 monitor, H5 dispatcher, S2 recorder
[ ] R0.4  typecheck ≤ 25 (target 0)
[ ] R0.5  re-baseline; 0 unexplained test failures

Phase R1 — guardrails
[ ] R1.1  honest truth targets: test-failures fixed, collect-check, gate + .gate-status
[ ] R1.2  commit gated on collect-check + fresh green gate (Makefile + plugin throw)
[ ] R1.3  completion claims verified against .gate-status, vocabulary triggers removed
[ ] R1.4  TASKS.md evidence ledger + plugin throw + preflight check
[ ] R1.5  system-prompt injection ≤ ~40 mechanical lines; TUI rules moved to skill
[ ] R1.6  TDD gate sharpened (reference-aware + logged-refactor path), still throws
[ ] R1.7  AGENTS.md: completion=gate+evidence section, new targets documented

Phase R2 — missed work
[ ] R2.1  M1 ansible events real
[ ] R2.2  M6 refresh targets the loop's runner
[ ] R2.3  M13 config sections consumed or deleted
[ ] R2.4  M12 real active_jobs + claim cap
[ ] R2.5  M10 approvals persisted + change events
[ ] R2.6  every claimed G/S/F/M item re-proven by named test; failures fixed

Phase R3 — honesty
[ ] R3.1  SESSION.md rewritten from gate output
[ ] R3.2  fail_under raised to observed−2
[ ] R3.3  BUGS.md incident extended with fixes
[ ] R3.4  make validate green; final commit
```

Start at R0.1. Prove every step with a `make` target.
