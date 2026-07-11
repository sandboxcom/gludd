# gludd Stabilization & Completion Plan

> **Audience**: Any AI model executing work in this repository (GLM, DeepSeek,
> Qwen, Claude, or human contributors). This document is written to be
> self-sufficient: follow it top-to-bottom without tribal knowledge.
>
> **Written**: 2026-07-08. **Source of truth for live state**: `SESSION.md`
> (derived from `make gate`) and `TASKS.md` (evidence ledger). If this plan
> disagrees with `make gate` output, the gate is correct.
>
> **Goal**: take gludd from its current state (beta.2 version-bumped but
> unshipped, CI red with a small failure tail) to a **stable, shipped,
> useful** state: green CI, beta.2 released, beta.3 architecture landed,
> security residuals closed, and the system able to work external projects.

---

## 0. What gludd is (context for the executor)

gludd ("General Ludd") is an autonomous software-development agent system:

- A **daemon** (FastAPI, `src/general_ludd/daemon.py`) runs an **event loop**
  (`src/general_ludd/event_loop/loop.py`) that ticks through phases:
  claim todo → dispatch to a model/agent → execute (edits in a git worktree)
  → review (ReturnReviewer / consensus) → reconcile (commit/PR).
- **Todos** are persisted in SQLite (SQLAlchemy + alembic). Work arrives via
  `POST /api/todos`, GitHub issue ingestion, or self-improvement analysis.
- A **model gateway** (`src/general_ludd/models/gateway.py`) routes calls to
  ~24 providers (ZAI/GLM, DeepSeek, OpenRouter, etc.) with failover chain,
  circuit breaker, budget guard, and pause gates.
- An **Ansible collection** (`collections/ansible_collections/general_ludd/agent/`)
  exposes the daemon as modules (`gludd_facts`, `gludd_message`,
  `gludd_agent_run`, …) and ~40 roles, all molecule-tested against a mock
  daemon (`molecule/mock_daemon/server.py`).
- **Enforcement plugins** (`.opencode/plugin/*.ts`) and shell hooks guard the
  orchestrating agent itself (gate freshness, push-rate, anti-false-done).

Current version: `0.1.0-beta.3` (bumped in code, **tag not cut**).
DB decision of record (W3.5): **SQLite only, single worker** — being replaced
in beta.3 by a broker/writer-process architecture (see Phase B).

---

## 1. NON-NEGOTIABLE RULES (read before ANY work)

These rules are enforced by hooks/plugins. Violating them wastes your run.

### 1.1 Shell is make-only
Every Bash/shell call **must** be `make <target>` — a PreToolUse hook blocks
everything else (including `git`, `gh`, `python`, `echo`, `cat`). Use your
harness's Read/Grep/Glob/Edit/Write tools for file work. Discover targets by
reading the `Makefile`.

### 1.2 Commit messages: single line, no metacharacters
`MSG='...'` must be ONE line and must NOT contain `;` `|` `&&` `$()`
backticks — these are blocked even inside quotes. Write plain prose:
`MSG='fix(gateway): retry on 529 with backoff'`.

### 1.3 Commit flow
1. Run targeted verification first: `make test-iso TESTFILE='tests/unit/test_x.py'`
   (NOTE: `make test TESTFILE=...` is a **no-op**; always use `test-iso`),
   plus `make lint` and `make typecheck`.
2. Commit with: `make git-commit-no-verify GLUDD_CI_IS_GATE=1 MSG='...'`
   — the full local gate OOMs on this machine, so CI is the gate of record.
3. Never run two gates or two pytest sessions concurrently. Check
   `make ps-pytest` first. Output like "208 errors popen-gwN / 0 failures"
   means a tmp-rotation collision with another run, not a regression.

### 1.4 Never push red / never fake green
- Never claim "done/green/fixed" without pasting the measurement (CI run id +
  conclusion, `.gate-status` content, or test output with counts).
- Never cut a release without a CONFIRMED-GREEN CI run for the exact SHA.
- `make release-cut` already enforces this (require-ci-green step 0).

### 1.5 "Fix" means repair, never disable
Never make a problem disappear by disabling, stubbing, skipping, xfail-ing,
or weakening the feature/test/guardrail involved. If a test fails, fix the
root cause. Adding a suppression comment (`# type: ignore` without reason
code, `# noqa`, blanket `xfail`) to pass a gate is a policy violation.

### 1.6 Search constraints
`make pygrep Q='...'` must not contain backslashes or regex metacharacters
(prompts the operator). Use plain terms. `make srcgrep` / `make filegrep`
search `src/` only.

### 1.7 Resource discipline
- Worktree agents each build a ~320MB venv; cap ~5–6 concurrent worktrees.
  Run `make disk` before heavy ops; `make clean-worktree-venvs` to reclaim.
