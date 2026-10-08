# gludd — Agentic Implementation Spec

**Version:** 1.0 (2026-07-09)
**Audience:** LLM implementation agents (and their human operators).
**Purpose:** the single, exhaustive, dependency-ordered work specification that takes gludd from its current state to a **feature-complete, CI-green release**, while keeping the pipeline green at every step. Every item is independently actionable: it names the exact files, the current-state evidence, the implementation steps, the test plan, and the acceptance criteria.

Items whose current-state evidence comes from audit **documents** rather than freshly-verified code carry the marker **[RE-VERIFY]**: before implementing, confirm the defect still exists on current `master` (several previously-documented findings are already fixed — see §3.0). Skipping this step wastes whole agent-days re-fixing fixed code.

---

## 1. Mission + Definition of Done

gludd is an autonomous agentic-SDLC daemon: todos in → AI implements → AI reviews → quality gate → git commit, with multi-model routing (~24 providers), an Ansible execution layer (~109 roles), SQLite persistence (27 ORM tables), and a security/guardrail stack.

**Definition of done, in order:**

1. **CI GREEN as a standing invariant.** Both GitHub Actions workflows on `sandboxcom/gludd` conclude `success` on `master` HEAD:
   - `.github/workflows/build.yml` ("Build and Release") — gate, 10 test shards, coverage, molecule, packaging.
   - `.github/workflows/pages.yml` ("Deploy Presentation to Pages") — deck build + Pages deploy. (The Pages **site** now exists — created 2026-07-09 via `make pages-enable`, `build_type=workflow`, URL `https://sandboxcom.github.io/gludd/`. Deploys were failing only because the site had never been created.)
2. **v0.1.0-beta.1 shipped**: `make release-cut TAG='v0.1.0-beta.1' ...` + `make verify-release-artifact TAG='v0.1.0-beta.1'` succeed against a CONFIRMED-GREEN CI run for that exact SHA. (Code version is `0.1.0-beta.1`; no beta release has been cut yet.)
3. **Feature-complete**: every work item in §3 is closed with evidence, or explicitly re-triaged as REFUTED/deferred with a written rationale in `TASKS.md`.
4. **Docs truthful**: README, the reveal.js deck, and the design docs describe what the code actually does (stale-doc items in §3.6 closed).

Never declare any of these "done" without pasting the machine evidence (CI run id + conclusion, test PASS lines, release asset listing). Worktree-drafted ≠ done. Local-green ≠ done — **CI is the gate** (the full local gate OOMs on the dev machine; `make gate-lite` is the local approximation).

---

## 2. Ground Rules for Implementing Agents

These are binding. They come from `CLAUDE.md`, `AGENTS.md`, and hard-won session experience.

### 2.1 Shell discipline
- **Bash is make-only.** A PreToolUse hook rejects anything that isn't `make <target>` (including `||`, `&&`, `;`, pipes, `$()`, backticks — even inside quotes). If you need a new capability, **add one Makefile target** that does it, then call that target. Read/Edit/Write/Grep/Glob tools are unrestricted.
- Never pass `PATH=` as a make variable (it clobbers the shell `$PATH` inside recipes; the repo uses `PATH_`/`DIR` instead). Never put backslashes/regex metacharacters in `make grep Q=` (prompts the operator); use plain substrings or the `grepf` variant writing to a file.
- Useful search/list helpers: `make grep Q='pat' [PATH_='dirs']` (defaults src+tests; does NOT search the Makefile), `make grepf Q='pat' DIR='dir' OUT=/tmp/x.txt`, `make lsd|lsf DIR=... OUT=...`, `make git-diff-full [FILES='...']`.

### 2.2 Testing discipline
- Reproduce and verify with **`make test-iso TESTFILE=tests/... ID=<unique>`** (isolated basetemp — safe for concurrent agents) or **`make test-xdist TESTFILE=... ID=<unique>`** to reproduce xdist-order issues (the gate runs `-n N --dist loadgroup`).
- **Never run two gates or two full pytest suites at once** (`make ps-pytest` first). "208 errors/popen-gwN/0 failures" output = tmp-rotation collision, not a regression.
- `make test TESTFILE=` is a no-op; use `test-iso`/`test-specific`.
- Local Python is 3.14; CI runs 3.11 + 3.12. A 3.12-only failure usually means xdist-order pollution (different test distribution), not a language issue.
- **Never weaken, skip, or delete a test to make it pass.** "Fix" always means make the feature work. Disabling a feature you were asked to fix is a new bug. If a test asserts obsolete semantics after a deliberate behavior change, reconcile the test to assert the NEW intended behavior meaningfully (see the slurm cost-cap precedent, §3.1 item A3).

### 2.3 Commit / push discipline
- Commits only through gated targets: `make git-commit MSG='...'` / `make git-commit-no-verify MSG='...'` — both require a **fresh (<30 min), fully-green `.gate-status`** (`make gate-lite` writes it locally without OOM; `make gate-async` + `make gate-status` for the full gate). There is **no bypass flag**; `GLUDD_CI_IS_GATE=1` does nothing.
- Exception designed for subagents: `make ship-commit MSG='...'` / `make ship-commit-files FILES='...' MSG='...'` are allowlisted from the local gate check (CI is the gate) and include the batch push.
- Commit messages: **single line**, no `;` `|` `&` `$()` backticks (hook-blocked even quoted).
- Every checked `- [x]` item in `TASKS.md` MUST carry a `| evidence:` suffix (test node ids / commit SHAs) — `tests/unit/test_tasks_tick_guard.py::TestCheckTasksTicks::test_real_tasks_file_passes` enforces this **in CI**, so an evidence-less tick is itself a CI failure.
- Push small batches. After every push: `make ci-verdict-safe` (cooldown-enforced; never loop-poll, never dispatch a CI-poll subagent). Get the verdict before stacking the next wave.
- Never push red, never cut a release without a CONFIRMED-GREEN CI run for the exact SHA.

### 2.4 Multi-agent hygiene
- Cap ~5-6 concurrent worktree agents (each venv ≈ 300 MB; `make disk` before heavy ops, `make clean-worktree-venvs` to reclaim; ENOSPC deadlocks Bash for everyone).
- Give subagents **disjoint file sets**. Contention points that must be single-writer: `Makefile`, `TASKS.md`, `tests/conftest.py`, `.github/workflows/*`.
- Brief every subagent that Bash is make-only and to use `make test-iso ... ID=<uniq>`.
- Long operations must stream/heartbeat (`make run-watched CMD='...'`); flat output past the ETA = stall — act, don't wait.
- 429/503/529/timeouts are retry-with-backoff signals, not stops.

---

## 3. Work Items

Priorities: **P0** = ship-blocker (CI red or release-gating), **P1** = release-quality (security residuals, correctness, coverage), **P2** = enhancement.

### 3.0 Already FIXED — do not re-implement (verified against code 2026-07-09)

To prevent wasted effort on stale docs:
- **Alembic migration drift** ("001 missing 8 tables + project_id FKs") — FIXED. Chain `alembic/versions/001…024_reconcile_drift.py` covers all 27 ORM tables; `tests/unit/test_alembic_orm_parity.py` (4/4) and `tests/unit/test_alembic_create_all_parity.py` (4/4) pass. Update stale docs instead (item F4).
- **daemon bare `AgentRegistry()`** — FIXED at `src/general_ludd/daemon.py:1795` (`default_registry()` + anti-regression comment; `tests/unit/test_can_invoke_daemon_activation.py`).
- **#40 SSRF canonical module** exists and is adopted by ~50 call sites (`src/general_ludd/security/ssrf.py`); what remains is consolidation of 14 stragglers (item C1).
- **#50 dispatch_one fail-open** — FIXED (fail-closed; `tests/unit/test_dispatcher.py` 16/16, `test_dispatch_permission_gate.py` 8/8).
- **#56 SLM compaction SLICES 1-3** — landed (`e2b41364`, `5c2fa5dc`, `4f2cba3b`) and daemon-wired.
- **Generic project toolchain runner slices 1-2 + most of 3** — REAL and reachable: `src/general_ludd/project_runner/{detect,profile,runner,findings}.py`, `execution/engine.py:194 _run_tests`. The remaining gap is the unwired `run_project_gate` (item D2), not the runner itself.
- **shell=True / yaml.load / pickle / eval-exec on model output** — swept clean; don't re-audit.
- **alembic.ini logging sections** — FIXED. `alembic.ini:5-37` carries the `[loggers]`/`[handlers]`/`[formatters]` sections (previously missing); no further action.
- **M-3 unknown-role fail-open** — FIXED. `resolve_role(strict=True)` at `src/general_ludd/models/gateway.py:1360` rejects unresolvable roles instead of silently defaulting.
- **`record_success` fallback double-count** — FIXED. Guard at `src/general_ludd/models/gateway.py:1280-1281` prevents the fallback path from incrementing success twice.
- **SEC-4 webhook delivery** — FIXED. `src/general_ludd/events/hooks.py:241-296` fires webhooks via tracked async httpx calls with redaction, replacing the untracked/unredacted sync path.
- **M-4 `list_all()` unbounded result set** — FIXED. `src/general_ludd/db/repository.py:337-365` clamps the returned page size.
- **M-13 compare-and-swap lock** — FIXED, stronger than the audit assumed. CAS optimistic-lock semantics at `src/general_ludd/db/repository.py:277-315,565-605` guard the relevant update sites.
- **`prompt_registry.refresh` blocking the event loop** — FIXED. Moved to `asyncio.to_thread` at `src/general_ludd/daemon.py:1161`.
- **SEC-5b secrets `resolve()` permission bypass** — FIXED. `resolve()` now enforces the permission check via `_enforce_permission` (`src/general_ludd/secrets/manager.py:286-295`) before returning a secret.
- **C16 filestore bootstrap RCE** — FIXED. `src/general_ludd/filestore/bootstrap.py:370-429` verifies a pinned digest before chmod+exec of any downloaded artifact, failing closed on mismatch.
- **D1 onboard provider wiring** — FIXED. `gludd onboard <provider>` reaches the real aws/gcp/azure implementations end-to-end, including `--project`/`--subscription` passthrough (landed in local commit `2543152b` — verify in CI per §3.0.1; the detailed §3.4 D1 entry is kept for historical reference and test-plan completeness).
- **SEC-8 `/api/status` info leak** — FIXED. The endpoint no longer includes `db_url` in its response payload.

### 3.0.1 STATUS UPDATE 2026-07-10 — landed locally, pending push + CI verification

Two local commits close a large slice of Waves A/C/D/F. Treat their items as **implemented but NOT done** until a green CI run confirms them (rule §2.3):

