# General Ludd Agent

The black swan agentic coding system — an autonomous, Ansible-driven, multi-model AI agent
that submits coding tasks and produces real, committed, reviewed, and reconciled code changes.

## What Is This?

General Ludd (`gludd`) is an **autonomous agentic SDLC daemon** (FastAPI). You submit a
todo — "add end-to-end encryption to the API," "fix the race condition in the job queue,"
"upgrade all dependencies and run the test suite" — and the system dispatches it to an AI
model, runs the generated code through a validation pipeline (tests, lint, typecheck, quality
gates), reviews the result with a separate model, and lands the change in git.

It is not a chatbot or a copilot. It is a daemon with an event loop:
**claim → dispatch → review → reconcile → repeat**.

The execution layer is **Ansible**: every task the daemon runs is an Ansible playbook that
composes modules from the `general_ludd.agent` collection. This means tasks are auditable,
idempotent, and can fan out to subagents via the same API.

## Who Is This For?

- **Platform and infrastructure teams** who want autonomous agents managing configuration
  drift, dependency updates, and security patches across dozens of repositories.
- **AI/ML researchers and operators** experimenting with multi-model agent architectures,
  adaptive model routing, and benchmark-driven model selection.
- **SREs and DevOps engineers** who already use Ansible and want an agent that can execute
  playbooks, validate results, and open pull requests with evidence trails.
- **Anyone deploying LLM-based coding agents** who needs budget guards, cost tracking,
  per-model benchmarking, and a quality gate that actually blocks bad code.

## Current Stability

This project is **alpha-quality research software**. The daemon boots, the event loop
ticks, the database layer works, and the model gateway can call real APIs. But many
subsystems are wired but not fully exercised end-to-end. **Do not run this in production
without understanding the failure modes.** Expect rough edges around Ansible playbook
execution, multi-model failover, and project workspace management.

**CI note:** GitHub Actions runs on every push to master. The gate (`lint`, `typecheck`,
`collect`, `test`, `smoke`) runs against Python 3.11 and 3.12 with `fail-fast: false` so
both matrix legs report. The molecule scenario suite runs as a separate job after gate.
CI is being stabilized — consult the Actions tab for the real current status rather than
relying on any static claim here.

### Measured status (single source of truth)

This README intentionally does **not** hardcode test counts, mypy error totals, or
coverage percentages — stale numbers in docs were a recurring source of false "done"
claims. The live, authoritative status is the gate:

```bash
make gate            # lint + typecheck + collect + test + smoke; writes .gate-status
cat .gate-status     # the single source of truth for current counts
make test-count      # collected-test count, 0 collection errors required
make typecheck       # current mypy error count (gate enforces ≤ MYPY_MAX, see Makefile)
```

Known-failing tests are tracked as strict xfail entries in `config/ratchet.yml` (the file
may only shrink). The gate passes only when `make test` exits 0.

Version: `v0.1.0-alpha` — prereleases are built automatically on every push to master and
published as GitHub Releases with timestamped artifacts for Linux (x86_64), macOS (arm64),
and Windows (x86_64).

---

## Feature & Task Completion Status

**Status as of v0.1.0-alpha.2 — 2026-06-18**

This table is regenerated/verified on every release cut (enforced by `make release-cut`).

Honesty note: this project has a documented history of false "done" claims (see `BUGS.md`).
Every percentage below is backed by a commit SHA, named test, or audit finding. Unbuilt work
shows a low number. "Local-only" means the gate passes on macOS arm64 but CI (ubuntu) is
unverified.

Evidence key: `[commit]` = 7-char SHA in `TASKS.md`, `[test]` = named test file or class,
`[audit]` = `docs/audit/backlog_completeness_2026-06-16.md` findings.

---

### Core Engine / Daemon Spine

