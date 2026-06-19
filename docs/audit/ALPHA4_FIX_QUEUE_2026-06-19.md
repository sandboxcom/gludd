# Alpha.4 Fix Queue — 2026-06-19

Consolidated from session audit reports:
- `docs/audit/backlog_completeness_2026-06-16.md` (backlog verdicts, inflated claims)
- `docs/audit/batch3_dedup_coherence.md` (8 duplicate pairs, dead code, missing __init__.py)
- `docs/audit/feature_package_wiring_status.md` (36-package wiring census)
- `docs/audit/misconfig_detector_dedup_decision.md` (MisconfigDetector canonical decision)
- `docs/audit/model_routing_coherence_check.md` (7 diverging weights, 3 missing artefacts)
- `docs/design/connector_join_key_normalization.md` (8 normalization gaps G1–G8)
- `docs/integration/CI_ALPHA2_VERDICT.md` (105 CI failures, clusters 1–7)
- `docs/integration/OVERLOAD_RETRY_COVERAGE.md` (retry cap + watchdog gaps)
- `docs/research/MODEL_ROUTING_RECOMMENDATION.md` (model weight / routing spec)

Fix branches already known:
- `fix/ci-readme-gate-and-basetemp` — merged CI fixes (clusters G1/G2/G3, basetemp, httpx)
- `fix/ci-tui-packaging-and-connector-hermetic` — merged
- `fix/ci-cli-http-call-none-guards` — merged
- `feature/batch3-security` — tip `85158c2`, gate-clean, pending merge (F5b/F6a/F6b)

---

## Legend

| Column | Notes |
|---|---|
| Severity | P0 = release blocker; P1 = security/data correctness; P2 = security hardening; P3 = correctness/dedup; P4 = coverage/tests |
| Est-effort | S < 30 min; M < 2 h; L < 1 day; XL > 1 day |
| Branch | `—` = no branch yet; name = branch exists |

---

## Group A — Security P1 (Active Threats / Silent Security No-ops)

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| A-01 | SpendLimiter projected_cost always 0.0 — cap never fires | P1 | `src/general_ludd/daemon.py:729` | Replace `projected_cost_usd=0.0` with actual model call cost estimate; remove TODO at `spend_limiter.py:17-27` | M | — |
| A-02 | Scoring cost-cap silently inert — avg_cost never stored in DB | P1 | `src/general_ludd/db/repository.py` `BenchmarkRepository.get_aggregate_scores` | Add `func.avg(BenchmarkResult.cost_usd).label("avg_cost")` to SELECT; requires Alembic migration for `cost_usd` column if absent | M | — |
| A-03 | DynamicDispatcher not in event-loop — autonomous tool calls bypass dispatch | P1 | `src/general_ludd/dispatch/dynamic_dispatcher.py:8-12` `TODO(integration)`; `event_loop/loop.py:1-30` (no import) | Import `DynamicDispatcher` in `event_loop/loop.py` and invoke it when the model turn returns `tool_calls` | L | — |
| A-04 | CI gate `run_gate.sh` basetemp not cleaned on lock-rejection — disk leak + race | P1 | `scripts/run_gate.sh` lock-rejection exit path | Add `rm -rf "$BASETEMP"` in the lock-rejected exit branch | S | fix/ci-readme-gate-and-basetemp (merged) |
| A-05 | Overload retry cap too low — PROVIDER_ERROR exhausted in < 3 min | P1 | `src/general_ludd/models/timeout_detector.py` `TimeoutRetryPolicy.__init__` defaults `max_retries=3, max_backoff_seconds=60` | Add `overload_max_retries=10` kwarg; extend `max_backoff_seconds=120` for `PROVIDER_ERROR`/`RATE_LIMITED` kinds in `gateway.py:call_model_with_retry` | M | — |

---