- **`2543152b` (batch 1, 65 files):** alembic env.py logger fix (C21-FIXED entry); caplog order-independence hardening (A1/A2); slurm cost-cap reconciliation (A3); GPU-metrics determinism (A4); test-shard rework (A7/A9); pages.yml SHA-pins (A10); daemon sync-bridge removal (C26 landed-set); onboard provider wiring incl. `--project`/`--subscription` passthrough (D1); SSRF connectors tranche 6 (C1 part); adversarial scan-file jail (C2) + secrets redaction widening (C3); deck rebuild (F1 part); TASKS.md CGW ledger + CHANGELOG.
- **`4113f206` (batch 2):** SSRF tranche 5 — `issue_sources/{base,jira,monday,bitbucket_issues,clickup,gitlab_issues}.py` + `git_automation/repo.py reject_unsafe_repo_url` onto the canonical `security/ssrf.py` predicates; 200 tests passed.
- **`a0f86dd1` (batch 3):** spec + docs sweep — `docs/AGENTIC_IMPLEMENTATION_SPEC.md`, `SESSION.md`, root files, and audit banners updated to reflect the batch-1/batch-2 landings above.

Consequently **C1 (SSRF consolidation) is substantially DONE** — remaining C1 work: sweep for any stragglers beyond the 14 listed sites, land the `bug_class_registry.py:232` detector-allowlist fix if not in batch 1, and keep the consolidation meta-test. First agent after push: run `make ci-verdict-safe` and update this block with the run id + conclusion.

### 3.1 Wave A — CI GREEN (all P0)

The authoritative failure inventory is from run **29055665462** (master @ `a7ab5d15`): shards unit-3 (11 failures, identical 3.11+3.12 = deterministic), other (2 PSK + 3 GPU-metrics), unit-2 3.11 (1 caplog), unit-1a (cancelled both pythons). Local commit `0e34db68` (unpushed at spec time) already fixes the plugin-line-count threshold, the aiosqlite `Event loop is closed` teardown class (session-scoped conftest patch), and a writer-supervisor timing flake.

> **A0 — Land the in-flight CI-green wave.**
> Files: `TASKS.md`, `tests/conftest.py`, `tests/unit/test_process_overhead.py`, `tests/unit/test_writer_supervisor.py` (commit `0e34db68`) plus the working-tree fixes from items A1-A5/A7 below.
> Steps: collect all fix diffs into the main tree → `make gate-lite` → gated commit(s) → push (`make git-push-sandboxcom`) → `make ci-verdict-safe`.
> Acceptance: build.yml run for the pushed SHA concludes `success` on every test-shard job.

> **A1 — Caplog order-independence: worker cluster.**
> **STATUS: [LANDED — verify in CI].** Real root cause: `alembic/env.py` called `fileConfig(...)` with `disable_existing_loggers=False` at import time (`alembic/env.py:12-18`), which reset logger propagation/handlers process-wide for whichever xdist worker imported it first — not per-test logger reconfiguration as originally hypothesized below. Fixed by correcting the `fileConfig` call; confirm the assertions below now pass on the next CI run.
> Failing: `tests/unit/test_worker_broadcast_401.py` (2), `tests/unit/test_worker_broadcast_psk.py::test_no_allowlist_preserves_behavior_and_warns`, `tests/unit/test_worker_build_gateway.py::TestBuildGatewayConfigError::test_config_load_error_logs_and_returns_none`, `tests/unit/test_model_registry.py::test_download_unpinned_warns`.
> Root-cause class: another test in the xdist shard disables propagation / reconfigures the source logger; `caplog` then captures `''`.
> Fix pattern (the durable one, per commit `f7638e73`): make each test order-independent — `caplog.set_level(<level>, logger="<exact source logger name>")` (forces handler + propagation for that logger) or monkeypatch `logging.getLogger(<name>).propagate = True`. Find the exact logger name by reading the source module that emits the record (e.g. `src/general_ludd/reload/worker_broadcast.py`, the gateway builder, `src/general_ludd/model_weights/…` registry). NEVER weaken the assertion.
> Test plan: `make test-iso TESTFILE=<file> ID=a1` per file; prove order-independence with `make test-xdist` pairing the file with a known logger-polluting test if identified.
> Acceptance: all five tests pass in-shard on CI (both pythons).

> **A2 — Caplog order-independence: security/dispatch cluster.**
> **STATUS: [LANDED — verify in CI].** Same root cause as A1: `alembic/env.py` `fileConfig(..., disable_existing_loggers=False)` at `alembic/env.py:12-18` reset logger state process-wide for the first xdist worker to import it. Fixed alongside A1; confirm on the next CI run.
> Failing: `tests/security/test_daemon_auth_redteam.py::TestA3NoAuthDegraded::{test_no_psk_logs_loud_warning,test_no_psk_allow_no_auth_logs_loud_warning}` (P1-severity security assertions — the loud fail-closed warning MUST still be verified), `tests/unit/test_spend_limiter_dispatch_wiring.py::TestSpendLimiterDispatchGate::test_over_budget_skips_dispatch`, `tests/unit/test_webhook_fire_tracking.py::test_failed_webhook_is_tracked_then_cleaned_up_and_logged`.
> Same fix pattern as A1. The daemon-auth tests need the exact logger used by the startup PSK check (grep `src/general_ludd/security/auth.py` / daemon startup for the warning emit site).
> Acceptance: all four pass in CI both pythons; assertions still verify the warning text (e.g. `'gludd_psk' in caplog.text`).

> **A3 — Slurm cost-cap semantics reconciliation.** **STATUS: [LANDED — verify in CI].**
> Files: `tests/unit/test_slurm_cost_cap.py:223-252`, `tests/integration/test_slurm_cost_cap.py:26-31`; source `src/general_ludd/infra/slurm.py:842-864` (unchanged).
> Background: commit `4b961146` deliberately reordered `SlurmJobMonitor._poll()` so cost is sampled every poll BEFORE the terminal-state check (final cost sample recorded; cancellation still gated on non-terminal). It updated two integration files but missed the unit tests, and its commit message over-claimed (it did NOT fix PSK/rg_search/plugin-count).
> Fix: unit tests now assert the elapsed-based cost values (`(10000.0/3600.0)*3.0` etc.); integration test job id `"job-001"` → `"1001"` (real Slurm ids are numeric; `_require_job_id` at slurm.py:38-45 is correct) and `max_cost_usd` 0.01 → 10.0 so the unmocked `scancel` path is never invoked.
> Acceptance: `test-iso` PASS on `tests/unit/test_slurm_cost_cap.py` (23), `tests/integration/test_slurm_cost_cap.py` (7, no `PytestUnhandledThreadExceptionWarning`), `tests/integration/test_bill2_slurm_cost_cap_wiring.py` (9), `tests/integration/test_bill_slurm_cost_cap_e2e.py` (8).

> **A4 — GPU-metrics environment pollution (3.12, shard other).** **STATUS: [LANDED — verify in CI].**
> Failing: `tests/integration/test_bill_gpu_metrics_e2e.py::TestGPUMetricsCollectorUnavailable::*` (3) — `is_available()` returned True on a GPU-less runner; `collect_all` returned one all-zeros `GPUMetrics` instead of `[]`.
> Likely mechanism: cross-test pollution (module-level availability memoization, a fake `nvidia-smi` on PATH from another test, or an unreverted monkeypatch of `shutil.which`/subprocess).
> Fix: make the tests deterministic — explicitly patch/reset the availability seam in setup; make any module-level cache resettable and add an autouse reset. Do not vacuate the assertions.
> Test plan: `test-iso` on the file, then `test-xdist` pairing with the identified polluter.
> Acceptance: file passes in-shard on CI 3.12.

> **A5 — `tests/unit/test_rg_search.py::test_build_argv_drops_disallowed_flags`.** **STATUS: [LANDED — verify in CI].**
> `assert False`, deterministic both pythons. Source: the `build_argv` flag-allowlist in `src/general_ludd/code_intelligence/` (grep `build_argv`). Security-relevant (disallowed rg flags must be dropped) — find the real regression or ordering cause; never weaken.
> Acceptance: `test-iso` full-file PASS + CI green in unit-3.

> **A6 — TASKS.md tick-guard discipline.** *(fixed in-session)*
> `tests/unit/test_tasks_tick_guard.py::TestCheckTasksTicks::test_real_tasks_file_passes` fails whenever any `- [x]` in `TASKS.md` lacks `| evidence:`. The slurm-cost-cap entry was missing it; evidence added 2026-07-09 (9/9 pass). Standing rule: every future tick includes evidence at write time.

> **A7 — unit-1a shard cancellation (both pythons).** **STATUS: [PARTIALLY LANDED — verify no cancel]** fix applied by splitting unit-1a into unit-1a/unit-1d (rebalance); verify the next CI run shows no CANCELLED verdict for either shard.
> Jobs `test-shard (3.11|3.12, unit-1a)` ended CANCELLED with the Test step failed, while sibling shards completed. Diagnose via `make ci-jobs-anon RUN=<id>` timestamps + `.github/workflows/build.yml` (test-shard `timeout-minutes` and `fail-fast` on the matrix): distinguish (a) job timeout, (b) hung test (last test id printed before cancellation), (c) fail-fast cascade from another shard's failure. Check runs `29053789829` and `29051813598` for chronicity.
> Fix accordingly: if fail-fast — set `fail-fast: false` on the test-shard matrix so one red shard can't mask others; if a hang — find and fix the hanging test (use `make test-hang-debug` locally); if timeout — profile the shard split (`scripts/adaptive_test.py`, unit-1a/unit-1b glob split at build.yml:162-179) and rebalance.
> Acceptance: unit-1a completes (pass or genuine fail) on the next push; no CANCELLED verdicts.

> **A8 — build.yml coverage job can red the pipeline despite "non-gating" intent.** **STATUS: [LANDED — verify in CI].**
> Evidence: `.github/workflows/build.yml:297-306` comment says non-gating, but `uv run coverage report --skip-covered` (line 306) inherits `fail_under = 70` from `pyproject.toml:209`.
> Fix: add `--fail-under=0` to that invocation (mirroring `--cov-fail-under=0` at build.yml:220), or consciously gate it and update the comment. Prefer `--fail-under=0` until item E1 lifts coverage.
> Acceptance: coverage job green on a run where merged coverage dips below 70%.