| Feature / Task | % | Evidence |
|---|---|---|
| G0 Daemon starts configured (env-var passthrough, default config search) | 100% | `tests/unit/test_daemon_launch_config.py` — PASS; `[b4de809]` |
| G1 Event loop opens DB session per tick + commits | 100% | `tests/unit/test_event_loop_session_per_tick.py` — PASS; `[a7a97c6]` |
| G2 `POST /api/todos` persists to DB | 100% | `tests/e2e/test_todos_persistence.py` — PASS; `[60cdb4d]` |
| G3 Playbook resolution real + extravars reach playbook | 100% | `tests/unit/test_runner_resolution.py` — PASS; `[506ed44]` |
| G4 Dispatched job calls model + applies edits | 100% | `tests/unit/test_execution_engine.py` — PASS; `[b4de809]` |
| G5 ReturnReviewer wired; failure escalates (never silent pass) | 100% | `tests/integration/test_w3_2_reviewer_wiring.py` 3 passed; `[a7a97c6]` |
| G6 Work lands in git (branch + commit + SHA) | 100% | `tests/unit/test_execution_git_delivery.py` — PASS; `[56fbec7]` |
| G7 Full pipeline e2e (submit → model → review → commit) | 100% | `tests/integration/test_full_pipeline_e2e.py` — PASS; `[6915362]` |
| Worker invokes ModelGateway for generation jobs (W3.1/C1) | 100% | `tests/e2e/test_obj03_worker.py::TestWorkerModelGatewayCall` 3 passed; `[b4de809]` |
| asyncio.to_thread for playbook runs (W3.3/M9) | 100% | `tests/unit/test_w3_3_asyncio_thread.py` — PASS; `[779937c]` |
| /readyz degraded-state endpoint (W3.4/N1) | 100% | `tests/unit/test_w3_4_readyz.py` — PASS; `[779937c]` |
| SQLite-only enforced + single-worker clamp (W3.5/M8/H18) | 100% | `tests/unit/test_single_worker_sqlite.py` 7 passed; `[312e403]` |
| Self-improvement todos persist via TodoRepository (W3.7/H2) | 100% | `tests/integration/test_w3_7_self_improve_persist.py` 2 passed; `[a7a97c6]` |
| Worker stub endpoints return HTTP 501 (W3.8/H3) | 100% | `tests/unit/test_w3_8_worker_501.py` — PASS; `[779937c]` |
| One `select_project()` per tick (W3.14/M14) | 100% | `tests/integration/test_w3_14_single_project_per_tick.py` 2 passed; `[a7a97c6]` |
| /api/deployments endpoint + deploy-before-destroy registry (C5/M2) | 100% | `tests/unit/test_deployment_registry.py` 7 passed; `[eb84b0c]` |
| Lease acquisition + expiry reclaim (H15) | 100% | `tests/e2e/test_obj04_event_loop.py::test_reclaims_expired_lease` — PASS; `[a7a97c6]` |
| Project workspaces cloned from repo_url + persisted (H13) | 100% | `tests/unit/test_project_workspace_clone.py` 6 passed; `[a4c04a9]` |
| Hot-reload honesty — reports only real reloads (H14) | 100% | `tests/unit/test_w3_12_reload.py` — PASS; `[779937c]` |
| Scheduler drives parallel dispatch (#23/#32) | 75% | `event_loop/loop.py:709,740` wired; no dedicated e2e parallel-dispatch proof; `scheduling/scheduler.py:15-19` has stale TODO comment; `[audit]` |
| DynamicDispatcher for autonomous tool-call dispatch (#26) | 25% | HTTP path wired (`routers/dispatch.py`); event-loop path NOT wired (`dynamic_dispatcher.py:8-12` TODO(integration) live); `[audit]` |
| PipelineController async lanes (pipeline/) | 75% | `pipeline/controller.py` 223 lines; daemon lazy-imports it (config-gated); `tests/unit/test_pipeline_lanes.py` exists; no e2e proof of `pipeline.enabled=True` path; `[audit]` |
| SpendLimiter rolling budget cap (#27/#49) | 20% | `daemon.py:729` passes `projected_cost_usd=0.0` — cap literally never fires; TODO in `spend_limiter.py:17-27`; wired-but-inert security control; `[audit]` |

---

### Models / Gateway

| Feature / Task | % | Evidence |
|---|---|---|
| Model gateway real API calls + cost accounting | 100% | `tests/unit/test_model_gateway_fallback.py` — PASS; `[3ef7eb6]` |
| Tenacity retry — hand-rolled loop deleted (W4.1/V3.1) | 100% | `tests/unit/test_w4_1_tenacity_retry.py` 5 passed incl. `test_call_with_tenacity_demo_deleted`; `[15db868]` |
| Model failover chain (F6) | 100% | `tests/unit/test_model_gateway_fallback.py` + `test_r2_5a_profiles_failover.py` — PASS |
| Router gateway gets metrics_collector (H12) | 100% | `tests/unit/test_w3_10_metrics_gateway.py` — PASS; `[779937c]` |
| Budget guard wired (H11) | 100% | `tests/unit/test_budget_wiring.py` — PASS; budget: config section consumed |
| Per-todo/daily budget caps (F5) | 100% | `tests/unit/test_budget_caps.py` — PASS |
| Secrets auto mode tries OpenBao before env (H17) | 100% | `tests/unit/test_secrets_auto_mode.py` 4 passed; `[1bbe4b8]` |
| CLI ↔ /admin/code/* endpoint parity (M11/W3.13) | 100% | `tests/unit/test_w3_13_cli_code_parity.py` — PASS; `[779937c]` |
| Scoring cost-constrained routing (#59/#69) | 20% | `scoring/router.py:38-67` route logic exists; `BenchmarkRepository.get_aggregate_scores` returns NO `avg_cost` column → `scoring/router.py:131` defaults 0.0 → cap is a production no-op; unit tests pass with mocked data only; `[audit]` |
| Model routing roles + weights (`routing_roles/`) | 25% | `routing_roles/roles.py` + `routing_roles/weights.py` in worktree only (not merged); `model_weights/` package absent entirely; 7/10 weight pairs diverge from recommendation doc; `[docs/audit/model_routing_coherence_check.md]` |
| BenchmarkResult `task_role` field (P1) | 0% | `schemas/benchmark.py` has no `task_role` field; recommendation doc §3.2 marks this P1; `[docs/audit/model_routing_coherence_check.md]` |
| `model_weights/` package (seed_data.json, schema, store, loader) | 0% | Absent from both main tree and worktree; recommendation doc §4.2 labels P0; `[docs/audit/model_routing_coherence_check.md]` |

---

### Connectors / Observability

| Feature / Task | % | Evidence |
|---|---|---|
| Observability connector base + normalize + registry (38 connectors) | 60% | 38 connector modules exist with 38 test files; `routers/observe.py` exists; BUT `daemon.py` never imports `general_ludd.connectors`; `observe` router not registered in `register_all`; `routers/observe.py:28-43` self-documents as deliberately unwired; `[audit rank #1 inflation]` |
| gludd_metrics + gludd_traces Ansible modules | 100% | `tests/integration/test_playbook_registry.py::TestMetricsAndTracesModules` 145 passed; molecule scenarios 27+28; `[86389be]` |
| /api/metrics + /api/traces endpoints | 100% | `tests/integration/test_facts_live_seam.py` 4 passed; `RecentTracesBuffer` live; `[86389be]` |
| Observability router (`routers/observe.py`) wired into daemon | 5% | Router file exists with `register()` but is never called by daemon startup; no `wire_observability()` function exists; 1 line of daemon.py change needed; `[audit]` |
| Receiver (buffer + parsers + OTLP/webhook/gelf) | 30% | `receiver/router.py` 393 lines; `receiver/buffer.py` + `receiver/parsers.py` with tests; `router.py:43` explicitly says "do NOT edit daemon.py here"; not wired; `[audit]` |
| Issue sources (~17 connectors: GitHub, Linear, CSV, Markdown, etc.) | 20% | `issue_sources/base.py` + several adapters with tests exist; NOT wired into daemon or receiver; package structure incomplete; several duplicate-pair conflicts unresolved; `[audit]` |
| Connector dedup cleanup (7 duplicate pairs) | 0% | `orchestration/pipeline_controller.py` vs `pipeline/controller.py`; `windows_event.py` vs `windows_event_log.py`; `docker_api.py` vs `docker_engine.py`; `tempo.py`+`zipkin.py` vs `tempo_zipkin.py`; etc. — all unresolved; `[docs/audit/batch3_dedup_coherence.md]` |
| MisconfigDetector dedup (`misconfig_detector.py` vs `model_deploy_check.py`) | 0% | Two classes both named `MisconfigDetector`; neither canonical yet chosen; `[docs/audit/misconfig_detector_dedup_decision.md]` |

---

### Security Hardening

| Feature / Task | % | Evidence |
|---|---|---|
| MCP per-request timeouts + kill fallback | 100% | `[9d487ab]` commit; MCP hardening shipped |
| webmcp dogfood + GLUDD_REQUIRE_AUTH (#20) | 100% | `[9d487ab]`; `tests/` confirm |
| FS write policy default-DENY + adversarial tests (#43) | 100% | `tests/security/test_fs_write_audit.py`; `[audit #43 DONE-VERIFIED]` |
| capability_policy default-DENY per-role (#44) | 100% | `security/capability_lattice.py:211-235`; `[4314a6c]` |
| Conflict scanner + pre-commit hook (#33) | 100% | `scripts/scan_conflicts.py`; `make scan-conflicts`; `[9d487ab]` |
| CI regression guards (#30) | 100% | `tests/security/test_ci_regression_guards.py`; `[9d487ab]` |
| Clone RCE/SSRF hardening (#56) | 100% | `tests/unit/test_git_repo_clone_hardening.py`; `security/auth.py:159`; `[audit #56 DONE-VERIFIED]` |
| DB races red-team (#52) | 100% | `tests/security/test_db_redteam.py`; `[audit #52 DONE-VERIFIED]` |
| Secrets lifecycle red-team (#53) | 100% | `tests/security/test_secrets_redteam.py`; `[audit #53 DONE-VERIFIED]` |
| Gateway concurrency red-team (#54) | 100% | `tests/security/test_gateway_concurrency_redteam.py`; `[audit #54 DONE-VERIFIED]` |
| Self-modification guards (#58) | 100% | `tests/security/test_self_modify_guards.py`; `[audit #58 DONE-VERIFIED]` |
| Ansible SSTI red-team (#50) | 100% | `tests/security/test_ansible_ssti_redteam.py` (own suite); `[audit #50 DONE-VERIFIED]` |
| Skill renderer SSTI fix (SandboxedEnvironment) | 100% | `skills/renderer.py:68` uses `SandboxedEnvironment`; `test_skills_renderer_adversarial.py`; `[audit BUG-1 DONE-VERIFIED]` |
| base_url SSRF guard (#61) | 100% | `tests/unit/test_gateway_base_url_ssrf.py`; `models/gateway.py:259-278`; `[audit #61 DONE-VERIFIED]` |
| SSH key gitignored + enforcement layers (W5.1) | 100% | `test_guardrails.py::TestNoTrackedPrivateKeys` 2 passed; `make git-tracked-keys "NONE TRACKED"`; `[526104b]` |
| dist packs LICENSE + THIRD_PARTY_LICENSES + SBOM (W5.2) | 100% | `tests/security/test_dist_license_pack.py` 6 passed; `[526104b]` |
| Fresh secrets scan adjudicated; dist paths clean (W5.3) | 100% | `test_dist_license_pack.py::test_dist_scrubs_build_paths` passed; `[526104b]` |
| Worker /jobs/* require PSK auth (W5.6) | 100% | `tests/unit/test_w5_6_worker_auth.py` 9 passed; `[526104b]` |
| Metric-label cardinality guard (#60) | 100% | `tests/unit/test_metrics_cardinality.py`; `observability/metrics_exporter.py:34-79`; `[audit #60 DONE-VERIFIED]` |
| F5b/F6a/F6b security features (fast-follow branch) | 50% | `feature/batch3-security` branch tip `85158c2`; 14/14 tests passing locally; ancestor-clean; gate-clean on branch — **NOT YET MERGED to master**; `SESSION.md` |
| D-04/D-05/D-06/D-29/D-30/D-31 security items (batch-4 branch) | 10% | `batch-4-security` branch "building" per SESSION.md; no merge; D-backlog catalogued in `docs/audit/` but items not yet scheduled; `SESSION.md:56` |
| D-07 through D-47 security backlog | 5% | Catalogued in `docs/audit/NEW_FINDINGS_2026-06-16.md`; not scheduled; `SESSION.md:56` |
| CVE diskcache CVE-2025-69872 + pip PYSEC-2026-196 (W5.3-CVE) | 0% | `TASKS.md:252-253` ticks open with no commit hash; `pip-audit` ends `|| true` (gates nothing); `[audit]` |

---

### Orchestration / Agents

| Feature / Task | % | Evidence |
|---|---|---|
| `general_ludd.agent` Ansible collection skeleton + gludd_ping (W6.1) | 100% | `tests/integration/test_playbook_registry.py::TestCollectionStructure` 12 passed; `[ea2e915]` |
| gludd_model_call + POST /admin/models/call (W6.2) | 100% | `tests/integration/test_playbook_registry.py::TestModuleSecurityProperties` 32 passed; `[ea2e915]` |
| gludd_worktree + gludd_git modules (W6.3) | 100% | `tests/integration/test_playbook_registry.py::TestCollectionStructure::test_module_has_documentation_block`; `[2aae2ef]` |
| gludd_db module (W6.4) | 100% | `tests/integration/test_playbook_registry.py::TestModuleSecurityProperties::test_gludd_db_no_log`; `[2aae2ef]` |
| Skill rendering with Jinja2 StrictUndefined (W6.5) | 100% | `tests/integration/test_playbook_registry.py::TestSkillRenderer` 5 passed; `[2aae2ef]` |
| gludd_mcp_tool (honestly fenced W3.9, W6.6) | 100% | Module exists; `not_implemented=True`; decision recorded `TASKS.md:324-342`; `[2aae2ef]` |
| agent_task role + playbook migration (W6.7/W6.9) | 100% | `make ansible-collection-test "118 passed"`; `[d0203ba]` |
| gludd_agent_run (ToolCallLoop kept, W6.8) | 100% | `tests/integration/test_playbook_registry.py::TestModuleSecurityProperties::test_psk_no_log_in_gludd_agent_run`; `[c337fdb]` |
| AgentMessageRepository + /api/messages (W7.1) | 100% | `tests/unit/test_agent_message_repo.py` 8 passed; `tests/integration/test_messages_and_facts_api.py::TestMessagesApi` 4 passed; `[bd80f5a]` |
| /api/facts aggregation + gludd_facts + gludd_message modules (W7.2/W7.3) | 100% | `tests/integration/test_messages_and_facts_api.py::TestFactsApi` 2 passed; `TestFactsAndMessageModules` 11 passed; `[bd80f5a]` |
| Prompt message-queue section for agent roles (W7.4) | 100% | `tests/unit/test_prompt_message_queue_section.py` 9 passed; `[bd80f5a]` |
| 7 AI-coding-agent roles (implement_change, write_tests, etc.) (W8.1) | 100% | `tests/integration/test_w8_roles_and_reports.py` 107 passed; `[2eec9e1]` |
| 5 audit/report roles (W8.2) | 100% | `tests/integration/test_w8_roles_and_reports.py` 107 passed; `[2eec9e1]` |
| Agent coordination playbooks (W8.3) | 100% | `make ansible-syntax "31 playbooks all passed"`; `tests/integration/test_w8_roles_and_reports.py` `TestNewPlaybooksStructure`; `[2eec9e1]` |
| completion_audit 83% → 100% (W9.1) | 100% | `tests/unit/test_completion_audit_wiring.py` 26 passed; `make preflight completion_audit PASS 100.0%`; `[6915362]` |
| Molecule mock-daemon harness + 14 module scenarios (W10.1–W10.5) | 100% (local) | `make molecule-test-all "ALL scenarios passed" 14/14`; CI-green unverified; `[761f79c]` |
| All 12 role molecule scenarios (W10.6) | 100% (local) | `make molecule-test-all "ALL scenarios passed" 26/26`; CI-green unverified; `[41889e6]` |
| 5 workflow-pipeline roles + molecule scenarios (W13.1) | 100% (local) | `make molecule-test-all 33/33`; CI-green unverified; `[2a8f97b]` |
| 7 secure-SDLC roles + molecule scenarios (W14.1) | 100% (local) | `make molecule-test-all 40/40`; CI-green unverified; `[9629e20]` |
| 9 agile/sprint roles + molecule scenarios (W15.1) | 100% (local) | `make molecule-test-all 49/49`; CI-green unverified; `[8b252e1]` |
| File-overlap coordination router (#31) | 10% | `routers/coordination.py` exists with `FileClaimRegistry`; `coordination.py:14` has `# TODO(integration)`; NOT registered in daemon.py; no file-path-to-work-item mapping; `[audit]` |
| Per-project cost/time/LoC accounting (#28) | 20% | `MetricsCollector.get_cost_by_project` + `TodoRepository.status_summary` work; no time, LoC, or per-role stats; 3 of 5 claimed dimensions missing; `[audit]` |
| Watchdog/stall detection improvements (mt-6-watchdog branch) | 15% | Branch "building" per SESSION.md; no merge; `[SESSION.md:28]` |
| Gate-safe + predictive floor controller (floor_controller-consolidated branch) | 15% | Branch "building" per SESSION.md; no merge; `[SESSION.md:29]` |
| self_update wired into daemon | 5% | `self_update/__init__.py` commits 4 symbols; NOT in daemon imports or router block; `[audit]` |
| Persistent agent memory (G1) | 0% | No `memory/` package; design-only; `[audit]` |
| Offline eval harness (G2) | 0% | No `eval/` package; design-only; `[audit]` |
| Semantic codebase retrieval (G3) | 0% | No `retrieval/` package; design-only; `[audit]` |
| Sandboxed code execution (G4) | 0% | No `sandbox/` package; design-only; `[audit]` |
| HITL approval gates (G7) | 0% | No `approvals/` package; design-only; `[audit]` |
| Multi-agent debate / consensus (G11) | 0% | No `review/consensus.py`; design-only; `[audit]` |
| Plan/critique layer (G9) | 0% | No `planning/` package; design-only; `[audit]` |
| Prompt/skill versioning A/B (G6) | 0% | `PromptRegistry` has no version/hash/history; design-only; `[audit]` |
| Outcome-driven self-improve (G5) | 0% | No `OutcomeAnalyzer`; blocked on G2 (eval harness); `[audit]` |
| Cost/quality Pareto router (G8) | 0% | No implementation; blocked on avg_cost DB fix (#59/#69); `[audit]` |
| Per-run replay (G10) | 0% | No `replay/` package; design-only; `[audit]` |
| Live web retrieval MCP tool (G12) | 0% | No implementation; design-only; `[audit]` |
| Structured task-spec / acceptance_criteria (G13) | 0% | Todos are free-text; no schema field; design-only; `[audit]` |

---

### DB / Migrations

| Feature / Task | % | Evidence |
|---|---|---|
| Alembic 23 → 0 errors (W35) | 100% | `make lint 0`; `[9d487ab]` commit message; ruff clean |
| mypy 18 → 0 errors (W5.4) | 100% | `.gate-status typecheck PASS 0`; `MYPY_MAX=0` in Makefile; `[526104b]` |
| Alembic stamp_head + SQLite-only enforced | 100% | `tests/unit/test_single_worker_sqlite.py` 7 passed; `[312e403]` |
| Message queue DB schema (AgentMessageModel) | 100% | `tests/unit/test_agent_message_repo.py` 8 passed; `[bd80f5a]` |
| Observability trace store (RecentTracesBuffer) | 100% | `tests/unit/test_trace_store.py` 7 passed; `[86389be]` |
| CVE diskcache + pip dependency upgrades | 0% | `TASKS.md:252-253` open; `make pip-audit` ends `|| true`; `[audit]` |
| avg_cost column in BenchmarkRepository.get_aggregate_scores | 0% | `db/repository.py::get_aggregate_scores` returns no avg_cost; scoring/router.py:131 defaults 0.0; identified as a 2-line fix; `[audit]` |

---

### Release / CI

| Feature / Task | % | Evidence |
|---|---|---|
| CI gate job Python 3.11/3.12 matrix (V1.7/W16.1) | 75% | `.github/workflows/build.yml` matrix exists; local gate ALL PASSED; `TASKS.md:450` explicitly admits "CI-green UNVERIFIED-in-CI at commit time"; prior CI runs had 10x "Event loop is closed"; `[11d3060]` |
| CI version PEP 440 fix (W11.1) | 100% | `tests/security/test_ci_workflow.py::TestVersionPEP440` 7 passed; `dist/gludd` built; `[11d3060]` |
| Molecule CI job | 75% (local) | `.github/workflows/build.yml` molecule job added; locally 49/49; CI-green unverified; `[audit]` |
| dist packs LICENSE + SBOM + no build-machine paths | 100% | `tests/security/test_dist_license_pack.py` 6 passed; `[526104b]` |
| Pre-commit hooks (detect-secrets, ruff, no-tracked-keys, etc.) | 100% | `make install-hooks`; hooks enforcing since `[7035e8c]` |
| make dogfood passes self-hosting | 100% | Per memory notes: `make dogfood` PASSES; no API key required (monkeypatches dispatch); `[gludd-glm-orchestration.md memory]` |
| Operator SSH key rotation + history scrub | 0% | Explicitly out-of-agent-scope; operator action required; `TASKS.md:W5.1 note` |
| Wave 3 merge to master | 75% | Branch tip `6063e51`; gate was RUNNING per SESSION.md; not confirmed merged; `SESSION.md:10-19` |

---

### Dev-Harness Guardrails

| Feature / Task | % | Evidence |
|---|---|---|
| Ratchet-growth guard (W1.1) — RATCHET_MAX constant | 100% | `test_guardrails.py:401 RATCHET_MAX=11`; `config/ratchet.yml` has 11 entries; `[audit W1.1 DONE-VERIFIED]` |
| TASKS.md tick guard in preflight (W1.2) | 100% | `tests/unit/test_preflight.py` + `enforce-make.ts`; `[audit W1.2 DONE-VERIFIED]` |
| State-based stop checks only — vocabulary list deleted (W1.3) | 100% | `[audit W1.2-W1.7 DONE-VERIFIED]` |
| status-snapshot writes SESSION.md in place + drift detector (W1.4) | 100% | `[audit W1.2-W1.7 DONE-VERIFIED]` |
| audit-evidence wired into validate (W1.5) | 100% | `[audit W1.2-W1.7 DONE-VERIFIED]` |
| Makefile hygiene: stderr capture, MYPY_MAX var, gate coverage (W1.6) | 100% | `[audit W1.2-W1.7 DONE-VERIFIED]` |
| preflight fails closed on unknown criteria (W1.7/H16) | 100% | `tests/unit/test_preflight.py` asserts FAIL on unknown criterion; `[audit W1.2-W1.7 DONE-VERIFIED]` |
| Ratchet burn-down: 93 → 11 entries (W2 phases) | 100% | `config/ratchet.yml` 11 entries; `RATCHET_MAX=11`; `[audit W2.x DONE-VERIFIED]` |
| Anti-stop fuzz test (6/6 catching BUGS.md incidents) | 100% | `tests/unit/test_anti_stop_fuzz.py` 6 passed; `[a1c1185]` |
| W3.6 per-item proof table (50 G/S/F/M proofs, 0 GAP) | 100% | `TASKS.md:167-246`; 50 named tests all PASS; `[6915362]` — caveat: ~5-6 items pass tests of partially-inert production code (SpendLimiter, cost-cap); `[audit rank #4]` |
| pydantic-settings UserConfig + GLUDD_ env prefix (W4.4) | 100% | `tests/unit/test_w4_4_pydantic_settings.py` 5 passed; `[15db868]` |
| Watchdog FileWatcher in integrity scanner (W4.3) | 100% | `tests/unit/test_w4_3_watchdog.py` 2 passed + 3 xpassed (FSEvents timing); `[15db868]` |
| deptry installed; langchain/langgraph deferred (W4.5) | 100% | `tests/unit/test_w4_5_deps_audit.py`; `[15db868]` |
| README claims measured / no hardcoded numbers (W5.5) | 100% | `tests/unit/test_status_snapshot.py::TestReadmeNoHardcodedMetrics` 5 passed; `[526104b]` |
| `make release-cut` target exists and runs (enforcement of this table) | 0% | Target referenced in this table's header but not yet confirmed in Makefile; specced-not-verified |

---

### Security Findings Backlog (NEW_FINDINGS_2026-06-16.md)

These are new P1/P2 findings from the deeper-coverage security audit (2026-06-16). All are
grounded at `file:line`. None are scheduled for the current release.

| Finding | Severity | % | Evidence / Location |
|---|---|---|---|
| `TaskDecisionModel.return_id` no FK/unique — dangling decisions | P1 | 0% | `db/models.py:191`; not built; `[NEW_FINDINGS]` |
| `TodoModel.version` optimistic lock is no-op (wired but missing `version_id_col`) | P1 | 0% | `db/models.py:109`; `[NEW_FINDINGS]` |
| `resolve()` leaks secret material via `str(exc)` in logs | P1 | 0% | `secrets/manager.py:112-115,248`; `[NEW_FINDINGS]` |
| `SecretAlias` path/mount injection — arbitrary backend path read | P1 | 0% | `secrets/manager.py:66-91`; `[NEW_FINDINGS]` |
| Worker workspace leak on failure (no cleanup) | P1 | 0% | `worker/app.py:195-217`; `[NEW_FINDINGS]` |
| `CosignKey.__repr__` leaks private_key + password to logs | P1 | 0% | `secrets/cosign.py:12-17`; `[NEW_FINDINGS]` |
| `call_model_with_fallback` never checks circuit-breaker health | P1 | 0% | `models/gateway.py:663-688`; `[NEW_FINDINGS]` |
| `AgentDispatcher.dispatch_one` never calls `registry.can_invoke` — permission matrix dead | P1 | 0% | `agents/dispatcher.py:66-101`; `[NEW_FINDINGS]` |
| Alembic migration drift: 9 tables created, ORM defines 16+ | P1 | 0% | `alembic/migrations/001_initial_schema.py`; `[NEW_FINDINGS]` |
| `_fire_webhook` calls sync `httpx.post` from async path — freezes event loop | P1 | 0% | `events/hooks.py:159`; `[NEW_FINDINGS]` |
| Webhook full event payload leaks model credentials | P1 | 0% | `events/hooks.py:155`; `[NEW_FINDINGS]` |
| MCP tool-name collision silently hijacks routing | P1 | 0% | `mcp/registry.py:31`; `[NEW_FINDINGS]` |
| `connectors/registry.py` arbitrary code execution via `importlib.import_module` | P1 | 0% | `connectors/registry.py:153-163`; `[NEW_FINDINGS]` |
| PSK fail-open: unset `GLUDD_PSK` + `GLUDD_REQUIRE_AUTH` → no auth on /admin | P1 | 0% | `daemon.py:1030/1039`; `[NEW_FINDINGS]` |
| `/api/status` returns db_url (with credentials) to unauthenticated callers | P1 | 0% | `routers/todos.py:177-215`; `[NEW_FINDINGS]` |
| `SpendLimiter.restore()` accepts negative cost → cap evasion | P1 | 0% | `controllers/spend_limiter.py:185-200`; `[NEW_FINDINGS]` |
| F5b/F6a/F6b security features (batch3-security, 14 tests) | 50% | Branch `85158c2`, gate-clean, not merged; `[SESSION.md]` |
| D-04/D-05/D-06/D-29/D-30/D-31 (batch-4 branch) | 10% | Building; not merged; `[SESSION.md]` |

---

### Existing README Claims Audit

The following claims in the body of this README are broadly accurate but carry caveats:

- **"the database layer works"** — accurate for SQLite with the single-worker constraint. Multi-worker or Postgres is explicitly refused.
- **"the model gateway can call real APIs"** — accurate; the gateway is real and tenacity-backed.
- **"CI is being stabilized"** — accurate but understated: the prior CI run (ubuntu) had 10x "Event loop is closed" failures; local fix was committed but CI-green is not yet confirmed by a sandboxcom run with a pasted run ID.
- **"34 roles"** — the Roles section lists roles accurately (count is claimed via `make collection-roles`, not hardcoded).
- Architecture diagram is accurate.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git
- An API key for at least one model provider (Z.AI GLM, OpenAI, DeepSeek, or OpenRouter)

### Install and Verify

```bash
git clone https://github.com/sandboxcom/gludd.git
cd gludd
make init        # set up directories and dependencies
make bootstrap   # init + lint + test + healthcheck
make help        # list all available make targets
```

### Start the Daemon

```bash
# Quick start with defaults (SQLite, no model key — will warn)
uv run gludd daemon --port 8000

# With a config directory and model profile
uv run gludd daemon --config-dir ~/.config/general-ludd --port 8000
```

### Submit Your First Todo

```bash
uv run gludd todo add "Write a unit test for the login endpoint" --queue core
uv run gludd todo list --status queued
uv run gludd status
```

### Check Health and Metrics

```bash
uv run gludd health
uv run gludd version
curl http://localhost:8000/healthz
curl http://localhost:8000/admin/metrics/export
```

### Dogfood the Repo

The daemon can run on its own codebase:

```bash
make dogfood     # runs the event loop on the gludd repo itself
```

## Architecture

```
                     ┌─────────────┐
  User ──CLI/TUI──▶  │   Daemon    │  (FastAPI + Gunicorn, single worker)
                     │  :8000      │  PSK-authenticated API
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Event    │ │  Admin   │ │  Todo    │
        │ Loop     │ │  Router  │ │  Router  │
        └────┬─────┘ └──────────┘ └──────────┘
             │
    ┌────────┼────────┬──────────┬──────────┐
    ▼        ▼        ▼          ▼          ▼
  Claim   Dispatch  Review   Reconcile  Self-Improve
             │
        ┌────▼────┐
        │ Ansible │  (general_ludd.agent collection)
        │ Runner  │
        └────┬────┘
             │
    ┌────────┼────────────────────┐
    ▼        ▼                    ▼
  gludd_*  Roles               Model
  modules  (~34)               Gateway
             │                    │
             ▼                    ▼
      ┌─────────────────────────────────┐
      │     SQLite (single-worker)      │
      │  todos · returns · benchmarks   │
      │  messages · metrics · traces    │
      └─────────────────────────────────┘
```

### Event Loop

Every tick (default: 1 second), the event loop:

1. **Claims** runnable tasks from the queue
2. **Dispatches** them via the Ansible runner with the appropriate model profile
3. **Reviews** completed task returns with a (potentially different) model
4. **Reconciles** decisions — approve, retry, or reject

### API

The daemon exposes a PSK-authenticated REST API. Key endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/facts` | Live daemon snapshot: work/todos/models/history/messages/metrics/traces as Ansible dynamic facts |
| `GET /api/metrics` | Agent-level metrics, global model usage, per-project cost, benchmark rankings |
| `GET /api/traces` | Recent execution traces with per-phase aggregates |
| `POST /api/messages` | Inter-agent message queue: send a message |
| `GET /api/messages` | Inbox for a recipient (supports broadcast) |
| `POST /api/messages/{id}/ack` | Acknowledge a message as read |
| `GET /api/todos` | Task queue management |
| `GET /healthz` | Health check |
| `GET /admin/metrics/export` | Metrics export |

`GET /api/facts` is the backbone endpoint: it aggregates the full daemon state into a single
structured dict that `gludd_facts` injects as `ansible_facts.gludd` so playbook `when:` and
`vars:` conditions can branch on live data without coupling roles to the HTTP layer.

### Database & Concurrency (SQLite Only)

gludd is **SQLite only**. Schema creation and Alembic migrations are SQLite-specific; any
non-SQLite database URL is refused at startup rather than booting into a half-broken state.

Because there is no cross-process claim coordination over a single SQLite file, the daemon
runs a **single gunicorn worker**. `--workers` defaults to 1, and any `--workers N` with
`N > 1` is clamped to 1 with a warning.

### Multi-Model Routing

The model router selects which AI model to use based on role, quality requirement, latency
budget, or work pattern. The shipped config routes to `zai_coder` (Z.AI GLM) with a fallback
chain to `deepseek_coder` and `qwen_coder`. Supported providers: Z.AI, OpenAI, Anthropic
Claude, OpenRouter, vLLM (local), llama.cpp (local). API keys are resolved from OpenBao or
environment variables — never stored in profile YAML files.

## The `general_ludd.agent` Ansible Collection

All task execution happens through the `general_ludd.agent` Ansible collection. Install it
via the collection path (`collections/ansible_collections/general_ludd/agent/`).

### Modules

| Module | Purpose |
|---|---|
| `gludd_ping` | Connectivity check against the daemon |
| `gludd_facts` | Inject `GET /api/facts` as `ansible_facts.gludd` (work/todos/models/history/messages/metrics/traces) |
| `gludd_message` | Inter-agent message queue — send, receive, ack |
| `gludd_skill` | Invoke a named skill on the daemon |
| `gludd_mcp_tool` | Call an MCP (Model Context Protocol) tool |
| `gludd_git` | Git operations (commit, branch, push, diff) |
| `gludd_worktree` | Git worktree management |
| `gludd_db` | Direct SQLite record access |
| `gludd_model_call` | Raw model call with token/cost accounting |
| `gludd_agent_run` | Spawn a sub-agent run |
| `gludd_metrics` | Focused read from `GET /api/metrics` |
| `gludd_traces` | Focused read from `GET /api/traces` |

`gludd_facts` and `gludd_message` form the backbone: facts feeds live daemon state into
playbook logic; message provides the inter-agent coordination queue. `gludd_metrics` and
`gludd_traces` expose observability data as Ansible dynamic facts for playbooks that need
to branch on telemetry.

### Roles

Roles compose modules into full agent task runs. They are grouped by family:

**Code-task roles** — core SDLC actions:
`agent_task`, `debug_failure`, `dependency_update`, `document_change`, `implement_change`,
`refactor_code`, `triage_issue`, `write_tests`

**Audit/report roles** — quality and visibility:
`audit_dependencies`, `audit_security`, `report_audit`, `report_metrics`, `report_status`

**Workflow-pipeline roles** — CI/CD orchestration:
`gate_triage`, `ci_pipeline_repair`, `flaky_quarantine`, `release_build`, `validate_and_push`

**Secure-SDLC roles** — supply-chain and security assurance:
`threat_model`, `security_review`, `secret_scan`, `sbom_generate`, `supply_chain_verify`,
`security_requirements`, `security_gate`

**Agile/sprint roles** — backlog and sprint lifecycle:
`story_create`, `estimate_story`, `backlog_groom`, `sprint_plan`, `standup_report`,
`sprint_board_report`, `velocity_report`, `sprint_review`, `retrospective`

The actual count can be verified with: `make collection-roles`

## Testing

### Unit and Integration Tests

```bash
make test              # full suite with coverage
make test-unit         # unit tests only (fast)
make test-integration  # integration tests
make test-e2e          # end-to-end tests
make test-count        # check collection (0 errors required)
```

### Molecule Harness

Every collection module and role has a molecule scenario under `molecule/playbooks/`. Each
scenario spins up a lightweight stdlib mock daemon (`prepare.yml`), runs the role/module
against it, then verifies results — no real daemon or container runtime required.

```bash
make molecule-test SCENARIO=role_implement_change   # run one scenario
make molecule-test-all                              # run all scenarios (CI-equivalent)
make molecule-scenarios                             # list all scenarios
```

The minimum scenario count is enforced by `preflight.py` (`MIN_MOLECULE_SCENARIOS`);
the gate will fail if scenarios are removed. The real count: `make molecule-scenarios | wc -l`.

### Gate and Preflight

```bash
make gate              # lint + typecheck + collect + test + smoke; writes .gate-status
make preflight         # preflight quality gate (coverage, lint, mypy, templates, molecule, etc.)
make validate          # gate + ansible syntax + healthcheck
```

## Development

### Code Quality

```bash
make lint              # ruff (0 errors required)
make typecheck         # mypy (gate enforces ≤ MYPY_MAX; see Makefile)
make gate              # full gate
make validate          # full validation including ansible syntax
```

### Pre-Commit Hooks

Install once: `make install-hooks`

Every commit runs:
- **trailing-whitespace** — no trailing spaces
- **end-of-file-fixer** — files end with a newline
- **check-yaml / check-json / check-toml** — valid syntax
- **check-added-large-files** — no files over 500 KB
- **detect-private-key** — no SSH/PGP private keys committed
- **no-commit-to-branch** — no direct commits to main
- **detect-secrets** — Yelp detect-secrets scan
- **ruff lint** — Python linting
- **test collection check** — `pytest --co` must succeed

### Git Workflow

```bash
make feature-start MSG='feature/my-feature'   # create branch
# ... work, test, commit ...
make feature-done MSG='feature/my-feature'    # test + merge to master
```

## Example Configurations

### Minimal Config (`~/.config/general-ludd/general-ludd.yml`)

```yaml
model_routing:
  default_profile: zai_coder

# Database defaults to SQLite (~/.local/share/general-ludd/gludd.db).
# If you set a url it MUST be a sqlite+aiosqlite:/// URL — postgres is refused.

budget:
  max_usd: 50
  warn_percent: 80
```

### Model Profiles

Copy from the shipped examples and add your API key:

```bash
mkdir -p ~/.config/general-ludd/model_profiles
cp config/model_profiles/zai_example.yml ~/.config/general-ludd/model_profiles/zai_coder.yml
# Edit zai_coder.yml and set your API key as the ZAI_API_KEY env var
```

Available profiles:
- [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) — Z.AI GLM (primary coder)
- [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) — DeepSeek fallback
- [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) — Qwen fallback
- [`config/model_profiles/openai_example.yml`](config/model_profiles/openai_example.yml) — OpenAI GPT-4
- [`config/model_profiles/anthropic_example.yml`](config/model_profiles/anthropic_example.yml) — Claude

### Model Routing (`config/model_routing.yml`)

```yaml
default_profile: zai_coder
fallback_chain:
  - deepseek_coder
  - qwen_coder
role_routing:
  coder: zai_coder
  planner: zai_coder
  reviewer: zai_coder
```

### Secrets (OpenBao)

```bash
mkdir -p ~/.config/general-ludd/openbao
cp config/openbao/default.yml ~/.config/general-ludd/openbao/default.yml
```

OpenBao supports three modes:
- **external**: Connect to an existing OpenBao or HashiCorp Vault instance
- **auto**: Try external first, fall back to environment variables
- **disabled**: Use environment variables only

On macOS, the daemon automatically prefers Docker over Podman for container-based
OpenBao (Docker Desktop handles port forwarding transparently on macOS).

## Contributing

Pull requests are welcome. Please follow these guidelines:

### PR Requirements

1. **Branch from master** — create a feature branch for your work.
   ```bash
   make feature-start MSG='feature/my-change'
   ```

2. **Keep your commits** — do not squash or flatten your PR branch. Each commit
   should represent one logical change. The merge to master will use `--no-ff` to
   preserve the branch topology.

3. **Include your prompts** — every PR description must include the full prompt(s)
   used to generate or guide the code change. If you used an AI coding agent
   (General Ludd itself, opencode, Copilot, etc.), paste the exact prompts you
   gave it in the PR body under a `## Prompts Used` heading.

4. **Gate must be green** — run `make gate` before opening the PR. The `.gate-status`
   file is the single source of truth.

5. **TDD** — new behavior must have a failing test committed before the implementation.
   The test file and the implementation should be separate commits on the branch.

### Commit Style

- One logical change per commit (one test file, one feature, one fix)
- Messages are imperative: `Add`, `Fix`, `Remove`, `Update`
- Reference issue numbers when applicable

### Before Opening a PR

```bash
make gate           # must be green
make validate       # full validation including ansible syntax
make lint           # 0 errors
make test-count     # 0 collection errors
```

## Configuration Reference

| File | Purpose |
|------|---------|
| [`config/general-ludd.yml`](config/general-ludd.yml) | Main configuration (model routing, database, agents, budget) |
| [`config/model_routing.yml`](config/model_routing.yml) | Model routing with fallback chains |
| [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) | Z.AI GLM profile |
| [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) | DeepSeek profile |
| [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) | Qwen profile |
| [`config/openbao/default.yml`](config/openbao/default.yml) | OpenBao secrets backend |
| [`config/ansible/isolation.yml`](config/ansible/isolation.yml) | Process isolation settings |
| [`config/mcp_servers/example.yml`](config/mcp_servers/example.yml) | MCP server connections |
| [`config/binary_paths.yml`](config/binary_paths.yml) | External binary paths |

## License

MIT