## Group B — Security P2 (Hardening / Defense-in-Depth Gaps)

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| B-01 | W5.3-CVE ticks open — diskcache CVE-2025-69872 + pip PYSEC-2026-196 no commit hash | P2 | `TASKS.md:252-253` | Pin or patch diskcache; upgrade pip; paste commit hash in TASKS.md | S | — |
| B-02 | CI subprocess import failure — `python3 -m general_ludd.cli` fails in subprocess despite venv install | P2 | `tests/unit/test_tui_subprocess.py`, `test_cli_execution_coverage.py` | Pass full `sys.path` (or use `uv run`) when spawning subprocess in tests (fix landed for TUI; audit remaining callers) | M | fix/ci-tui-packaging-and-connector-hermetic (merged) |
| B-03 | `rich._emoji_codes` private module import breaks TUI in CI | P2 | `tests/e2e/test_tui_e2e.py::test_tui_main_screen_renders` | Pin `rich` to a tested version or replace import with public-API equivalent | S | — |
| B-04 | agent_watchdog has no overload-message detection — overload-killed treated as generic stall | P2 | `scripts/agent_watchdog.py` `classify_tail` | Add `_OVERLOAD_MARKERS` list; return `"overload-killed"` sub-state so orchestrator can apply high-cap redispatch | M | — |
| B-05 | AGENTS.md overload paragraph missing exact error string + high-cap rule | P2 | `AGENTS.md` re-dispatch table | Paste ready-to-commit paragraph from `docs/integration/OVERLOAD_RETRY_COVERAGE.md §2` | S | — |
| B-06 | `test_readme_status_gate.py` `normalize()` strips v-prefix incorrectly + infinite recursion | P2 | `tests/unit/test_readme_status_gate.py` (RecursionError, normalize assert) | Fix `normalize()` to strip leading `v`; fix `main()` recursion (calls itself instead of helper) | S | — |
| B-07 | `watchdog-check` Make target absent — orchestrator cannot auto-detect stalled overload agents | P2 | `Makefile` | Add `make watchdog-check` running `python scripts/agent_watchdog.py --list-stalled`; exit non-zero if any overload-killed agents found | S | — |
| B-08 | `test_connector_proc_sys.py` hard-codes sysfs ACPI path — CI-environment-specific failure | P2 | `tests/unit/test_connector_proc_sys.py::test_confined_explicit_path_allowed` | Mock sysfs path or use dynamic path discovery instead of hard-coded ACPI tree path | S | — |

---

## Group C — Data / Migrations

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| C-01 | `BenchmarkResult` missing `task_role` field — routing_roles spec P1 item | P3 | `src/general_ludd/schemas/benchmark.py` | Add `task_role: TaskRole \| None = None` field; generate Alembic migration | M | — |
| C-02 | `avg_cost` column absent from BenchmarkResult DB schema — silent cost-cap zero | P1 | `src/general_ludd/db/repository.py` + Alembic migrations | Add `cost_usd` column to `benchmark_results` table via Alembic migration; update `get_aggregate_scores` SELECT | M | — |
| C-03 | `routing_roles/` package exists in worktree only — not merged to main | P3 | worktree `agent-aa7abecb24030ba7d/src/general_ludd/routing_roles/` | Cherry-pick `roles.py` and `weights.py` from worktree into main before wiring into AdaptiveRouter | S | — |
| C-04 | `model_weights/` package entirely absent — cold-start seed data missing | P3 | `src/general_ludd/model_weights/` (CREATE) | Create `schema.py`, `store.py`, `loader.py`, `seed_data.json` with model assignments from recommendation doc §3.2 | L | — |
| C-05 | `scoring/metric.py` absent — W$ formula inline in router, no standalone module | P3 | `src/general_ludd/scoring/router.py` (inline math) | Create `scoring/metric.py` implementing W$ formula; update AdaptiveRouter to import from it | M | — |
| C-06 | `pipeline/__init__.py` missing — namespace package, mypy/pyright may reject | P3 | `src/general_ludd/pipeline/` | Add empty `__init__.py` | S | — |
| C-07 | `issue_sources/__init__.py` missing — modules unreachable via package import | P3 | `src/general_ludd/issue_sources/` | Add `__init__.py` | S | — |