> **A9 — One test file silently dropped from CI.** **STATUS: [LANDED — verify exactly-once]** fixed via shell-level case filtering in the shard split (not the `build.yml:179` glob-add originally proposed below); verify the file now collects+runs in exactly one shard (no double-execution across shards).
> Evidence: shard `unit-1b` globs `tests/unit/test_[ce]*.py` (build.yml:164) then ejects `--ignore-glob=**/test_*_e2e.py` (line 173); the `other` shard's testpaths (line 179) never re-include `tests/unit/test_*_e2e.py`. Concretely `tests/unit/test_cross_project_borrowing_e2e.py` is NEVER executed in CI.
> Fix: add `tests/unit/test_*_e2e.py` to the `other` shard testpaths (build.yml:179). Then run that file locally first (`test-iso`) — it may have rotted while unexecuted; fix any failures before pushing the glob change (otherwise you redden CI with the fix).
> Acceptance: the file collects+passes in the `other` shard on CI.

> **A10 — pages.yml hardening + test coverage.**
> Evidence: every action in build.yml is SHA-pinned and structurally tested (`tests/security/test_ci_workflow.py:386-400`), but `.github/workflows/pages.yml:28-49` uses floating tags (`actions/checkout@v4`, `actions/setup-python@v5`, `astral-sh/setup-uv@v3`, `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4`) and NO test covers pages.yml.
> Fix: resolve each tag to a commit SHA (`make gh-tag-sha REPO=actions/deploy-pages TAG=v4` — dereferences annotated tags), pin with a `# vN` comment like build.yml, and extend `tests/security/test_ci_workflow.py` (or a sibling `test_pages_workflow.py`) to assert: SHA-pinned actions, trigger paths include `docs/presentation/**` + `scripts/build_deck.py` + `Makefile`, artifact path `docs/presentation/deck`.
> Acceptance: new tests pass; next pages.yml run deploys green (site exists now — see §1).

> **A11 — molecule shard budget drift (P1, not pipeline-red: continue-on-error).**
> **STATUS:** the comment-count fix is LANDED — `build.yml:126/321` now state the real counts (1214 files / 113 scenarios). The timeout-cancellation risk itself is UNVERIFIED — still measure wall time and confirm no timeout cancellations before closing.
> Evidence: build.yml:126/321 comments now say the real counts (1214 test files / 113 sequential scenarios), so each of 4 molecule shards runs ~28 scenarios against a 15-minute `timeout-minutes` (build.yml:320) — timeout-cancellation risk and wasted minutes remain to be measured.
> Fix: measure a shard's wall time from a recent run; raise `timeout-minutes` or go to 6 shards if needed.
> Acceptance: molecule shards complete within budget (no timeout cancellations) on 2 consecutive runs.

> **A12 — xdist pollution structural guard (P1).**
> `tests/conftest.py` `_isolate_root_logger` + `_restore_leaky_env_vars` (frozenset at conftest.py:73-85) are the durable mitigation, but the env-var list can silently go stale. Add a meta-test that scans `tests/` for `monkeypatch.setenv`/`os.environ` writes of vars NOT in the frozenset and fails with the list (or extend the existing `check-test-env-writes` gate hook — see `Makefile` target `check-all-guardrails`).
> Acceptance: meta-guard passes on clean tree; seeding a stray env write makes it fail.

### 3.2 Wave B — Release (P0)

> **B1 — Ship v0.1.0-beta.1.**
> Precondition: Wave A green (CONFIRMED via `make ci-verdict-safe` for the exact SHA).
> Steps: `make release-cut TAG='v0.1.0-beta.1' MSG='Release v0.1.0-beta.1'` → `make verify-release-artifact TAG='v0.1.0-beta.1'` → tick the `TASKS.md` "Ship v0.1.0-beta.1" item with evidence (release URL + asset list from `make release-view TAG=...`).
> Acceptance: `gh release view` shows the tag, non-draft, with the `dist/gludd` asset.

> **B2 — Pages deploy green + live presentation URL.**
> The Pages site exists (build_type=workflow). The next push touching `Makefile`/`docs/presentation/**`/`scripts/build_deck.py` triggers pages.yml; verify with `make pages-status` then fetch `https://sandboxcom.github.io/gludd/` (expect 200).
> **[LANDED — verify: `make deck-build` twice, no warning]** Fixed the cosmetic deck-build warning "tokens not found in template: {{VERSION}}…" — `scripts/build_deck.py --build` warned because the tracked `docs/presentation/deck/index.html` already had baked values; regeneration is now idempotent (anchored spans/comment markers, not one-shot `{{TOKEN}}`s). Note: `scripts/build_deck.py:32-35` still carries a stale comment describing the old one-shot approach — small follow-up to update it.
> Acceptance: pages.yml run `success`; live URL 200; README links resolve (F2).

### 3.3 Wave C — Security residuals (P1 unless noted)