- Local full test suite OOMs under 8-worker xdist — do not run it; use
  targeted `test-iso` locally and CI for the full suite.

### 1.8 Evidence ledger
Every completed task adds a row to `TASKS.md`:
`- [x] <ID> — <title> | evidence: <test file + pass count + commit hash>`
and updates `SESSION.md` (Last Updated, Current Work, Next Steps).

---

## 2. Current state (verified 2026-07-08)

| Area | State | Evidence |
|---|---|---|
| HEAD | `ed28ee48` (master) | `make git-log` 2026-07-09 |
| CI | CI gate PASSED (3.11+3.12 green); test shards pending | SESSION.md 2026-07-09 |
| beta.2 | Version bumped everywhere; **tag NOT cut, artifact NOT verified** | SESSION.md Known Gaps #1 |
| beta.3 | Phase B (B3.1.1–B3.1.5) COMPLETE | TASKS.md beta.3 section |
| cast(Any) burn-down | **COMPLETE** — 0 sites in src/, ratchet xfail removed (commit `1d89ce8e`). TASKS.md line ~867 still shows Tier 4 unticked — stale; tick it when touching TASKS.md | git log |
| SSRF consolidation (#40) | Tranches 3+4 landed (26 connectors on `is_url_blocked`, `_ssrf_guard.py` deleted) | TASKS.md Phase S2026-07-03 |
| Pause/resume (#35) | Slice 2 wired (PauseController → gateway + event loop + daemon) | TASKS.md S.35.2 |
| Full local suite | OOMs under xdist; CI-as-gate policy in force | SESSION.md |
| Known feature gaps | Slack/WebSocket/reconnect connectors (feature requests, non-blocking) | SESSION.md |
| Disk | not measured this session | — |

### The 13 remaining CI failure clusters (Phase A target)

From SESSION.md session 18 (a fix wave was dispatched but **not confirmed
landed** — verify before redoing work):

1. 4 × slurm billing
2. 3 × connectors_base caplog
3. 2 × PSK caplog
4. 2 × tokenizer
5. 1 × MCPToolRegistry import
6. 1 × structured_task_spec

---

## 3. Execution phases (do them in order)

Priority is strictly: **A (ship) → B (beta.3 architecture) → C (quality) →
D (security residuals) → E (MVP: external projects) → F (docs/onboarding)**.
Do not start a later phase's work package while an earlier phase has an
unblocked, unclaimed work package — except where a WP is marked `[parallel-ok]`.

---

### PHASE A — CI green + ship v0.1.0-beta.2  ⛔ blocks everything

#### WP-A1: Reconcile the in-flight fix wave
A previous session dispatched fixes for the 13 CI failures that may or may
not have been committed.
1. Read recent commits: `make git-log` (or Read SESSION.md + TASKS.md).
2. For each of the 6 failure clusters above, Grep the test files involved and
   check whether a fix is already present on HEAD.
3. Produce a table: cluster → fixed-on-HEAD? → commit hash or "still broken".
**Acceptance**: table in SESSION.md; no duplicate re-fixing.

#### WP-A2: Fix each remaining CI failure cluster (one commit per cluster)
For each still-broken cluster:
1. Reproduce locally: `make test-iso TESTFILE='<failing test file>'`.
2. Fix the ROOT CAUSE (see rule 1.5). Known recurring root-cause patterns in
   this repo — check these FIRST:
   - **caplog failures**: missing `caplog.propagate = True` (or root logger
     propagate) — see TASKS.md Q3.10 for the established fix pattern.
   - **xdist/global-state pollution**: failures that MOVE between runs are
     ordering pollution; fix = snapshot/restore logging state + reset global
     singletons in fixture teardown (see 2026-07-01 CI-green notes).
   - **import errors** (MCPToolRegistry): usually a moved/renamed symbol —
     Grep for the class to find its current home; fix the import, don't stub.
3. Verify: `make test-iso TESTFILE='...'` shows the previously failing tests
   passing; paste counts.
4. Commit per rule 1.3.
**Acceptance**: all 6 clusters fixed and committed; each has a TASKS.md row.

#### WP-A3: Push, wait for CI, iterate to green
1. `make git-push-sandboxcom` (push-rate guard applies; if blocked, read the
   guard message and wait/comply — do not bypass).
2. `make ci-wait` (or poll `make ci-verdict BRANCH=master`).
3. If new failures appear: repeat WP-A2 pattern for them. Failures that move
   between runs = ordering pollution (see above), not flakiness to ignore.
**Acceptance**: CI run for HEAD SHA with `conclusion: success`, run id pasted
into SESSION.md.

#### WP-A4: Cut the release
Only after WP-A3 acceptance:
1. `make release-cut TAG='v0.1.0-beta.2' MSG='Release v0.1.0-beta.2'`
   (aborts itself if CI is not green — that is correct behavior, not a bug).
2. `make verify-release-artifact TAG='v0.1.0-beta.2'` must PASS.
3. Update SESSION.md + TASKS.md (tick the ship row with run id + tag URL).
**Acceptance**: release exists with artifacts; verify target PASS output pasted.

---

### PHASE B — beta.3 architecture (gunicorn multi-worker)

Explicitly queued by the project owner. TASKS.md "Phase beta.3" is the
authoritative breakdown; summary:

#### WP-B1: B3.1.3 — Writer subprocess extraction
Extract the daemon's writer path (event-loop claim/review/reconcile) into a
dedicated subprocess so DB-write responsibility is isolated from gunicorn
HTTP workers. Builds on the already-landed `Broker` + `WriteQueue`
(`tests/unit/test_ipc_write_queue.py`, commit `bddeba52`) and the read-only
engine factory (`init_read_only_engine_from_config`,
`tests/unit/test_read_only_engine.py`).
1. Read `src/general_ludd/db/session.py` (the `_clamp_workers_for_sqlite`
   clamp and W3.5 decision), the broker/write-queue module, and
   `event_loop/loop.py` phase structure before designing.
2. TDD: write tests first for (a) writer process owns ALL mutating sessions,
   (b) HTTP workers get read-only sessions, (c) writes flow through the
   WriteQueue, (d) ordering preserved, (e) crash of writer does not corrupt DB.
3. Keep the single-process mode working (config-gated) — this is an addition,
   not a replacement, until B3.1.4 proves stable.
**Acceptance**: new tests pass via `test-iso`; single-process mode regression
suite (`test_event_loop*.py`, `test_daemon_lifespan_smoke.py`) still green.

#### WP-B2: B3.1.4 — Supervisor + writer process lifecycle
Application-level supervisor owning writer subprocess start/restart/health
with bounded retry + exponential backoff; every recovery surfaced as an
observable event (No Unseen Events invariant). This ALSO satisfies beta.3.4
(self-healing pattern) — implement them together, distinct from the
process-level `scripts/agent_watchdog.py`.
**Acceptance**: kill-writer-process test shows auto-restart + emitted event;
bounded-retry test shows permanent-failure escalation (not a spin loop).

#### WP-B3: B3.1.5 — Agent hydration/dehydration
Serialize in-flight agent state (claim context, tool budget, message-queue
position) so a worker resumes an interrupted todo after restart. Prior art
exists: the agent-env dehydrate/hydrate work (27 tests, 2026-07-01) — Grep
`dehydrate` in src/ and BUILD ON IT, do not re-implement.
**Acceptance**: restart-mid-todo integration test resumes rather than drops.

#### WP-B4: Postgres path (only if owner confirms)
TASKS.md mentions "move off SQLite to Postgres" as part of beta.3.1. This is
a LARGE step (alembic is SQLite-specific; migration 001 has known drift —
missing 8 tables + project_id FKs). **Do not start Postgres work without an
explicit owner go-ahead**; B1–B3 (broker/writer-process over SQLite) deliver
the multi-worker value without it. If approved: first fix alembic drift
(WP-D3), then add Postgres engine support behind config, then CI matrix job.

---

### PHASE C — Quality: coverage + test-suite health  `[parallel-ok with B]`

#### WP-C1: Coverage lifting (beta.3.2)
1. Get the coverage report from the latest green CI run artifacts (local
   full-suite runs OOM — do not try).
2. Rank modules by (low coverage × high criticality): gateway, event_loop,
   dispatcher, db/repository first.
3. Write behavioral tests (not import-only tests — see the ENF-README lesson:
   190/192 "100%" claims were file-existence-only until audited).
4. One commit per module cluster, with TASKS.md rows.
**Acceptance**: coverage delta visible in CI report; no new xfail/skip.

#### WP-C2: xdist pollution burn-down
The durable fix pattern (from 2026-07-01 work): snapshot/restore ALL logging
state + reset global singletons around tests. Inventory remaining polluters:
Grep tests/ for fixtures mutating `logging` root, module-level singletons,
`os.environ` writes without monkeypatch.
**Acceptance**: two consecutive green CI runs with no moved-failure churn.

#### WP-C3: Local suite OOM mitigation
Investigate making a local gate viable again: lower default xdist workers,
add `--max-worker-restart`, memory-cap fixtures, or a `gate-lite` target
(lint + typecheck + collect + smoke + targeted tests). Do NOT delete the
CI-as-gate path.
**Acceptance**: documented working `make gate-lite` (or a written finding of
why not) + AGENTS.md updated.

---

### PHASE D — Security & correctness residuals  `[parallel-ok]`

Work the open items from `docs/audit/` (BACKLOG_FINDINGS_2026-07-01.md,
ALPHA4_VERIFIED_BACKLOG_2026-06-24.md, NEW_FINDINGS). Rules:

1. **Verify before fixing** — this repo's history shows ~80% of "open"
   security findings were already fixed on master. For each finding: Grep the
   cited file/line first; if fixed, mark the doc row CLOSED with the commit
   hash instead of re-fixing.
2. Known genuinely-open threads to check first:
   - **WP-D1**: SSRF consolidation completeness check — tranches 3+4 covered
     26 connectors; verify NO outbound-HTTP call site remains off
     `is_url_blocked` (webhooks, web retriever, skills fetcher, MCP
     transport, model providers). Add a guardrail test that fails when a new
     unguarded `httpx`/`urlopen` call site appears.
   - **WP-D2**: CC-1 lease double-dispatch — previously REFUTED (the Q.F1
     guard defends it); re-verify the named test still passes and close the
     doc row; do NOT build the migration unless a failing test proves need.
    - **WP-D3 — CLOSED**: alembic migration drift — migration 001 missing 8
      tables + project_id FKs. Fixed by migration 024 (reconciles `alembic
      upgrade head` with `create_all`, commit `ff8a8298`). Evidence: parity
      suites `tests/unit/test_alembic_orm_parity.py` (4 passed) and
      `tests/unit/test_alembic_create_all_parity.py` (5 passed) — 9/9 green
      (re-verified 2026-07-11). The old blocker on Postgres work (WP-B4) is gone.
   - **WP-D4**: remaining AB-5/6/8, GA-1/3, XT-3/4, GW-1/2 findings — triage
     per rule 1 above.
**Acceptance per finding**: doc row updated (CLOSED w/ hash, or fixed w/ new
test + commit); TASKS.md row added.

---

### PHASE E — MVP keystone: work external polyglot projects

gludd today can only operate on projects shaped like itself (make-driven,
ruff/mypy/pytest). The MVP goal is a **generic target-project toolchain
runner**.

#### WP-E1: ToolchainAdapter design + detection
1. Locate current hardcoding: Grep src/ for `ruff`, `mypy`, `pytest`
   subprocess invocations and `make ` calls in the project-runner path.
2. Design: per-project `ToolchainConfig` (detect → lint → typecheck → test →
   format commands), populated by (a) explicit project config, then
   (b) detection heuristics (pyproject/package.json/go.mod/Cargo.toml).
3. Land the config model + detection with tests BEFORE refactoring call sites.

#### WP-E2: Migrate runner call sites to the adapter
Replace each hardcoded invocation with adapter lookups, preserving gludd's
own toolchain as the default config (zero behavior change for self-hosting —
`make dogfood` must still pass).

#### WP-E3: Prove it on one external project
Add an e2e test that clones a small non-make fixture project (e.g. plain
pytest, or a Node project) and runs a full todo lifecycle against it.
**Acceptance**: e2e green in CI; `make dogfood` still green.

---

### PHASE F — Onboarding & docs for weaker-model executors  `[parallel-ok]`

#### WP-F1: Config reference + quickstart
Document every env var (`GLUDD_*`, `ZAI_API_KEY`, etc.), config file, and
the minimal run path (install → configure one model provider → start daemon
→ submit todo → watch it complete). Verify each step by running it.

#### WP-F2: CONTRIBUTING for AI executors
Distill section 1 of this plan plus the Makefile target catalogue into
`CONTRIBUTING.md` (or AGENTS.md §) so the rules live in-repo for every
future session/model.

#### WP-F3: Keep this plan current
Whenever a WP completes, tick it here AND in TASKS.md. A stale plan is worse
than no plan.

---

## 4. Standard work-package protocol (for every WP above)

```text
1. READ    the named files + any design doc under docs/ for the area.
2. VERIFY  the problem still exists on HEAD (grep/tests) — never fix blind.
3. TEST    write/extend a failing test that pins the desired behavior (TDD).
4. FIX     root cause only; smallest change that makes the test pass.
5. CHECK   make test-iso TESTFILE='...'; make lint; make typecheck.
6. COMMIT  make git-commit-no-verify GLUDD_CI_IS_GATE=1 MSG='<single line>'
7. RECORD  TASKS.md evidence row + SESSION.md update (same or next commit).
8. NEVER   claim completion without pasted counts/hashes/run ids.
```

Escalate to the owner ONLY for: force-push, history rewrite, deleting
user-authored files, Postgres go-ahead (WP-B4), external service
credentials, or release-tag decisions outside Phase A.

---

## 5. Open questions for the owner (non-blocking)

1. WP-B4: is Postgres in scope for beta.3, or is broker/writer-over-SQLite
   sufficient for the milestone?
2. Phase E ordering: should the external-project runner jump ahead of
   Phase C/D once beta.2 ships (it is the MVP keystone)?
3. Connector feature requests (Slack, WebSocket, reconnect): which release
   are they targeted at?