---

## Group D — Correctness / Dedup

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| D-01 | `orchestration/pipeline_controller.py` duplicate of `pipeline/controller.py` — test imports wrong module | P3 | `src/general_ludd/orchestration/pipeline_controller.py`; `tests/unit/test_pipeline_controller.py:1` | Re-point test to `pipeline.controller` / `pipeline.lanes`, then delete orphan `orchestration/pipeline_controller.py` | M | — |
| D-02 | `issue_sources/markdown_source.py` dead code — zero imports, zero tests | P3 | `src/general_ludd/issue_sources/markdown_source.py` | Delete file (zero-risk; no callers anywhere) | S | — |
| D-03 | `connectors/windows_event.py` duplicate of `windows_event_log.py` (fewer tests, weaker API) | P3 | `src/general_ludd/connectors/windows_event.py`; `tests/unit/test_connector_windows_event.py` | Delete orphan + its test file after confirming no registry refs | S | — |
| D-04 | `connectors/docker_api.py` duplicate of `docker_engine.py` (no SSRF guard) | P3 | `src/general_ludd/connectors/docker_api.py`; `tests/unit/test_connector_docker_api.py` | Delete orphan + its test file after confirming no registry refs | S | — |
| D-05 | `infra/misconfig_detector.py` duplicate of `model_deploy_check.py` — must port rule-d first | P3 | `src/general_ludd/infra/misconfig_detector.py` | Port CPU-offload/swap rule d into `model_deploy_check.py`, delete `misconfig_detector.py` + `test_misconfig_detector.py` per decision doc §4 | M | — |
| D-06 | `issue_sources/excel_csv.py` duplicate of `csv_excel.py` — merge `_confine()` security guard first | P3 | `src/general_ludd/issue_sources/excel_csv.py` | Port `_confine(root, path)` into `CsvExcelSource`, then delete `excel_csv.py` + its test | M | — |
| D-07 | `GitHubIssueSource` (singular, backward-compat stub) still in `github_issues.py` — no production caller | P3 | `src/general_ludd/issue_sources/github_issues.py:135` | Migrate `test_issue_source_github.py` to `GitHubIssuesSource`; remove singular class | S | — |
| D-08 | `connectors/tempo_zipkin.py` overlaps `tempo.py` + `zipkin.py` — three-way duplication | P3 | `src/general_ludd/connectors/tempo_zipkin.py` | Decide canonical (recommended: keep standalones; delete `tempo_zipkin.py` or make it a thin re-export) | M | — |
| D-09 | `routers/coordination.py` `FileClaimRegistry` unwired — `TODO(integration)` at line 14; not in daemon | P3 | `src/general_ludd/routers/coordination.py:14`; `daemon.py` router block | Register `coordination` router in daemon `register_all` or explicitly fence with DEFERRED comment | M | — |
| D-10 | `AdaptiveRouter.route()` ignores `routing_roles/weights.py` per-task table — uses constructor defaults | P3 | `src/general_ludd/scoring/router.py` `route()` | Inject `task_weights[task_type]` from `routing_roles.weights` inside `route()` instead of `self.cost_weight/quality_weight` | M | — |
| D-11 | 7 per-TaskType weight values diverge between `weights.py` and recommendation doc §3.4 | P3 | `src/general_ludd/routing_roles/weights.py` (DEBUGGING, OPTIMIZATION, FEATURE, TEST_WRITE, CODE_REVIEW, REFACTOR, DOCUMENTATION rows) | Reconcile to doc values OR update doc with rationale; starkest gap: CODE_REVIEW doc=0.15/0.85, code=0.40/0.60 | S | — |
| D-12 | `test_cli_e2e.py` mock patch paths stale after CLI refactor — ~31 tests fail | P3 | `tests/e2e/test_cli_e2e.py` (multiple test functions) | Audit patch paths after CLI refactor; update to match new module structure | M | fix/ci-cli-http-call-none-guards (merged) |