> **C1 — SSRF consolidation of the 14 divergent re-implementations (issue #40 completion + #61 tranche 5/6).**
> **STATUS 2026-07-10: [LANDED — verify in CI]** all 14 SSRF sites consolidated (CGW-13, 186+120 tests; tranche 5 commit `4113f206`).
> Canonical: `src/general_ludd/security/ssrf.py` (`_ip_addr_is_blocked`, `host_is_blocked`, `is_url_blocked` — literal-only/no-DNS, metadata names/IPs, `not is_global`).
> Replace these local classifiers with canonical imports (adding an explicit opt-in DNS-resolving variant to ssrf.py for the connectors that genuinely need resolution, so the DNS-policy split is deliberate):
> 1. `src/general_ludd/connectors/nomad.py:95-126` `_host_is_private` (does DNS; no metadata names)
> 2. `src/general_ludd/connectors/grafana_oncall.py:229-270` `_guard_ssrf` (own suffix blocklist + DNS)
> 3. `src/general_ludd/connectors/kubernetes.py:104-162` `_endpoint_block_reason` (missing `instance-data`/`metadata.goog`/`ip6-*`, `not is_global`, `100.100.100.200`)
> 4. `src/general_ludd/connectors/cilium_hubble.py:62-71,126-177` (narrower `_is_blocked_ip` layered over canonical; misses CGNAT)
> 5. `src/general_ludd/connectors/snmp.py:53-61` `_is_blocked_ip`
> 6. `src/general_ludd/connectors/podman.py:64-88` and 7. `src/general_ludd/connectors/docker_engine.py:68-95` (byte-identical copy-paste `_is_internal_literal_host`)
> 8. `src/general_ludd/git_automation/repo.py:65-166` `reject_unsafe_repo_url` (own 6-literal list + DNS, on the git-clone path)
> 9. `src/general_ludd/issue_sources/base.py:550-613` `_is_internal_host`
> 10-14. `src/general_ludd/issue_sources/{jira.py,monday.py,bitbucket_issues.py,clickup.py,gitlab_issues.py}` local `_is_blocked_host`/`_is_internal_host` copies.
> Also fix the drifted static-detector allowlist at `src/general_ludd/quality/bug_class_registry.py:232` (guard names `is_safe_url`/`is_safe_host`/`validate_url`/`is_allowed_url` match NOTHING real — add `is_url_blocked`, `host_is_blocked`, `is_safe_fetch_url`, `is_safe_endpoint`, `reject_unsafe_repo_url`).
> Test plan: per-site unit tests asserting metadata-IP/hostname and non-global-IP rejection; a consolidation meta-test asserting no module besides `security/ssrf.py` defines a function matching `_is_blocked_ip|_is_internal_host|_host_is_private|_guard_ssrf`.
> Acceptance: meta-test green; all touched connector test files green.

> **C2 — Arbitrary-file-read in adversarial scan endpoint.**
> **STATUS: [LANDED — verify]** scan-file jail + 400 mapping + latent parsed-field 500 fix; 95 passed.
> Evidence (code-verified): `src/general_ludd/routers/adversarial.py:115-120` passes `body.file_path` to `src/general_ludd/security/adversarial_detector.py:605-613` `scan_file()` = bare `open()` with no containment; matched excerpts are echoed in the response. PSK-gated but still an authenticated arbitrary-read primitive.
> Fix: jail to configured scannable roots (reuse the `is_path_within` idiom from `execution/engine.py:808-833`); reject symlinks escaping the root. Also confine the dormant `src/general_ludd/validation/backlog_auditor.py:134-155` reads now, before anything wires it.
> Test plan: new `tests/security/test_adversarial_scan_confinement.py` — `/etc/passwd`, `../` escape, symlink escape → 400; in-root file → 200.

> **C3 — Secrets redaction gap.**
> **STATUS: [LANDED — verify]** exact-match + contextual redaction; 124 passed.
> Evidence: `src/general_ludd/secrets/manager.py:177-183` redaction regex requires ≥20 contiguous base64 chars; shorter/non-base64 secrets in exception text reach `logger.error` unredacted.
> Fix: redact by known-secret VALUE (the manager knows what it just resolved — replace exact occurrences), plus lower the pattern threshold. Add unit tests with short secrets and symbol-bearing secrets.

> **C4 — Budget/spend correctness family (BACKLOG_FINDINGS F1-F6).** [RE-VERIFY each]
> - F1: `estimate_call_cost` returns 0.0 for unknown models → gates treat as free (`src/general_ludd/execution/engine.py:200,210`, `budget_guard_check.py:72`). Fix: unknown-cost → conservative configurable default + loud log, or deny when `strict_budget`.
> - F2: `RunBudgetGuard.check_per_call` fails OPEN on NaN (`budget.py:82`) → `math.isnan` check, fail closed. **STATUS: VERIFIED CLOSED 2026-07-10 — do not re-audit this sub-claim.** `check_per_call` (`controllers/budget.py:82-102`) fails closed on any non-finite `estimated_cost` (`controllers/budget.py:86-91`). Independently, `ModelGateway.check_budget` — the gate actually wired into `call_model` at `gateway.py:679` — also fails closed: NaN `budget_remaining` is clamped to `0.0` and a non-finite `estimated_cost` is clamped to `+inf` so it can never slip under a finite cap (`gateway.py:560-563`).
> - F3: no reservation → concurrent TOCTOU overshoot → reserve-then-commit ledger in `SpendLimiter`.
> - F4/F5/F6: `SpendLimiter.restore` monotonic-ts restart bug; restore double-count; daily-rollover stale reservations.
> - Also D-#14-residual (sibling cost paths on concurrent fan-out) and D-#49 (rolling-cap e2e regression test) and D-#59/#69 (avg_cost regression test) from `TASKS.md:1012-1018`.
> Test plan: `tests/unit/test_budget_guards.py` additions incl. NaN, unknown-model, concurrent-reserve; e2e over-budget dispatch skip already exists (`test_spend_limiter_dispatch_wiring.py`) — extend for reservation.

> **SPD-1 (P1) — Spend persistence: dead restart-survival code path.**
> Evidence: `db/repository.py:1773` `SpendRepository.add()` has ZERO production callers (grep-verified) — `spend_records` is never written, so `daemon.py:437-477 _restore_persisted_spend` (called at `daemon.py:1551-1555`) always rehydrates an empty table on startup. The cap's advertised restart-survival (the premise behind C4/F4) is dead code today.
> Fix: a periodic `EventLoop` flush phase `_phase_flush_spend_ledger`, appended to `PHASE_ORDER` (`event_loop/loop.py:88-105`) immediately after `check_service_credits` (`loop.py:3625 _phase_check_service_credits`), interval-gated by a new `spend_persist_interval_ticks` config (default 60; `<=0` disables). `SpendLimiter` (`controllers/spend_limiter.py`) gains a monotonic `_seq` watermark incremented on every accepted charge (`try_charge`/`record`, :248), plus `unflushed_records()` (records with `_seq` past the last-flushed watermark; alongside `snapshot()` at :310) and `mark_flushed(upto_seq)`; `restore()` (:323) seeds the watermark from the restored records so a restart does not re-INSERT rows already persisted before the crash.
> The flush phase opens a DEDICATED session via the session factory — NOT the shared tick session held across `dispatch_execute_jobs` per E10 — writes each unflushed record through `SpendRepository.add()` (`db/repository.py:1773`, backed by `SpendRecordModel` at `db/models.py:603-622`), then calls `mark_flushed`. The charge-recording seams that populate the in-memory records this phase later flushes (`daemon_wiring.py:311-368 make_spend_guarded_executor`, `loop.py:1874-1887`'s dispatch-time `try_charge`) need no change.
> Failure semantics: a persist failure (DB error) is logged at WARNING and never blocks dispatch — the in-memory limiter stays authoritative for the live cap; only the next tick's flush retries. A restart between flushes loses at most one flush interval's worth of records (bounded exposure), not silent unbounded loss.
> Test plan: (1) unit — `try_charge`/`record` increment `_seq`; `unflushed_records`/`mark_flushed` watermark semantics; (2) unit — `restore()` seeds the watermark so post-restart records are never re-flushed as duplicates; (3) integration — a charge made via `make_spend_guarded_executor` surfaces in the next `_phase_flush_spend_ledger` run (cross-path regression closing the "wired but never exercised" gap); (4) a real restart-survival e2e — flush → simulate process restart (`_restore_persisted_spend`) → assert the rehydrated rolling-window total matches the flushed amount.
> Acceptance: `spend_records` receives rows from a running daemon tick (not just a directly-constructed-repository unit test); `_restore_persisted_spend` rehydrates a non-empty window in the e2e test. Full design: `docs/design/WAVE_D_DESIGNS_2026-07-10.md` § SPD-1.

> **C29 (P1) — LangGraph budget bypass (dormant).**
> Evidence: `ModelGateway.get_chat_model` (`gateway.py:465-533`) has no `check_budget`/`record_spend` call; `LangGraphAgentLoop.__init__` (`execution/langgraph_agent.py:46-56`) takes no `budget_guard` param. The iteration loop lives INSIDE LangGraph (`create_react_agent` → `graph.ainvoke` at `langgraph_agent.py:136`) — unlike `ToolCallLoop`, there is no Python `for` loop to place a per-iteration pre-check inside, so enforcement must wrap the chat-model seam instead. Currently DORMANT: live-wired only behind `use_langgraph_tool_loop` (default `False`; `event_loop/loop.py:2352-2372`), constructed with no `budget_guard` forwarded at `loop.py:2363-2369` — contrast the sibling `ToolCallLoop` branch which DOES receive `budget_guard=self._budget_guard` at `loop.py:2459`.
> Fix: wrap the chat model returned by `_resolve_chat_model()` (`langgraph_agent.py:181`) in a runnable adapter that, per model invocation, (1) runs `budget_pre_check` with the same denial semantics as `tool_loop.py:174-186` and (2) records spend via the gateway's existing billing seam using the response's token usage. Thread `budget_guard` through `LangGraphAgentLoop.__init__` (default `None` only for the benchmark `_run_plain` path); pass `self._budget_guard` at `loop.py:2363-2369` (mirroring `:2459`); forward it through `make_langgraph_tool_loop` (`agents/capabilities.py:198-219`). Defense-in-depth: keep a total-iteration cap in the per-tool wrappers built by `_build_langchain_tools` (`langgraph_agent.py:192`).
> Constructor sites needing the new param: `event_loop/loop.py:2363`, `agents/capabilities.py:214`, `benchmark/langgraph_bench.py:198` (`None` is fine there — benchmark harness, no live budget). Test files needing signature updates: `tests/unit/test_langgraph_tool_loop.py` (~13 instantiations), `tests/integration/test_langchain_daemon_integration.py` (:63-92, :119-122), `tests/unit/test_langgraph_benchmark.py` (:306, :334, :689).
> Acceptance: exhausted budget stops dispatch before the next model call (call-count assertion); every completed model call under the LangGraph path has a matching spend record.

> **C5 — Integrity store (H1/H2/M1).** [RE-VERIFY]
> `integrity_db.json` baseline unsigned; corrupt store silently re-baselines; non-canonical HMAC payload allows field-injection. Files: grep `integrity_db` under `src/general_ludd/`. Fix: HMAC the canonical-JSON baseline with the PSK-derived key; corrupt store → fail closed + require explicit re-baseline command; canonicalize (sorted keys, separators) before MAC.

> **C6 — Model gateway (H1/M1/M3).** [RE-VERIFY]
> `src/general_ludd/models/gateway.py:561`-area: caller kwargs can override SSRF-validated `base_url`/`api_key`; no request/connect timeout; alias-resolved URL leaked in SSRF error text. Fix: strip/deny `base_url`/`api_key` in per-call kwargs after validation; default httpx timeout; redact resolved URL in errors.

> **C28 (P1) — Failover follow-ups (post-`803b75c5`).**
> Evidence: `call_model_with_fallback` (`models/gateway.py:1620-1674`) discards the per-attempt exception context gathered by `_walk_fallbacks` (the `_attempts` return value is unused) and, on total exhaustion, raises a bare `CircuitBreakerOpenError` (`gateway.py:1663-1674`) without routing through `_enrich_all_down_message` (defined `gateway.py:247`, already used at `:1173` and `:1390` on other call paths) — so the structured all-down error D17 landed has zero callers on THIS path. Separately, the fallback concurrency gate (`_fallback_semaphore`, `gateway.py:1394-1410`) is a bare `threading.Semaphore` acquired with a blocking `with` at `_call_fallback` (`gateway.py:1431`) — no timeout: a hung secondary provider can hold a slot indefinitely and, because `call_model_with_fallback` is invoked via `asyncio.to_thread` from async callers, starve the shared thread-pool workers app-wide (ties into C11's ThreadPoolExecutor-saturation note), not just the calling request. Also undocumented: the failover walk is TRANSITIVE — each hop follows ITS OWN `fallback_profiles`, so a chain can cascade through more than one fallback hop with no depth cap or operator opt-out. `ModelFailoverChain.record_failover` (`models/failover.py:38-46`) appends to `self._failover_events` with no lock (cosmetic — single-threaded call sites today, but a latent race if that changes).
> Fix: (a) accumulate per-attempt exception summaries in `call_model_with_fallback` and surface them via `_enrich_all_down_message` even on the bare-raise path (net-new behavior on a currently-uncalled path, not a regression risk); (b) wrap the semaphore acquire in a bounded wait (e.g. `asyncio.wait_for` around a thread-safe acquire, or a stdlib `Semaphore.acquire(timeout=...)`) with a fail-fast error distinguishing "no fallback capacity" from "all profiles down"; (c) document the transitive-cascade behavior (docstring + `docs/audit/FAILOVER_GAPS.md`) and add an explicit per-profile opt-out (e.g. `allow_transitive_fallback: bool`, default `True` for back-compat); (d) lock `record_failover`'s list append.
> Test plan: unit test asserting an all-down raise carries the enriched multi-attempt message; unit test for the bounded semaphore wait (mock a hung acquire, assert bounded wait + a distinct timeout error); integration test proving a 2-hop transitive cascade fires only when opted in; concurrency test for `record_failover`.

> **C7 — MCP transport allowlist (M2).** Verified CLOSED (argv allowlisting per interpreter landed; version pins fixed for the npm-family/`uvx` path) — do not re-audit. One LOW residual filed separately as **C27 / MCP-1**.

> **C8 — Hot-reload / worker broadcast family (reload H1/H2/M1-M4).** [RE-VERIFY]
> `src/general_ludd/reload/hot_reloader.py`, `reload/worker_broadcast.py`: snapshot→swap TOCTOU; **unauthenticated worker registration leaks PSK to arbitrary address** (highest of the family); no concurrency guard; dict-mutation during iteration; symlink/`..` bypass; no rate limit. Fix in that order; registration must require the PSK it would receive.

> **C9 — self_update deny-list family (F1-F5).** [RE-VERIFY]
> `src/general_ludd/self_update/`: deny-list leading-slash drift vs `security/capability_lattice.py`; parent-dir TOCTOU; cwd-anchored resolve; empty-targets false "applied". Normalize both lists through one path-canonicalizer; assert drift in a unit test comparing the two modules' deny sets.

> **C10 — Execution engine (#1/#3/#4/#5).** [RE-VERIFY]
> `src/general_ludd/execution/engine.py`: benchmark `create_task` in sync method swallowed; blocking `_run_tests` on loop; deferred-commit race; `_background_tasks` never drained. Fix: `asyncio.to_thread` for blocking runs; hold+drain task refs on shutdown.

> **C11 — Event loop (#1-#3).** [RE-VERIFY]
> `src/general_ludd/event_loop/loop.py:1258`-area: DB session pinned across multi-minute execution (already partially addressed by `_dispatch_execute_job_isolated` session-per-job — verify remaining pins); shared ThreadPoolExecutor saturation; unbounded gather fan-out. Fix: per-phase session scopes, bounded semaphore on fan-out.

> **C12 — Events/hooks (H1, B1-B3).** [RE-VERIFY]
> `src/general_ludd/events/hooks.py`: `fire()` list-mutation-during-iteration; EventBus zero locking; double-invocation of async callbacks. Fix: copy-on-fire, lock registration, idempotent scheduling.

> **C13 — Self-improve gate bypasses (×4).** [RE-VERIFY]
> `src/general_ludd/self_improve/gate.py:25` `auto_queue=True` bypasses approval; `allow_auto_promote` backdoor; human-approval path dead code; `POST /admin/self-improve/run` bypasses the gate (`routers/self_improve.py:343,386`). Fix: single choke-point gate; admin route goes through it; default auto flags OFF.

> **C14 — Permissions / capability lattice (×4).** [RE-VERIFY]
> Deny-list drift (`security/capability_lattice.py`); `_intersect_constraints` widens file scope (`security/permissions.py:514`); STS re-delegation escalates TTL; denied grants not enforced through delegation. Property-test the intersection (result ⊆ both inputs).

> **C15 — Tool-call loop (×4).** [RE-VERIFY]
> `src/general_ludd/execution/tool_loop.py`: capability lattice bypassed on Phase-2 loop; no per-response tool-call cap; args unvalidated vs `input_schema`; VariableStore key injection. Fix: route Phase-2 through `DynamicDispatcher`'s lattice check; cap + validate.

> **C16 — Filestore RCE.** [RE-VERIFY]
> `src/general_ludd/filestore/bootstrap.py:266-308`: downloads chmod+executed with no checksum/signature — RCE on hijacked redirect. Fix: pinned SHA-256 per artifact (fail closed), keep D-#5-residual (bundle signing) as follow-up.

> **C17 — Git automation (GA-3 + squash).** [RE-VERIFY]
> `src/general_ludd/git_automation/repo.py`: `merge_branch` bypasses the per-repo lock+timeout wrapper; squash path `check=False` fail-open; plus orchestration-audit #63 (per-repo serialization not wired into `_run_git`; 3/8 race tests fail) and #64 (branch-name 1-second collision — use todo-id + short-uuid).

> **C18 — Accounting (×3).** [RE-VERIFY]
> Blocking `subprocess.run(git diff)` on the event loop; no tenant scoping; NaN/Inf USD poisons JSON. Files: `src/general_ludd/accounting/`.

> **C19 — Cross-tenant traces XT-3/XT-4.** [RE-VERIFY — a fix (`86389be`) is recorded; confirm and close, else finish]
> `/api/traces` cross-tenant leak. Acceptance: two-project e2e proving project A cannot read B's traces.

> **C20 — Worker fail-open auth.** [RE-VERIFY]
> Worker (`src/general_ludd/worker/app.py`) fails auth-OPEN by default per BACKLOG_FINDINGS. Note `tests/security/test_daemon_auth_redteam.py` asserts daemon-side fail-closed — mirror that contract on the worker; default deny without PSK.

> **C21 — ALPHA4 leftovers: RE-TRIAGED against code 2026-07-10.** Most of the old list is closed — do NOT re-implement the FIXED/MITIGATED entries below; only the three STILL-OPEN bullets are work. (The FIXED entries formerly listed here now live in §3.0 "Already FIXED" — do not duplicate them here.)
> **MITIGATED (no action unless listed residual):** M-13 (CAS optimistic lock at update sites — stronger than the audit assumed, see §3.0); M-7 (bounds/caps enforced; residual LOW: `_sort_by_ts` NaN/Inf sort key needs a finite-key guard at BOTH `src/general_ludd/connectors/base.py:304-309` AND `src/general_ludd/observe/facade.py:338`); skills fetcher (RemoteSkillFetcher already has a 1MB cap; residual LOW-MED: `GitHubSkillSource` at `src/general_ludd/skills/fetcher.py:109-137` uncapped — add the same cap); alembic URL — MITIGATED (`env.py` reads `DATABASE_URL` override); M-10 circuit-breaker per-process state is fine under the enforced single-worker clamp (revisit in D19).
> **NON-ISSUE:** M-5 `/docs` prefix (bad repro).
> **STILL OPEN (real work):**
> - `src/general_ludd/validation/runner.py:31-53` — subprocess not symlink-confined post-validation, and its caller `reload/self_improve.py:84-89` doesn't confine either. Fix: resolve+jail the workdir before exec.
> - `src/general_ludd/event_loop/loop.py:1330-1361` — PID/concurrency cap applied AFTER rows are marked ACTIVE (over-cap todos sit ACTIVE-without-dispatch in the window). Fix: cap check before the CAS claim, or requeue-on-cap in the same transaction.
> - `src/general_ludd/event_loop/loop.py:1031-1035` — `_dispatch_review_job` `to_thread(run_playbook)` with no timeout. Fix: `asyncio.wait_for` with a config-driven review timeout + BLOCKED transition on expiry.

> **C30 (P1) — `TodoModel.version` wire-vs-remove decision.**
> Evidence: `TodoModel.version` (`db/models.py`) exists but is not wired as SQLAlchemy's `version_id_col` — repository-level compare-and-swap (`db/repository.py:277-315,565-605`) is the SOLE concurrency guard today and, per the M-13 mitigation note above, is verified stronger than the original audit assumed. The `version` column is therefore either dead weight or a redundant defense-in-depth guard, depending on intent — an undocumented ambiguity, not an active bug.
> Fix: pick one and document it — (a) wire `version_id_col=TodoModel.version` as defense-in-depth alongside the existing CAS repository guard (verify it does not double-increment or conflict with the CAS path), or (b) remove the unused column via a migration + a `TASKS.md` rationale citing the CAS guard as sufficient. Either way, add a concurrent-writers test proving two simultaneous updates to the same todo cannot both silently succeed.
> Test plan: a CAS concurrency test (sibling of the repository's existing CAS tests) — two concurrent updates on the same row, assert exactly one wins and the loser gets a detectable conflict signal.

> **C22 — SSTI sweep residuals.** [RE-VERIFY]
> `docs/audit/ssti_bugclass_sweep.md`: engine.py reachability (CRITICAL), core_runner/templating full-Templar trusted-only contract (HIGH ×2 — enforce with a boundary assert), skills frontmatter injection (MED ×2), loader.py contributory.

> **C23 — Connector security audit F1-F9 + review tail.** [RE-VERIFY]
> `docs/audit/connector_security_audit.md`: dead `is_safe_endpoint` paths, path interpolation, exception-text secret leak, single-label hostname pass, `is_global` divergence (folds into C1), elasticsearch narrow blocklist, Beats no byte cap, dual resilience contracts, live transports re-resolving DNS. ~20 connectors + receiver router still unreviewed — schedule a sweep.

> **C24 — Daemon/network defaults.** [RE-VERIFY]
> Daemon default bind `0.0.0.0` → `127.0.0.1` unless configured; compute `allowed_cidr 0.0.0.0/0` default (POST_ALPHA4 finding, no closure evidence) → require explicit CIDR.

> **C25 — Remediation endpoint idempotency.** [RE-VERIFY]
> `POST /admin/remediation/remediate` lacks an idempotency guard (`src/general_ludd/routers/remediation.py`). Fix: idempotency-key or dedupe on (action, target, window) via the `remediation_actions` table. Note: the router is now wired+registered (see D21 — [LANDED — verify]); this idempotency guard is the sole remaining piece before D21 closes fully.

> **C27 — MCP-1 (LOW): transport argv validation residual.**
> `src/general_ludd/mcp/transport.py` python/node launcher family lacks argv validation (`:28-30`, `:136-138` — `_validate_package_spec` only covers the npm-family/`uvx` path). Rest of MCP M2 (allowlisting, version pinning across the family) verified CLOSED — do not re-audit.
> Fix: extend argv validation to the python/node launchers (module/script paths jailed to repo, no arbitrary flags), mirroring the existing npm-family/`uvx` guard.

> **C26 — Async / process-lifecycle residuals (2026-07-09 async audit).**
> Items 1-4 of that audit (sync_bridge, issue_ingestor `urlopen`, `admin_connectors_health`, `WriterProcess.stop`) were **landed 2026-07-10 — verify in CI, do not re-fix**. Still open:
> 1. **Production aiosqlite closed-loop guard missing** — the session-scoped worker patch exists only in `tests/conftest.py` (test-only). The same `Event loop is closed` → dead-worker-thread failure mode exists in the production daemon during shutdown. Fix: an equivalent guard at daemon shutdown (drain/close aiosqlite connections BEFORE closing the loop in the lifespan teardown), not a monkeypatch.
> 2. `src/general_ludd/daemon.py:2079` — silent `suppress` on pipeline/MCP shutdown hides real teardown failures → log at WARNING with exception detail.
> 3. Ornith MCP subprocess `PIPE` never drained → pipe-buffer deadlock risk on chatty children. Fix: drain task or redirect to file.
> 4. `src/general_ludd/routers/stream.py:180` — kill-without-reap leaves zombies → `wait()` after kill.
> 5. `src/general_ludd/runner/background_test_runner.py` — no zombie reaping; `os.kill(pid, 0)` misreports zombies as alive → use `Popen.poll()`/`waitpid(WNOHANG)`.
> 6. `src/general_ludd/daemon.py:1382` `_langgraph_call_model` returns silent `None` on failure → raise or return a structured error the caller must handle.
> 7. Module-level `_daemon_state` global (race-prone shared state) → move into app state / explicit injection.
> Test plan: shutdown-ordering test asserting no `PytestUnhandledThreadExceptionWarning`-class errors on daemon stop; zombie-reap unit tests with a short-lived child process.

### 3.4 Wave D — Feature completeness

> **D1 (P0-adjacent, user-visible CRITICAL) — Wire the real onboard providers.**
> **STATUS: [LANDED — verify in CI]** wiring is complete, including `--project`/`--subscription` passthrough (local commit `2543152b`; see §3.0 and §3.0.1). This detailed entry is kept for historical evidence and test-plan completeness — confirm via CI before striking it entirely.
> Evidence (code-verified, pre-fix baseline): `src/general_ludd/onboard/__init__.py:44-89` registers `_BaseStub` classes raising `NotImplementedError` for aws/gcp/azure, so `gludd onboard <provider>` (CLI `src/general_ludd/cli.py:994-1093`) always exits 3 outside `--dry-run` — while REAL, unit-tested implementations sit unwired in `onboard/aws.py:154` (boto3 STS + IAM probe), `onboard/gcp.py:52,114,161`, `onboard/azure.py:44,106,162`.
> Fix: point `SUPPORTED_PROVIDERS` at the real classes (wrap the gcp/azure module-level functions in the provider protocol); delete `_BaseStub`.
> Test plan: extend `tests/unit/test_onboard_aws.py` pattern to gcp/azure; add a CLI-level test that `gludd onboard aws` reaches the real `create_role_instructions` (mock boto3).
> Acceptance: non-dry-run onboard produces provider instructions; CLI exit 0 on the instruction phase.

> **D2 (P1) — Wire `run_project_gate` into review/reconcile.**
> Evidence: `src/general_ludd/quality/project_gate.py:35` fully implemented, zero callers. Only `run_project_check` (single check, `mcp/builtins.py`) and `execution/engine.py:194 _run_tests` are live — an external project's lint/typecheck failures never gate a merge decision.
> Fix: call `run_project_gate` from the review/decision path (`src/general_ludd/review/decision_applier.py` `verify_completion` gate is the natural seam) for todos whose project has a `project.yml`; record per-check results into `task_returns.result` payload; surface via CLI.
> Test plan: integration test — todo on a fixture project with a failing lint check → decision blocked; passing project → completes.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D2.

> **D3 (P1) — Generalize the self-improve APPLY path to external projects.**
> Evidence: gap-finding half is project-neutral (`self_improve/harness.py`), but `src/general_ludd/reload/self_improve.py:88` hardcodes `test_commands=["make test-unit"]`; `reload/hot_reloader.py` reloads the RUNNING daemon's own `sys.modules` (cannot target external repos by construction); `routers/self_improve.py:343,386` builds `SelfImprovementWorkflow()` with no project routing; `_apply_approved_config_change` anchors on `Path.cwd()`.
> Fix: (a) inject the target's `ProjectProfile` (from `project_runner/detect.py`) so `test_commands` come from the detected toolchain; (b) split "apply" into `SelfApplyStrategy` (current hot-reload, only when target == gludd itself) vs `ExternalApplyStrategy` (write to the project checkout → run `ProjectCommandRunner` gate → commit via `GitAutomation`); (c) route by `project_id` end-to-end from the router.
> Test plan: fixture external Python + Node projects; e2e: self-improve proposes+applies a fix in the external checkout and the project gate passes; unit tests for strategy selection.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D3.

> **D4 (P1) — DAST driver + findings parser.**
> Evidence: SAST real (`project_runner/findings.py` parses semgrep/bandit); DAST nonexistent (`docs/examples/project.yml:69-73` comment only; `docs/design/PROJECT_RUNNER.md:68` "Later").
> Fix: `project_runner/dast.py` — lifecycle wrapper (start target via profile's serve command, wait healthz, run scanner, teardown) with a ZAP-baseline JSON parser into the same `Finding` model; declare via `project.yml` `dast:` block; allowlisted executables only.
> Test plan: unit tests with canned ZAP JSON; integration with a dummy HTTP server + stub scanner binary (no network).
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D8.

> **D5 (P2) — Compute discovery + auto-select.**
> Evidence: `src/general_ludd/infra/terraform.py:607,630-633` hardcodes vSphere `DC0/Cluster0/datastore0/VM Network`; kubernetes has no dispatch entry (`terraform.py:159-169` falls to `_generate_generic`); `ProviderRegistry.get_cheapest_for_gpu` (`infra/providers.py:225-244`) sorts a static table and has no callers; `provider` is a required caller-supplied field (`routers/compute.py:74`).
> Fix in slices: (1) k8s dispatch entry generating real manifests/HCL; (2) vSphere params from config with pyvmomi inventory validation; (3) auto-select: make `provider` optional, resolve via `get_cheapest_for_gpu` + `pricing_intel` + budget, record into UtilizationTracker. (The `compute-resource-discovery` skill in `.claude/` covers this feature's full design.)
> Test plan: per-slice unit tests; HCL golden files; auto-select scoring table test.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D5.

> **D6 (P2) — Wire `OrchestrationPlanner` (#54) or delete it.**
> Evidence: `src/general_ludd/scheduling/planner.py:60` referenced only by itself; every real caller uses `Scheduler` directly; `docs/design/feature_package_wiring.md` recommends wiring.
> Fix: use it in `event_loop/loop.py::_dispatch_jobs_via_scheduler` as the batch-planning seam (it wraps `Scheduler.plan`), OR delete the module + its tests with a rationale line in TASKS.md. Wiring is preferred (it exists to replace ad-hoc parallel-batch judgment).

> **D7 — Pause/resume (#35) — implementation-ready plan (4 ordered items).**
> Current state (code-verified 2026-07-09): SLICE 1 (PauseController + durable PauseStore, `657e2b13`) and SLICE 2 (gateway `ModelPausedError` + claim gate) landed. `routers/pause.py` ALREADY EXISTS and is registered (`daemon.py:2874`) — only the CLI remains for SLICE 4. SLICE 3 is partially landed but **triply dead**: (1) `app.state._hibernation_controller` is never assigned anywhere in src, so `quiesce_project` short-circuits at `controllers/pause_controller.py:126-127`; (2) even if wired, the snapshot built at `:133-138` never satisfies `should_dehydrate`, so zero handles are captured; (3) resume drops `agent_handles` on the floor, and `HibernationStore`'s MAC key is ephemeral per-process (`agents/hibernation.py:217`) so pause → restart → resume can never hydrate. Also: `pause_controller.py:10-13` docstring is stale ("no daemon wiring" — false since SLICE 2); fix it in whichever item touches the file first.
>
> **D7.1 = D-#35.2b (P1) — Persist-before-mutate + lock-free is_paused + router ordering.**
> Bugs: memory is mutated at `pause_controller.py:186` and `:198-201` BEFORE `_persist` (a persist failure or crash leaves memory and disk divergent; restart un-pauses); `is_paused()` reads shared state lock-free (race under churn); `routers/pause.py:76-86` quiesces BEFORE calling `pause()`, so the claim/model gate is still open mid-quiesce.
> Fix: persist first, then swap an immutable copy-on-write `frozenset` of paused projects (atomic reference swap makes lock-free `is_paused` safe); reorder the router to `pause()` → quiesce.
> Tests: `tests/controllers/test_pause_persist_ordering.py` (persist-failure leaves state unchanged; restart preserves pause), `tests/controllers/test_pause_concurrency.py` (concurrent readers under pause/resume churn).
>
> **D7.2 = D-#51 (P1) — Construct + wire HibernationController.**
> Fix: give `HibernationStore` a durable `mac_key` ctor param, loaded from `<pause_base>/secrets/hibernate_mac.key` using the same fail-closed loader pattern as `controllers/pause_store.py:190-251`; assign `app.state._hibernation_controller` after `daemon.py:1188-1189`; add an optional `hibernation` param to `AgentDispatcher.__init__` and pass it at `daemon.py:1873-1878`.
> Tests: `tests/unit/test_hibernation_durable_key.py` (key survives process restart; MAC verifies across processes; bad key fails closed), `tests/unit/test_daemon_hibernation_wiring.py` (app.state populated; dispatcher receives it).
>
> **D7.3 = D-#35.3 (P1) — Quiesce at the DISPATCHER seam + rehydrating resume.** (Depends on D7.1 + D7.2.)
> Fix: on pause — gate first (D7.1 ordering), then dehydrate EVERY active task for the project unconditionally (bypass `should_dehydrate`: it is a RAM policy, not a pause policy), capturing description/prompt/project_id into `AgentEnvironmentSnapshot.scratch`; new `dispatcher.cancel_active_for_project` delivering a pause-marked `CancelledError` → `AgentTaskResult(status="paused")`; new `PauseController.attach_quiesce_result` (persisted-first per D7.1). `PauseRecord` gains `agent_handles: list`, `quiesce_status: Literal["none","clean","degraded"]`, `quiesce_errors` — all with defaults so previously-persisted records stay valid. On resume — `controller.resume()` FIRST, then per handle: `model_validate` → `hydrate_async` → rebuild `AgentTask` → `dispatch_one` → `discard_async` only after successful dispatch; hydrate failure = warn, keep the snapshot file, resume still succeeds (degraded).
> Tests: `tests/unit/test_pause_quiesce_slice3.py`, `tests/unit/test_pause_resume_rehydrate.py`, `tests/integration/test_pause_resume_e2e.py` (pause blocks claim+model call; in-flight task dehydrated; restart; resume rehydrates and completes).
>
> **D7.4 = D-#35.4 (P2) — CLI `gludd pause` / `gludd resume`.**
> Fix: subcommands following the `_http_call` convention at `src/general_ludd/cli.py:254-283`, hitting the existing `routers/pause.py` endpoints; print quiesce_status/errors.
> Tests: CLI unit tests with a mocked daemon (happy path, daemon-down error path).
>
> Sequencing: D7.1 ∥ D7.2 → D7.3 → D7.4, with a SINGLE WRITER for `daemon.py` and `routers/pause.py` across these items.

> **D9 (P1) — Auto-remediation never fires on tick (#52).**
> Evidence: only referenced in `docs/SESSION_HANDOFF_2026-07-03.md:69`. Candidate causes listed there: non-atomic claim+dispatch, suppressed over-cap requeue, uncounted timed-out batches, lease-acquire failure leaving ACTIVE-no-lease todos.
> Fix: trace `MisconfigDetector`/remediation phase in `event_loop/loop.py` PHASE_ORDER; add an integration test that seeds a detectable misconfig and asserts a `remediation_actions` row after N ticks.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D6 (#52 subsection).

> **D10 (P1) — Commit-path file-claim livelock (#53).**
> Evidence: `src/general_ludd/event_loop/loop.py:2521-2543` [RE-VERIFY exact lines] — claims in `FileClaimRegistry` can livelock the commit path.
> Fix: total-order claim acquisition (sorted file list) + claim TTL + backoff-with-jitter; test with two synthetic todos claiming overlapping files concurrently.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D6 (#53 subsection).

> **D11 (P1) — Subagent orchestration defects (#57).**
> Evidence: `docs/audit/status_security_orchestration.md` — nesting/escalation/control-loop/spiral defects, "zero implementation found". Define + implement: max nesting depth, capability non-escalation on child spawn (child caps ⊆ parent caps — ties into C14), dispatch-rate control loop, spiral detection (same-task re-dispatch counter with cutoff).
> Test plan: unit tests per guard; adversarial test spawning a self-re-dispatching agent chain that must be cut off.
> Design: docs/design/WAVE_D_DESIGNS_2026-07-10.md §D7.

> **D12 (P2) — Slack connector.**
> Evidence: `SESSION.md` Known Gaps #9; no Slack anywhere in `connectors/` (~90 modules) or `issue_sources/` (12 adapters).
> Fix: `connectors/slack.py` following the established connector base (auth, SSRF-guarded webhook/API URL via C1 canonical module, notification send + channel history read), registered in `connectors/registry.py`; optional `issue_sources` intake from a channel.
> Design (outbound-notifications slice only): docs/design/WAVE_D_DESIGNS_2026-07-10.md §D4; the inbound/connector half remains undesigned.

> **D13 (P2) — `security_backlog.py`: wire or delete.**
> Evidence: `src/general_ludd/security/security_backlog.py:53` `run_backlog_checks()` — 20/24 checkers return hardcoded "deferred", 4 return stub `True`; zero production callers (tests only). It LOOKS like a security gate and gates nothing — an audit-trust hazard.
> Decision rule (never leave as-is): either implement real checkers for the D-items that remain open after Wave C and wire into `make security` / CI, or delete module + tests with a TASKS.md rationale.

> **D14 (P2) — Expose `background_test_runner`.**
> Evidence: `src/general_ludd/runner/background_test_runner.py` implemented + unit-tested, reachable only via `python -m`. Fix: `make test-bg-runner ...` target and/or `gludd test-bg` CLI subcommand.

> **D15 (P2) — Pricing sources static→live.**
> Evidence: `src/general_ludd/pricing_intel/sources.py` legend + ~9 `# TODO(integration)` markers (lines 216, 303, 399, 717, 821, 1175, 1541, 1639) — RunPod/AWS/GCP/model prices are dated static tables feeding real spend decisions.
> Fix: live fetchers with TTL cache + static fallback (offline-safe), per source; emit staleness metadata so budget decisions can see data age.

> **D16 (P2) — Toolchain/parser breadth.**
> Add ecosystems to `project_runner/detect.py` (already: Python/Node/Go/Rust/Make) and parsers to `project_runner/findings.py` (already: semgrep/bandit): eslint JSON, golangci-lint, cargo-audit, trivy (trivy is already allowlisted in `docs/examples/project.yml`). One PR per parser, golden-file tests.

> **D17 (P2) — Failover xfail gaps.** **STATUS: [PARTIALLY LANDED — verify]** `src/general_ludd/models/gateway.py` now implements correlation-ID propagation (item 12), structured all-down errors (item 6b), and the `failover_count` facet (item 14b); item 13a (fallback concurrency cap) remains unimplemented. xfail-to-pass flips are in flight — confirm each of 6b/12/14b now passes (not xfail) before closing, and land 13a to close the item fully.
> `docs/audit/FAILOVER_GAPS.md`: 6b all-down structured error; 12 correlation-ID propagation; 13a fallback concurrency cap; 14b failover_count facet.

> **D18 (P2) — Non-ephemeral account creation.**
> `src/general_ludd/routers/account.py:159-166` returns 501 for `ephemeral=false` (documented). Implement persistent accounts atop `EphemeralAccountManager`'s storage or explicitly keep 501 + document in README/API docs.

> **D19 (P2, gated on owner go-ahead) — Postgres path / multi-worker (beta.3).**
> SQLite single-writer ceiling (`_clamp_workers_for_sqlite`); STABILIZATION_PLAN WP-B4. Do not start without explicit approval; alembic parity now green so the old blocker is gone.

> **D20 (P2) — Dedup/coherence cleanups.**
> `docs/audit/batch3_dedup_coherence.md` 8 duplicate pairs + missing `__init__.py` (pipeline/, issue_sources/) [RE-VERIFY]; execute `docs/audit/misconfig_detector_dedup_decision.md` 6-step deletion/port plan; `docs/audit/dry_deadcode_audit.md` items 1-7 (frontmatter dup, install-guard dup, `normalized_record` ignored…); `docs/audit/model_routing_coherence_check.md` 5 gaps (TaskType weight divergence etc.).

> **D21 (P1) — Orphaned routers: wire or delete.**
> **STATUS: [LANDED — verify]** both routers are now wired+tested (CGW-9) — `routers/remediation.py` and `routers/eval.py` are registered in `register_all` with PSK auth + endpoint tests. Remaining open: land C25's remediation idempotency guard (the only piece not yet closed).
> Evidence (code-verified via coverage map, pre-fix baseline): `src/general_ludd/routers/remediation.py` (~319 lines) and `src/general_ludd/routers/eval.py` (~87 lines) were never registered — not in `routers/__init__.py::register_all` and not directly in `daemon.py` (contrast: `self_update.register(app, daemon_state)` at daemon.py:2811). Their endpoints were unreachable in production while looking finished in the tree.
> Fix (remaining): land C25's remediation-endpoint idempotency guard (idempotency-key or dedupe on (action, target, window) via the `remediation_actions` table).

### 3.5 Wave E — Quality / coverage / process (P1 unless noted)

> **E1 — beta.3.2 coverage lifting.**
> Evidence: `docs/audit/COVERAGE_AUDIT_2026-07-06.md` FAIL — 149/577 files below the 85% per-file threshold (overall 83.17%); true gap ~60-80 files. `TASKS.md:866` open item; WP-C1 already lifted gateway/event_loop/dispatcher/db-repository.
> Method: `make audit-coverage` for the ranked list; lift in batches of ~10 files per agent (disjoint), unit tests only, no test-shaped tautologies; after each batch re-run `make audit-coverage`.
> Acceptance: `make gate-audit` (85% per-file) passes; then flip `pyproject.toml` coverage `fail_under` 70 → 85 and remove A8's `--fail-under=0` in the same commit.

> **E2 — e2e audit closure.**
> Evidence: `docs/audit/E2E_AUDIT_2026-07-06.md` — 16 failing e2e (15 in `tests/e2e/test_environment_e2e.py`) — **STALE/RESOLVED**: 2026-07-10 re-run shows 14 passed (`tests/e2e/test_environment_e2e.py`) + 16 passed, 2 skipped (the broader environment-e2e suite); the audit's failing set no longer reproduces. Remaining work: ~40 src modules with zero e2e coverage — add e2e for the top-10 riskiest uncovered modules (daemon lifecycle, reconcile, review).

> **E3 — Lint/type config gaps.**
> Evidence: `docs/audit/LINT_CONFIG_AUDIT_2026-07-06.md`: mypy excludes security/sandboxes; tests/ never type-checked; coverage fail_under 70 vs gate 85 (folded into E1); file-level noqa; undocumented `disable_error_code`; bare-"pass" `exclude_lines`; no `.pre-commit-config.yaml` [RE-VERIFY — `make install-hooks` implies one exists now]. Close each or document why not.

> **E4 — noqa guardrail 3-layer fix.**
> Evidence: `docs/audit/NOQA_GUARDRAIL_ROOT_CAUSE_2026-07-06.md` — proposed (edit-time hook + behavior-pin test + AGENTS.md rule), not built. Build all three layers so agents can't silence lint instead of fixing it.

> **E5 — Plugin leanness (P2).**
> `tests/unit/test_process_overhead.py` threshold was raised to <6000 to unblock CI (total 5334). The test's intent stands: refactor the .opencode plugins (enforce-make.ts 1047, enforce-stop.ts 1062, enforce-floor.ts 801, enforce-delegate.ts 739 lines) toward shared helpers, then ratchet the threshold back down (5334 → 4500 → 3500) in steps.

> **E6 — Audit-doc re-triage.**
> The docs are materially behind the code (see §3.0). Re-triage `docs/audit/BACKLOG_FINDINGS_2026-07-01.md` and `NEW_FINDINGS_TRIAGE_2026-06-18.md` against current master, annotating each item FIXED (commit) / OPEN (work-item id in this spec) / REFUTED. This is what keeps [RE-VERIFY] cheap for everyone after you.

> **E7 — Zero-test modules (code-verified structural map: 602 src files vs 1214 test files, 22,558 collected, 0 collection errors).**
> Six src modules have NO test references at all (originally eight — `routers/remediation.py`/`routers/eval.py` struck: now wired+tested per D21) — write unit suites in this priority order:
> 1. `src/general_ludd/cli_payment.py` (201 lines, PCI-adjacent payment-vault CLI — HIGH risk untested).
> 2. `src/general_ludd/self_update/router.py` (470 lines, deprecated-but-reachable capability-assigning router — HIGH-MED; NOTE `tests/unit/test_self_update_router.py` misleadingly tests apply/model logic, NOT this router — rename it or extend it honestly).
> 3. ~~`src/general_ludd/runtime/release_orchestrator.py` (60, release composition point)~~ **STALE — now covered** (verified 2026-07-10: `tests/unit/test_completion_audit_wiring.py:261` `TestReleaseOrchestratorWiring::test_orchestrator_builds_and_validates` imports and exercises it; remaining modules 1-2 and 4-6 re-verified as still zero-reference the same day). 4. `src/general_ludd/renderers/cache.py` (63, TTL expiry logic). 5. `src/general_ludd/event_loop/benchmark.py` (42). 6. `src/general_ludd/renderers/executor.py` (17, shim).
> Acceptance: each module has a dedicated test file exercising its public functions; `make audit-coverage` shows them ≥85%.

> **E8 — Router HTTP layer systematically thin.**
> Nine routers are touched ONLY by the generic registration smoke test — no endpoint-level tests (originally ten — `routers/security.py` struck: **[LANDED]** 58 endpoint tests now cover it). Write FastAPI TestClient endpoint tests (happy path + auth-required + validation error per endpoint), priority order: `ornith.py` (323), `account.py` (212 — its docstring falsely claims router coverage; fix the docstring too — NOTE: this claim is UNCONFIRMED, verify before treating as closed), `adversarial.py` (149 — coordinate with C2's confinement tests), `benchmark.py` (130), `model_performance.py` (119), `memory.py` (94), `mcp.py` (67), `worktree.py` (60), `variants.py` (36).

> **E9 — Skip-smell cleanup.**
> - Hook-liveness guardrail tests (3 CI-skip sites — NOT ~30; the ~74 "not yet created" skip-stubs below are a separate, unrelated bucket) are `skipif CI=="true"` — the guardrail-liveness suite is disabled exactly where it matters. Make them CI-runnable (hermetic fixtures) or convert to an explicit opt-in marker with a documented local runbook.
> - `tests/integration/test_local_inference_integration.py:143` — `skipif(True, ...)`: an unconditional skip disguised as conditional. Convert to an env-var gate (`GLUDD_LOCAL_INFERENCE=1`).
> - ~74 "not yet created" `pytest.skip` stubs across 14 files (worst: `tests/unit/test_audit_roles.py` 30, `tests/unit/test_w8_roles_and_reports.py` 17) — audit each: if the feature shipped, the stale guard is masking real coverage; implement or delete the stub.
> - 4 failover `xfail(strict=False)` gap-trackers (see D17) + 2 typing-ratchet xfails — flip to `strict=True` or link to a work item so silent passes can't hide.
> - `tests/e2e/test_dogfood_todo_site.py:53` stub — implement or remove.
> **Hook-liveness CI-runnable design (ready to implement):** a zero-npm Node harness (`node --experimental-strip-types scripts/hook_plugin_harness.mjs`) can hermetically drive every `.opencode/plugin/*.ts` hook without installing dependencies. All ~33 currently-disabled checks become always-on CI assertions except ONE opt-in `hook_live` test (left manual/local). Fixtures redirect state files via the existing `GLUDD_*_STATE` env vars — no new plumbing needed. CI needs a SHA-pinned `setup-node` (node 22) step added to the test-shard job. The watchdog test must tear down via `session.deleted` (not a bare process kill) to avoid orphaned state. Hold the 3-site `skipif CI` cleanup until the `setup-node` step lands, then implement per this design.

> **E10 = PERF-1 (P1) — Tick DB session pinned across the dispatch gather (verified 2026-07-10).**
> Evidence: `src/general_ludd/event_loop/loop.py:687-726` + `:1527-1543` — the tick's session stays open across `_dispatch_execute_jobs`' gather, which can run up to ~30 minutes; under SQLite single-writer this holds the writer hostage for the whole window.
> Fix: commit/close the tick session BEFORE entering the dispatch gather; open a fresh session for post-dispatch phases. Test: integration test asserting no open session (or no held write lock) while a slow fake job runs; existing tick tests stay green.

> **E11 = PERF-2 (P1) — `task_decisions.created_at` unindexed + unconditional scan every tick (verified 2026-07-10).**
> Evidence: `src/general_ludd/event_loop/loop.py:3034` runs `ORDER BY created_at DESC LIMIT 50` on `task_decisions` every tick with no index on `created_at`; full sort per tick, growing forever (no retention).
> Fix: alembic migration adding the index (keep `create_all` parity — extend the parity tests); add a retention/archival policy (see also E12 retention). Test: migration parity suites + a repository test asserting the query plan uses the index (SQLite `EXPLAIN QUERY PLAN`).

> **E12 (P2) — Event-loop/repository perf batch (verified 2026-07-10).**
> - N+1 queries in `_collect_training_data_from_returns` (`loop.py:3804-3834`) → batch fetch.
> - `claim_runnable` missing composite index (`db/repository.py:438-451`) → add (status, queue, scheduled) composite via migration.
> - `status_summary` full-table scans (`repository.py:519-543`) → indexed counts or cached summary.
> - `_reap_stuck_todos` per-lease N+1 (`loop.py:596-661`) → single join query.
> - No retention for `task_returns`/`task_decisions` → age-based archival job (coordinate with E11).

### 3.6 Wave F — Docs & presentation (P1)

> **F1 — Reveal.js deck: architecture depth + accuracy.** *(in flight this session)*
> Deck: `docs/presentation/deck/index.html` (reveal.js 5.1.0 CDN, inline Mermaid, zero binary assets — KEEP it that way; `make deck-clean-assets` exists to enforce). Generator: `scripts/build_deck.py` (README STATUS-TABLE + git data → tokens; banned-marketing-words honesty check).
> Required content additions: (a) the flagship flow with exact code paths — submission `routers/todos.py` → `todos` table → EventLoop 16 phases (`event_loop/loop.py` PHASE_ORDER :88) → claim (`db/repository.py claim_runnable`, `bucket_leases`) → dispatch (`loop.py:1809`) → two-phase model call (`models/job_invocation.py`, `models/gateway.py`, `execution/tool_loop.py`) → `task_returns` → review (`review/reviewer.py:23`, `task_decisions`) → reconcile+commit (`loop.py:3031,3320`, `git_automation/repo.py`); (b) a behaviors→DB-tables slide from the 27-table map (`src/general_ludd/db/models.py`; e.g. todos:159, task_returns:279, task_decisions:317, bucket_leases:451, audit_events:371, model_call_logs:918, spend_records:589-ish); (c) daemon/MCP/self-improve/guardrails slides with file paths; (d) keep honest-metrics/gaps slides current.
> Constraints: pass `make deck-honesty` (no "production-ready"/"blazing"/"seamless"…); verify with `make deck` (--check) and `make deck-build`; Mermaid diagrams only, no images.

> **F2 — README presentation links.**
> `README.md:6` and `:109` point at `https://sandboxcom.github.io/gludd/` (404 until B2 completes; site now created). `README.md:106` relative link is fine. Fix: keep the Pages URL as primary once B2 verifies 200, with the in-repo relative path as the always-works fallback next to it; re-check `make check-readme-status` passes.

> **F3 — docs/presentation internal link + staleness fixes.**
> `docs/presentation/index.md` links `a11y-visual-qa-skill.md`, `github-pages-link.md`, `build-task-list.md`, `revealjs-deck.md` — actual files are `DESIGN_a11y_visual_qa_skill.md`, `GITHUB_PAGES_LINK.md`, `BUILD_TASK_LIST.md`, `DESIGN_revealjs_deck.md` (case/name mismatch = 4 broken links). `GITHUB_PAGES_LINK.md` describes a superseded build-dir design — mark it historical, point at pages.yml reality.

> **F4 — Stale design/status docs.**
> - `docs/design/PROJECT_RUNNER.md:7-8` still says slices 2-3 "in progress" — they're landed; also note D2's unwired `run_project_gate`.
> - `docs/STABILIZATION_PLAN.md:280-283` WP-D3 alembic drift — close with commit `ff8a8298` + parity-test evidence.
> - `docs/design/SLM_COMPACTION.md` §6 "unwired" — stale; compaction is daemon-wired.
> - `docs/audit/ALPHA4_VERIFIED_BACKLOG_2026-06-24.md` + `POST_SHIP_BACKLOG_PREP_2026-06-21.md` — annotate the now-fixed items (daemon.py:763 registry, alembic).
> - `tests/unit/test_alembic_create_all_parity.py:29-71` docstring describes pre-fix drift — refresh.

> **F5 — Missing standard docs (P2).**
> Configuration reference (enumerate settings/env vars from the config module), MCP tool reference (`make gen-mcp-tools` + `make mcp-docs-check` exist — publish output under docs/), CONTRIBUTING pointer to AGENTS.md ground rules, CHANGELOG sync at release cut.

---

## 4. Sequencing & Pipeline-Green Management

**Wave order (hard dependencies):**

1. **Wave A (serial-ish, small team):** A0-A7 test fixes → one gated commit batch → push → `ci-verdict-safe`. Then A8-A11 workflow edits (single agent owns `.github/workflows/` — never parallelize workflow edits) → push → verdict. A12 anytime after.
2. **Wave B:** B2 pages verification rides the Wave-A push automatically (pages.yml triggers on Makefile). B1 release-cut ONLY after a green verdict on the exact SHA.
3. **Waves C/D/E/F in parallel** after B1, with these rules:
   - Disjoint file ownership per agent; declare the file set in the dispatch brief.
   - Contention points are single-writer: `Makefile`, `TASKS.md`, `tests/conftest.py`, `.github/workflows/*`, `pyproject.toml`. Batch such edits through one integrator.
   - C1 (SSRF consolidation) before C23 (connector sweep) — the sweep should audit against the canonical module.
   - Pause/resume: D7.1 ∥ D7.2 → D7.3 → D7.4, single writer for `daemon.py` + `routers/pause.py`. C14 (lattice) before D11 (child-caps ⊆ parent). E11/E12 schema changes ride separate migrations after the E1 coverage flip to keep parity suites simple.
   - E1's `fail_under` flip lands only with the coverage that justifies it, same commit as reverting A8.
   - Max 5-6 concurrent worktree agents; `make disk` before each wave; `make clean-worktree-venvs` between waves.

**Per-wave verification ritual (every implementing agent, every time):**

```text
make test-iso TESTFILE=<your files> ID=<uniq>     # targeted proof
make gate-lite                                     # local gate approximation, writes .gate-status
make git-add FILES='<exact files>'                 # never git-add-all in parallel waves
make git-commit MSG='<single line, no metachars>'  # or ship-commit-files for subagent pushes
make git-push-sandboxcom
make ci-verdict-safe                               # ONE call; wait for verdict before next wave
```

If CI reds: `make ci-jobs-anon RUN=<id>` → `make ci-failed-tests RUN=<id>` → fix forward immediately or revert the batch (`make git-revert-files FILES=...`). A red master blocks every other agent's landing — treat it as the top-priority incident.

**TASKS.md protocol:** one integrator appends the wave's ticks (with `| evidence:`) per landing, never concurrent edits.

---

## 5. Verification Appendix — the exact commands

| Activity | Command |
|---|---|
| List targets | `make help` |
| Full local state (tree/HEAD/remote/CI) | `make verify-state` |
| Disk headroom / reclaim | `make disk` / `make clean-worktree-venvs` / `make disk-guard` |
| Targeted test, isolated | `make test-iso TESTFILE=tests/...::TestX::test_y ID=<uniq>` |
| Reproduce xdist-order issue | `make test-xdist TESTFILE='<file(s)>' ID=<uniq>` |
| Batch/background tests | `make test-batch FILES='...'` / `make test-bg FILES='...'` (log in `.gate-logs/test-bg-*.log`) |
| Hang hunting | `make test-hang-debug` |
| Local gate (no OOM) | `make gate-lite` ; status: `make gate-status` ; detached full: `make gate-async` |
| Lint / types / broader | `make lint` / `make typecheck` / `make lint-all` / `make typecheck-scope FILES='...'` |
| Coverage audit | `make audit-coverage` / `make gate-audit` |
| Security scans | `make security` (sast+sbom+pip-audit) / `make scan-secrets` |
| Search | `make grep Q='pat'` / `make grepf Q='pat' DIR='d' OUT=/tmp/x.txt` (Makefile itself is NOT searched — Read it) |
| Diffs | `make git-diff` (stat) / `make git-diff-full [FILES='...']` (patch) |
| Commit (gated) | `make git-commit MSG='...'` / subagent path: `make ship-commit-files FILES='...' MSG='...'` |
| Push / sync | `make git-push-sandboxcom` / `make ci-head-compare` |
| CI status | `make ci-status` (last 8) / `make ci-verdict-safe` (cooldown) / `make ci-greenness` (ratio) |
| CI failure detail | `make ci-jobs-anon RUN=<id>` / `make ci-failed-tests RUN=<id>` / `make ci-faillog RUN=<id>` |
| Pages | `make pages-status` / `make pages-enable` (idempotent) |
| Action SHA pinning | `make gh-tag-sha REPO=owner/name TAG=vN` |
| Deck | `make deck` (build+honesty) / `make deck-build` / `make deck-serve` / `make deck-honesty` |
| Release | `make release-cut TAG='...' MSG='...'` → `make verify-release-artifact TAG='...'` → `make release-view TAG='...'` |
| Process hygiene | `make ps-pytest` / `make kill-stale` / `make run-watched CMD='...'` |

**Priority totals:** P0: 14 (A0-A10 + A6-discipline, B1, B2, D1) · P1: 46 (A11, A12, C1-C30 family + SPD-1, D2, D3, D4, D7.1-D7.3, D9-D11, D21, E1-E4, E6-E11, F1-F4) · P2: 14 (D5, D6, D7.4, D12-D20, E5, E12, F5). Of these, the §3.0.1 landed-locally set (A1-A5, A7, A8, A9, A10, C1, C2, C3, D1, D17 (partial), D21, parts of C21/C26/F1) is annotated **[LANDED — verify]**/**[PARTIALLY LANDED]** in place and awaits push + CI confirmation before being struck outright; do not re-implement any of it.

*Maintain this spec: when an item closes, strike it here AND tick TASKS.md with evidence in the same commit. When re-verification refutes an item, mark it REFUTED here with the evidence so no one re-opens it.*