---

## Group E — Coverage / Tests

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| E-01 | `connectors` + `observe` router unregistered — 38 connector modules + tests are dead library code | P3 | `src/general_ludd/routers/observe.py` not in `register_all`; `daemon.py:1116-1210` | Add `observe` to `register_all` in daemon — the ONLY missing step to unlock live connector query | S | — |
| E-02 | `receiver` package orphaned — buffer/parsers/router written + tested, not in daemon | P3 | `src/general_ludd/receiver/router.py:43` self-documents as deliberately unwired | One import + `app.include_router(receiver_router)` in daemon lifespan | S | — |
| E-03 | `issue_sources` package orphaned — base + ingest logic tested, not in daemon | P3 | `src/general_ludd/issue_sources/` — no importer in daemon | Wire into receiver pipeline after E-02 | M | — |
| E-04 | `self_update` package orphaned — applier + router written + tested, not in daemon | P3 | `src/general_ludd/self_update/router.py` | Add `POST /self_update/apply` route inclusion in daemon + lifecycle hook | S | — |
| E-05 | Scheduler has no dedicated integration test for parallel dispatch | P4 | `src/general_ludd/scheduling/scheduler.py` + `event_loop/loop.py:709,740` | Write `test_scheduler_integration.py` proving parallel task fan-out via Scheduler | M | — |
| E-06 | 23 of 29 completion_audit classes import-wired only — no behavioral tests | P4 | `tests/unit/test_completion_audit_wiring.py` | Add behavioral tests for LangGraphGateway, PromptScoringEngine, and the 20 remaining import-only classes | XL | — |
| E-07 | `BUG-2/BUG-8` frontmatter YAML injection fix unverified — carry-forward from BURNDOWN | P3 | `issue_sources/github_issues.py` yaml.safe_dump path | Verify `yaml.safe_dump` is used; add regression test if not confirmed | S | — |
| E-08 | `BUG-3` issue-ingestor dedup no-op — fresh ingestor per request resets `_seen_ids` | P3 | `issue_sources/` ingestor class | Verify dedup state persistence across requests; add regression test | S | — |
| E-09 | `BUG-4` GitHub URL/param injection not verified — carry-forward | P3 | `issue_sources/github_issues.py` owner/repo validation | Confirm `urllib.parse.quote` in use; add adversarial test | S | — |
| E-10 | `BUG-5` from_url IndexError not verified — carry-forward | P3 | `issue_sources/` `from_url()` | Confirm length guard + typed ValueError → 422; add test | S | — |
| E-11 | `BUG-6/BUG-7` RunHistory aliasing not verified — carry-forward | P3 | `observability/run_history.py` | Confirm deep-copy on store + structured-key match; add regression test | S | — |
| E-12 | W16.1 CI-green unverified — gate PASS is local-only, no sandboxcom run id | P3 | `TASKS.md:450` | Run CI, paste run id in TASKS.md; CI greenness currently 20% (4/20 runs green) | L | fix/ci-readme-gate-and-basetemp (merged; CI re-run needed) |
| E-13 | W5.3-CVE diskcache + pip CVE ticks no commit hash | P2 | `TASKS.md:252-253` | Pin/patch CVEs, paste hash | S | — |

---

## Group F — Provider E2E / Model Routing

| # | Title | Severity | File : line | One-line fix | Effort | Branch |
|---|---|---|---|---|---|---|
| F-01 | Connector join-key G1: `span_id` aliases missing from `normalize_join_keys` | P3 | `src/general_ludd/connectors/normalize.py` `_TRACE_ALIASES` | Add `_SPAN_ALIASES = ("span_id", "spanid", "span.id", "x-b3-spanid")` + wire into `_derive_join` | S | — |
| F-02 | Connector join-key G2: `request_id`/`CorrelationId` has no canonical alias | P3 | `src/general_ludd/connectors/normalize.py` | Add `_REQUEST_ALIASES` including `correlationid`, `x-request-id`; wire into `_derive_join` | S | — |
| F-03 | Connector join-key G3+G4: dotted ECS keys `trace.id` + `service.name` not in alias tables | P3 | `src/general_ludd/connectors/normalize.py` `_TRACE_ALIASES`, `_SERVICE_ALIASES` | Add `"trace.id"` to `_TRACE_ALIASES`; add `"service.name"` to `_SERVICE_ALIASES` | S | — |
| F-04 | Connector join-key G5: Datadog `tags` list not decomposed — Datadog records produce no join keys | P3 | `src/general_ludd/connectors/normalize.py` `_coerce_label_map` | Split `"key:value"` strings in `tags` list into label map (last-writer-wins) | S | — |
| F-05 | Connector join-key G6+G8: `machine` (WinEventLog) not in `_HOST_ALIASES`; ES `span.id` not surfaced to labels | P3 | `normalize.py` `_HOST_ALIASES`; `src/general_ludd/connectors/elasticsearch.py` | Add `"machine"` to `_HOST_ALIASES`; add `span.id` to labels in elasticsearch.py when extracted from `_source` | S | — |
| F-06 | No unit tests for normalize join-key aliases (`test_normalize_join_keys.py` absent) | P4 | `tests/unit/` | Create `test_normalize_join_keys.py` per test plan in `connector_join_key_normalization.md §8` (15 test cases) | M | — |
| F-07 | `model_weights/schema.py` absent — `TaskRole` in `routing_roles/roles.py` has no counterpart schema | P3 | `src/general_ludd/model_weights/schema.py` (CREATE) | Import `TaskRole` from `routing_roles.roles` in new `schema.py`; do NOT define a second enum | S | — |
| F-08 | `METRIC_AND_BIBLIOGRAPHY.md` absent — W$ formula undocumented | P4 | `docs/research/METRIC_AND_BIBLIOGRAPHY.md` (CREATE) | Document W$ formula, composite score weights, citations | S | — |
| F-09 | W10–W15 Molecule 49/49 GREEN local-only — CI molecule job added but CI-green unverified | P3 | `.github/workflows/build.yml` molecule job | Run CI with molecule job and paste run id | L | — |

---

## Summary Counts

| Group | Items | Open (no branch) | Already has fix branch |
|---|---|---|---|
| A — Security P1 | 5 | 4 | 1 (A-04 merged) |
| B — Security P2 | 8 | 6 | 2 (B-02 merged) |
| C — Data/Migrations | 7 | 7 | 0 |
| D — Correctness/Dedup | 12 | 11 | 1 (D-12 merged) |
| E — Coverage/Tests | 13 | 12 | 1 (E-12 partial) |
| F — Provider E2E | 9 | 9 | 0 |
| **TOTAL** | **54** | **49** | **5 (4 merged, 1 partial)** |

---

## Recommended First-Wave Sequence (alpha.4 → alpha.5)

1. **A-01 + A-02 (spend-cap + cost-cap)** — P1 security controls that currently never fire.
   Wire in a single commit: daemon cost projection + `avg_cost` migration.
2. **E-01 (observe router registration)** — 1-line change; unlocks 38 connector modules.
3. **C-06 + C-07 (__init__.py files)** — 2-minute fixes; unblock mypy on pipeline + issue_sources.
4. **D-01 + D-02 + D-03 + D-04 (dedup wave 1)** — zero-risk dead-code deletions; reduce noise.
5. **F-01 + F-02 + F-03 + F-04 + F-05 (normalize aliases)** — all additive 1-5 line changes.
6. **A-05 (overload retry cap)** — raise PROVIDER_ERROR cap to 10 before next CI run.
7. **E-12 (CI green)** — run CI, paste run id; gate milestone for alpha.4 cut.
