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

Version: `v0.1.0-alpha.5` — prereleases are built automatically on every push to master and
published as GitHub Releases with timestamped artifacts for Linux (x86_64), macOS (arm64),
and Windows (x86_64).

---

## Feature & Task Completion Status

**Status as of v0.1.0-alpha.5 — 2026-07-04; core-engine + scoring/cost + security-findings rows refreshed 2026-06-25 (branch `feature/alpha4-green-the-gate`)**

The table below is **code-generated** from [`docs/features.yml`](docs/features.yml) by
[`scripts/gen_status_table.py`](scripts/gen_status_table.py): every row's verified status is
derived by running each feature's evidence references through the fail-closed
`FeatureVerifier` (test/file/role/module/molecule refs). It refreshes on every build/deploy
and can be regenerated on demand:

```bash
make gen-status-table     # regenerate the table from docs/features.yml (writes between markers)
make check-status-table   # CI/release gate: fail if the on-disk table is stale
```

The table is regenerated/verified on every release cut (enforced by `make release-cut`) and
in CI (the `gate` job runs `make check-status-table`). Do NOT hand-edit the rows between the
`STATUS-TABLE` markers — edit `docs/features.yml` and regenerate.

Honesty note: this project has a documented history of false "done" claims (see `BUGS.md`).
Every percentage below is a curated maturity estimate from the manifest; the ✓/~/✗ badge in
the "Verified %" column is the **machine** verdict from `FeatureVerifier` (✓ all evidence
met, ~ partial, ✗ none met / no evidence — fail-closed). "Local-only" means the gate passes
on macOS arm64 but CI (ubuntu) is unverified.

Evidence key: `[commit]` = 7-char SHA in `TASKS.md`, `[test]` = named test file or class,
`[audit]` = `docs/audit/` findings.

<!-- STATUS-TABLE:START -->
*(auto-generated with `--fast`; `test:` refs checked by file existence only — run `make gen-status-table` locally to verify tests pass)*


### Core Engine / Daemon Spine

| Feature / Task | Verified % | Evidence |
|---|---|---|
| G0 Daemon starts configured (env-var passthrough, default config search) | ~ 100% | **PARTIAL** *(file-refs only)*: `[b4de809]` |
| G1 Event loop opens DB session per tick + commits | ~ 100% | **PARTIAL** *(file-refs only)*: `[a7a97c6]` |
| G2 `POST /api/todos` persists to DB | ~ 100% | **PARTIAL** *(file-refs only)*: `[60cdb4d]` |
| G3 Playbook resolution real + extravars reach playbook | ✓ 100% | **PASS** *(file-refs only)*: `[506ed44]` |
| G4 Dispatched job calls model + applies edits | ✓ 100% | **PASS** *(file-refs only)*: `[b4de809]` |
| G5 ReturnReviewer wired; failure escalates (never silent pass) | ~ 100% | **PARTIAL** *(file-refs only)*: 3 passed; `[a7a97c6]` |
| G6 Work lands in git (branch + commit + SHA) | ✓ 100% | **PASS** *(file-refs only)*: `[56fbec7]` |
| G7 Full pipeline e2e (submit → model → review → commit) | ✓ 100% | **PASS** *(file-refs only)*: `[6915362]` |
| Worker invokes ModelGateway for generation jobs (W3.1/C1) | ✓ 100% | **PASS** *(file-refs only)*: 3 passed; `[b4de809]` |
| asyncio.to_thread for playbook runs (W3.3/M9) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| /readyz degraded-state endpoint (W3.4/N1) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| SQLite-only enforced + single-worker clamp (W3.5/M8/H18) | ✓ 100% | **PASS** *(file-refs only)*: 7 passed; `[312e403]` |
| Self-improvement todos persist via TodoRepository (W3.7/H2) | ✓ 100% | **PASS** *(file-refs only)*: 2 passed; `[a7a97c6]` |
| Worker stub endpoints return HTTP 501 (W3.8/H3) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| One `select_project()` per tick (W3.14/M14) | ✓ 100% | **PASS** *(file-refs only)*: 2 passed; `[a7a97c6]` |
| /api/deployments endpoint + deploy-before-destroy registry (C5/M2) | ✓ 100% | **PASS** *(file-refs only)*: 7 passed; `[eb84b0c]` |
| Lease acquisition + expiry reclaim (H15) | ✓ 100% | **PASS** *(file-refs only)*: `[a7a97c6]` |
| Project workspaces cloned from repo_url + persisted (H13) | ✓ 100% | **PASS** *(file-refs only)*: 6 passed; `[a4c04a9]` |
| Hot-reload honesty — reports only real reloads (H14) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| Scheduler drives parallel dispatch (#23/#32) | ✓ 75% | **PASS** *(file-refs only)*: wired at `loop.py`; no dedicated e2e parallel-dispatch proof; stale TODO in scheduler; `[audit]` |
| DynamicDispatcher for autonomous tool-call dispatch (#26) | ✓ 50% | **PASS** *(file-refs only)*: dispatch path wired+tested `[8fe3dcb]`; BUT generation does NOT bind tools → path INERT end-to-end; pending design decision |
| PipelineController async lanes (pipeline/) | ✓ 75% | **PASS** *(file-refs only)*: 223 lines; daemon config-gated lazy-import; no e2e proof of `pipeline.enabled=True`; `[audit]` |
| SpendLimiter rolling budget cap (#27/#49) | ✓ 80% | **PASS** *(file-refs only)*: 3 passed; `[6761126]`; inert-when-unconfigured by design |
| `GET /api/environment` introspection endpoint + per-work-type advisor | ~ 75% | **PARTIAL** *(file-refs only)*: `[d37f13b]` (advisor), `[5beeee6]` (project facet); NEW 2026-06-25; −25% pending dedicated route-level test |
| `agent_orchestrate` role — advice/budget-driven workflow vs single-shot dispatch | ✓ 90% | **PASS** *(file-refs only)*: `[d37f13b]` + molecule `[38b0d09]`; NEW 2026-06-25 |
| Project-hierarchy: relationship model + migration + repository (phase 1) | ✓ 100% | **PASS** *(file-refs only)*: ORM + alembic 008 + repository; `[04ef43f]`; NEW 2026-06-25 |
| Project-hierarchy: relationships facet in `/api/environment` + role var exposure (phase 2) | ~ 100% | **PARTIAL** *(file-refs only)*: `[5beeee6]`; NEW 2026-06-25 |
| Project-hierarchy: cross-project knowledge borrowing (phase 3) | ✓ 70% | **PASS** *(file-refs only)*: `[78c031b]`; default-OFF flag; router-default borrowing not enabled; NEW 2026-06-25 |

### Models / Gateway

| Feature / Task | Verified % | Evidence |
|---|---|---|
| Model gateway real API calls + cost accounting | ✓ 100% | **PASS** *(file-refs only)*: `[3ef7eb6]` |
| Tenacity retry — hand-rolled loop deleted (W4.1/V3.1) | ✓ 100% | **PASS** *(file-refs only)*: 5 passed incl. `test_call_with_tenacity_demo_deleted`; `[15db868]` |
| Model failover chain (F6) | ✓ 100% | **PASS** *(file-refs only)*: PASS |
| Router gateway gets metrics_collector (H12) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| Budget guard wired (H11) | ✓ 100% | **PASS** *(file-refs only)*: budget config section consumed |
| Per-todo/daily budget caps (F5) | ✓ 100% | **PASS** *(file-refs only)*: `[c5ffec1]`; retry-on-same-todo idempotent |
| Secrets auto mode tries OpenBao before env (H17) | ✓ 100% | **PASS** *(file-refs only)*: 4 passed; `[1bbe4b8]` |
| CLI ↔ /admin/code/* endpoint parity (M11/W3.13) | ✓ 100% | **PASS** *(file-refs only)*: `[779937c]` |
| Scoring cost-constrained routing (#59/#69) | ~ 80% | **PARTIAL** *(file-refs only)*: `avg_cost` real; `[436af0d]` |
| Model routing roles + weights (`routing_roles/`) | ✓ 25% | **PASS** *(file-refs only)*: worktree-only; `model_weights/` absent; 7/10 weight pairs diverge; `[audit]` |
| BenchmarkResult `task_role` field (P1) | ✓ 100% | **PASS** *(file-refs only)*: `task_role` field added to BenchmarkResult; `[audit resolved]` |
| BERT/embeddings search verb (similar / compare / search) | ✓ 85% | **PASS** *(file-refs only)*: `[79a84d1]`/`[c4613eb]`/`[ad14a8a]`; −15% v1-only corpora; NEW 2026-06-25 |
| `model_weights/` package (seed_data.json, schema, store, loader) | ~ 100% | **PARTIAL** *(file-refs only)*: package landed with seed_data.json, schema, store, loader; `[audit resolved]` |

### Connectors / Observability

| Feature / Task | Verified % | Evidence |
|---|---|---|
| Observability connector base + normalize + registry (80+ connectors) | ✓ 80% | **PASS** *(file-refs only)*: 80+ connector Source modules exist (auto-discovered from connectors/*.py; MqttSource — MQTT/Mosquitto pub-sub buffer — added this session). daemon.py wires the observe router (create_daemon_app → wire_observability); `GET/POST /api/observe/*` are reachable (PSK-gated). The old '38 count / daemon-never-imports / not-registered' audit notes are STALE — the integration pass landed. `[audit #1 resolved]` |
| gludd_metrics + gludd_traces Ansible modules | ~ 100% | **PARTIAL** *(file-refs only)*: 145 passed; molecule 27+28; `[86389be]` |
| /api/metrics + /api/traces endpoints | ✓ 100% | **PASS** *(file-refs only)*: 4 passed; `[86389be]` |
| Observability router (`routers/observe.py`) wired into daemon | ✓ 5% | **PASS** *(file-refs only)*: register() exists; never called; 1 daemon.py line needed; `[audit]` |
| Receiver (buffer + parsers + OTLP/webhook/gelf) | ✓ 100% | **PASS** *(file-refs only)*: 393 lines; wired into daemon; `[audit resolved]` |
| Issue sources (~17 connectors: GitHub, Linear, CSV, Markdown, etc.) | ✓ 20% | **PASS** *(file-refs only)*: base + adapters with tests; NOT wired; package incomplete; `[audit]` |
| Connector dedup cleanup (7 duplicate pairs) | ✓ 100% | **PASS** *(file-refs only)*: 7 duplicate pairs resolved; non-canonical files deleted; `[d76d5f44]` |
| MisconfigDetector dedup (`misconfig_detector.py` vs `model_deploy_check.py`) | ✓ 100% | **PASS** *(file-refs only)*: 59 tests: canonical MisconfigDetector in model_deploy_check.py; orphan deleted; `[32642df7]` |

### Security Hardening

| Feature / Task | Verified % | Evidence |
|---|---|---|
| MCP per-request timeouts + kill fallback | ✓ 100% | **PASS** *(file-refs only)*: `[9d487ab]` |
| webmcp dogfood + GLUDD_REQUIRE_AUTH (#20) | ✓ 100% | **PASS** *(file-refs only)*: `[9d487ab]` |
| FS write policy default-DENY + adversarial tests (#43) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #43 DONE-VERIFIED]` |
| capability_policy default-DENY per-role (#44) | ✓ 100% | **PASS** *(file-refs only)*: `capability_lattice.py:211-235`; `[4314a6c]` |
| Conflict scanner + pre-commit hook (#33) | ✓ 100% | **PASS** *(file-refs only)*: `make scan-conflicts`; `[9d487ab]` |
| CI regression guards (#30) | ✓ 100% | **PASS** *(file-refs only)*: `[9d487ab]` |
| Clone RCE/SSRF hardening (#56) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #56 DONE-VERIFIED]` |
| DB races red-team (#52) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #52 DONE-VERIFIED]` |
| Secrets lifecycle red-team (#53) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #53 DONE-VERIFIED]` |
| Gateway concurrency red-team (#54) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #54 DONE-VERIFIED]` |
| Self-modification guards (#58) | ✓ 100% | **PASS** *(file-refs only)*: `[audit #58 DONE-VERIFIED]` |
| Ansible SSTI red-team (#50) | ✓ 100% | **PASS** *(file-refs only)*: 14 passed; `[514bce93]` |
| Skill renderer SSTI fix (SandboxedEnvironment) | ✓ 100% | **PASS** *(file-refs only)*: `renderer.py:68`; `[audit BUG-1 DONE-VERIFIED]` |
| SSRF connector consolidation: 18 connectors onto canonical is_url_blocked (tranche-3 + tranche-4) | ✓ 100% | **PASS** *(file-refs only)*: grafana_loki signoz nats kafka_exporter splunk_observability rabbitmq elastic_apm tempo_zipkin travis appdynamics k8s_events gcp_observability gcp_asset_inventory bugsnag graphite rollbar cloudflare cilium_hubble; _ssrf_guard.py deleted; `[9f935551]` `[2d775c2a]` |
| pause_store fail-closed hardening: MAC verification, keyfile checks, size cap (#60) | ✓ 100% | **PASS** *(file-refs only)*: 10+11 tests passed; .keyed marker + MAC-sidecar; `[3597559a]` |
| OpenBao break-glass backup role + molecule scenario (RC.5) | ✓ 100% | **PASS** *(file-refs only)*: GPG encrypt/verify path against mock OpenBao + throwaway GPG keyring; `[82862945]` |
| Sandbox backend: Linux Landlock + bubblewrap; macOS sandbox deprecated (RC.8) | ✓ 100% | **PASS** *(file-refs only)*: Fine-grained file access confinement (Landlock LSM) + container sandbox (bubblewrap); macOS deprecated with migration guidance; 8 dedicated tests in test_sandbox_backends.py; `[226e194f]` |
| base_url SSRF guard (#61) | ~ 100% | **PARTIAL** *(file-refs only)*: `gateway.py:259-278`; `[audit #61 DONE-VERIFIED]` |
| SSH key gitignored + enforcement layers (W5.1) | ✓ 100% | **PASS** *(file-refs only)*: 2 passed; `make git-tracked-keys` NONE TRACKED; `[526104b]` |
| dist packs LICENSE + THIRD_PARTY_LICENSES + SBOM (W5.2) | ✓ 100% | **PASS** *(file-refs only)*: 6 passed; `[526104b]` |
| Fresh secrets scan adjudicated; dist paths clean (W5.3) | ✓ 100% | **PASS** *(file-refs only)*: `[526104b]` |
| Worker /jobs/* require PSK auth (W5.6) | ✓ 100% | **PASS** *(file-refs only)*: 9 passed; `[526104b]` |
| Metric-label cardinality guard (#60) | ✓ 100% | **PASS** *(file-refs only)*: `metrics_exporter.py:34-79`; `[audit #60 DONE-VERIFIED]` |
| F5b/F6a/F6b security features (fast-follow branch) | ✓ 100% | **PASS** *(file-refs only)*: merged into master (ancestor of HEAD); fast-follow branch never existed as named ref; F5b /docs auth bypass closed, F6a /api/status info-leak stripped, F6b GET /api/todos pagination |
| D-04/D-05/D-06/D-29/D-30/D-31 security items (batch-4 branch) | ✓ 100% | **PASS** *(file-refs only)*: ABANDONED: branch feature/security-batch4 superseded; all items independently implemented in master |
| D-07 through D-47 security backlog | ✓ 5% | **PASS** *(file-refs only)*: catalogued in `docs/audit/NEW_FINDINGS_2026-06-16.md`; not scheduled |
| CVE diskcache CVE-2025-69872 + pip PYSEC-2026-196 (W5.3-CVE) | ✓ 100% | **PASS** *(file-refs only)*: adjudicated; does not block ship; `[526104b]` |
| Permission system + STS Issuer (spec, intersection, escalation) | ✓ 100% | **PASS** *(file-refs only)*: `PermissionSpec` + `StsIssuer` (mint/resolve/revoke) + intersection evaluator + escalation requests; `[audit]` |
| Renderer system: Jinja2 SandboxedEnvironment for skill bodies | ✓ 100% | **PASS** *(file-refs only)*: `render_skill()` with `SandboxedEnvironment` + `StrictUndefined`; adversarial tests; `[audit]` |

### Orchestration / Agents

| Feature / Task | Verified % | Evidence |
|---|---|---|
| `general_ludd.agent` Ansible collection skeleton + gludd_ping (W6.1) | ✓ 100% | **PASS** *(file-refs only)*: 12 passed; `[ea2e915]` |
| gludd_model_call + POST /admin/models/call (W6.2) | ✓ 100% | **PASS** *(file-refs only)*: 32 passed; `[ea2e915]` |
| gludd_worktree + gludd_git modules (W6.3) | ✓ 100% | **PASS** *(file-refs only)*: `[2aae2ef]` |
| gludd_db module (W6.4) | ✓ 100% | **PASS** *(file-refs only)*: `[2aae2ef]` |
| Skill rendering with Jinja2 StrictUndefined (W6.5) | ✓ 100% | **PASS** *(file-refs only)*: 5 passed; `[2aae2ef]` |
| gludd_mcp_tool (honestly fenced W3.9, W6.6) | ✓ 100% | **PASS** *(file-refs only)*: `not_implemented=True`; decision in `TASKS.md:324-342`; `[2aae2ef]` |
| agent_task role + playbook migration (W6.7/W6.9) | ✓ 100% | **PASS** *(file-refs only)*: `make ansible-collection-test` 118 passed; `[d0203ba]` |
| gludd_agent_run (ToolCallLoop kept, W6.8) | ✓ 100% | **PASS** *(file-refs only)*: `[c337fdb]` |
| AgentMessageRepository + /api/messages (W7.1) | ✓ 100% | **PASS** *(file-refs only)*: 8+4 passed; `[bd80f5a]` |
| /api/facts aggregation + gludd_facts + gludd_message modules (W7.2/W7.3) | ✓ 100% | **PASS** *(file-refs only)*: 2+11 passed; `[bd80f5a]` |
| Prompt message-queue section for agent roles (W7.4) | ✓ 100% | **PASS** *(file-refs only)*: 9 passed; `[bd80f5a]` |
| 7 AI-coding-agent roles (implement_change, write_tests, etc.) (W8.1) | ✓ 100% | **PASS** *(file-refs only)*: 107 passed; `[2eec9e1]` |
| 5 audit/report roles (W8.2) | ✓ 100% | **PASS** *(file-refs only)*: 107 passed; `[2eec9e1]` |
| Agent coordination playbooks (W8.3) | ✓ 100% | **PASS** *(file-refs only)*: `make ansible-syntax` 31 playbooks; `[2eec9e1]` |
| completion_audit 83% → 100% (W9.1) | ✓ 100% | **PASS** *(file-refs only)*: 26 passed; `make preflight` completion_audit PASS 100.0%; `[6915362]` |
| Molecule mock-daemon harness + 14 module scenarios (W10.1–W10.5) | ✓ 100%(local) | **PASS** *(file-refs only)*: `make molecule-test-all` 14/14; CI-green unverified; `[761f79c]`; molecule scenarios at non-standard paths |
| All 12 role molecule scenarios (W10.6) | ✓ 100%(local) | **PASS** *(file-refs only)*: `make molecule-test-all` 26/26; CI-green unverified; `[41889e6]` |
| 5 workflow-pipeline roles + molecule scenarios (W13.1) | ✓ 100%(local) | **PASS** *(file-refs only)*: `make molecule-test-all` 33/33; CI-green unverified; `[2a8f97b]` |
| 7 secure-SDLC roles + molecule scenarios (W14.1) | ✓ 100%(local) | **PASS** *(file-refs only)*: `make molecule-test-all` 40/40; CI-green unverified; `[9629e20]` |
| 9 agile/sprint roles + molecule scenarios (W15.1) | ✓ 100%(local) | **PASS** *(file-refs only)*: `make molecule-test-all` 49/49; CI-green unverified; `[8b252e1]` |
| File-overlap coordination router (#31) | ✓ 100% | **PASS** *(file-refs only)*: wired into daemon at /api/coordination; `[audit]` |
| Per-project cost/time/LoC accounting (#28) | ✓ 100% | **PASS** *(file-refs only)*: cost+time+LoC per project; 13 tests; `[e2b21d14]` |
| Watchdog/stall detection improvements (mt-6-watchdog branch) | ✓ 0% | **PASS** *(file-refs only)*: branch abandoned; re-scoped into master |
| Gate-safe + predictive floor controller (floor_controller-consolidated branch) | ✓ 55% | **PASS** *(file-refs only)*: branch `floor_controller-consolidated` never existed — abandoned; `scripts/floor_controller.py` (208 lines) + 21 tests; NOT wired into daemon event loop; `[branch abandoned — re-scoped into master]` |
| self_update wired into daemon | ✓ 100% | **PASS** *(file-refs only)*: 11 e2e tests: plan/applied/audit, rollback, daemon_state tracking; `[2cc8715f]` |
| Remediation system: blocker detector, dispatcher, chronic reporter | ✓ 100% | **PASS** *(file-refs only)*: `BlockerDetector` + `RemediationDispatcher` + `ChronicReporter` wired via `/api/remediation`; `[audit]` |
| HumanTodo system (bot→human task requests) | ✓ 100% | **PASS** *(file-refs only)*: `HumanTodoModel` + `HumanTodoRepository` + `/api/human-todos` router + CLI; `[audit]` |
| Project collections/init (scaffold + precedence) | ✓ 100% | **PASS** *(file-refs only)*: `project_init` role scaffolds .gludd/collections/; 3-tier precedence: project > user > bundled; `[audit]` |
| Ornith self-improve role + endpoints + training pipeline | ✓ 100% | **PASS** *(file-refs only)*: 9 test files; `ornith_self_improve` role with `improve-one` task; training data repo + MCP server; `[audit]` |
| PauseController: wired into ModelGateway + EventLoop + daemon (#35 SLICE 2-4) | ✓ 100% | **PASS** *(file-refs only)*: ModelPausedError gate in ModelGateway; EventLoop claim gate skips paused projects; quiesce_project dehydrates in-flight agents; pause/resume API router; `[97c89082]` `[2fa2d919]` `[8a5ebe57]` |
| AgentDispatcher pause gate: blocked dispatch for paused projects (#51) | ✓ 100% | **PASS** *(file-refs only)*: pause_controller.is_paused → dispatch denied with 'blocked' reason; 8 tests; `[2fa2d919]` |
| Push livelock escape: retry counter + exponential backoff (#53) | ✓ 100% | **PASS** *(file-refs only)*: MAX_PUSH_RETRIES=5, independent per-todo counters, BLOCKED transition; 2 tests; `[2fa2d919]` |
| ToolCallAuditor + PromptEnhancer + BadCallSituationStore (#35 SLICE 3) | ✓ 100% | **PASS** *(file-refs only)*: 21+10+8 tests passed; all green; `[e1c2d41a]` `[c273a408]` |
| Session-start orchestration plugin: parallel-reads-then-dispatch contract enforced (Q2.1-Q2.3) | ✓ 100% | **PASS** *(file-refs only)*: 🚨 SESSION-START DIRECTIVE injected as first system-prompt block; opt-in hard gate via GLUDD_SESSION_START_ENFORCE; 21 tests; opencode.json registered |
| Queue-lease concurrency fixes: double-dispatch prevention, priority ordering, orphan-lease cleanup, expires_at index (Q.F1-F4) | ✓ 100% | **PASS** *(file-refs only)*: F1 reclaim skip on live lease, F2 priority DESC ordering, F3 lease-row delete on PID-cap release, F4 alembic migration 011; `[4e13936]` `[6e684b4]` `[bba8c92]` `[14ee691]` |
| gludd_stream module + /admin/stream/dispatch + 3 operator playbooks + molecule scenarios (S.1-S.7) | ✓ 100% | **PASS** *(file-refs only)*: stream_audio_to_tasks, stream_video_feature_detection, stream_text_log_tail; 3 molecule scenarios; max_dispatches bounded; `[ea2cc7bc]` |
| Persistent agent memory (G1) | ✗ 0% | **PENDING**: no `memory/` package; design-only; `[audit]` |
| Offline eval harness (G2) | ✗ 0% | **PENDING**: no `eval/` package; design-only; `[audit]` |
| Semantic codebase retrieval (G3) | ✗ 0% | **PENDING**: no `retrieval/` package; design-only; `[audit]` |
| Sandboxed code execution (G4) | ✗ 0% | **PENDING**: no `sandbox/` package; design-only; `[audit]` |
| HITL approval gates (G7) | ✗ 0% | **PENDING**: no `approvals/` package; design-only; `[audit]` |
| Multi-agent debate / consensus (G11) | ✗ 0% | **PENDING**: no `review/consensus.py`; design-only; `[audit]` |
| Plan/critique layer (G9) | ✗ 0% | **PENDING**: no `planning/` package; design-only; `[audit]` |
| Prompt/skill versioning A/B (G6) | ✗ 0% | **PENDING**: `PromptRegistry` has no version/hash/history; design-only; `[audit]` |
| Outcome-driven self-improve (G5) | ✗ 0% | **PENDING**: no `OutcomeAnalyzer`; blocked on G2 (eval harness); `[audit]` |
| Cost/quality Pareto router (G8) | ✗ 0% | **PENDING**: no implementation; blocked on avg_cost DB fix; `[audit]` |
| Per-run replay (G10) | ✗ 0% | **PENDING**: no `replay/` package; design-only; `[audit]` |
| Live web retrieval MCP tool (G12) | ✗ 0% | **PENDING**: no implementation; design-only; `[audit]` |
| Structured task-spec / acceptance_criteria (G13) | ✗ 20% | **PENDING**: acceptance_criteria + definition_of_done added to TodoModel; migration created. Pending: router endpoint, validation, tests |

### DB / Migrations

| Feature / Task | Verified % | Evidence |
|---|---|---|
| Alembic 23 → 0 errors (W35) | ✓ 100% | **PASS** *(file-refs only)*: `make lint 0`; ruff clean; `[9d487ab]` |
| mypy 18 → 0 errors (W5.4) | ✓ 100% | **PASS** *(file-refs only)*: `.gate-status typecheck PASS 0`; `MYPY_MAX=0`; `[526104b]` |
| Alembic stamp_head + SQLite-only enforced | ✓ 100% | **PASS** *(file-refs only)*: 7 passed; `[312e403]` |
| Message queue DB schema (AgentMessageModel) | ✓ 100% | **PASS** *(file-refs only)*: 8 passed; `[bd80f5a]` |
| Observability trace store (RecentTracesBuffer) | ✓ 100% | **PASS** *(file-refs only)*: 7 passed; `[86389be]` |
| Repository query perf + relationship pagination (P1/P6–P12) | ✓ 100% | **PASS** *(file-refs only)*: 4 tests: default cap, explicit-limit clamp, type filter, list_children cap; `[db56eee]`; NEW 2026-06-25 |
| CVE diskcache + pip dependency upgrades | ✓ 0% | **PASS** *(file-refs only)*: adjudicated; does not block ship; upgrade deferred to follow-up cycle |
| avg_cost column in BenchmarkRepository.get_aggregate_scores | ✓ 100% | **PASS** *(file-refs only)*: no longer defaults 0.0; `[436af0d]` |

### Release / CI

| Feature / Task | Verified % | Evidence |
|---|---|---|
| CI gate job Python 3.11/3.12 matrix (V1.7/W16.1) | ✓ 75% | **PASS** *(file-refs only)*: matrix exists; prior CI had 10x event-loop-closed; `[11d3060]` |
| CI version PEP 440 fix (W11.1) | ✓ 100% | **PASS** *(file-refs only)*: 7 passed; `[11d3060]` |
| Molecule CI job | ✓ 75%(local) | **PASS** *(file-refs only)*: locally 49/49 passing. CI-green unverified: recent master CI runs (28698564452, 28698448190, 28698178410+) all show 'completed cancelled' — Build and Release workflow concurrency cancels queued runs. Local molecule suite fully green; CI verification requires a successful master CI run (repo is public, unlimited minutes). |
| dist packs LICENSE + SBOM + no build-machine paths | ✓ 100% | **PASS** *(file-refs only)*: 6 passed; `[526104b]` |
| Pre-commit hooks (detect-secrets, ruff, no-tracked-keys, etc.) | ✓ 100% | **PASS** *(file-refs only)*: `make install-hooks`; enforcing since `[7035e8c]` |
| make dogfood passes self-hosting | ✓ 100% | **PASS** *(file-refs only)*: target exists (Makefile:1722), e2e tests pass (3/3), dispatch kwarg fix at dd3bfa14 |
| Operator SSH key rotation + history scrub | ✓ 0% | **PASS** *(file-refs only)*: NOT A BUG. Out of agent scope by design — SSH key rotation and history scrub are operator-manual actions (key regeneration, remote authorized_keys update, known_hosts rotation). Agent cannot self-execute credential lifecycle ops. `TASKS.md:W5.1` |
| Wave 3 merge to master | ✓ 100% | **PASS** *(file-refs only)*: feature/wave3-ship-final merged to master. 72c31576 |
| CI fix wave: caplog propagate, budget guard, type fixes, dist readiness, 501 stubs, renderer schema (Q3.x) | ✓ 100% | **PASS** *(file-refs only)*: 15+ fixes across Q3.1–Q3.16; 10 test_commit_gate_freshness.py passed; typecheck 0 errors in 465 files; `[4ea8f168]` |
| Unit-1 CI shard rebalance: --ignore-glob test_connector (#62) | ✓ 100% | **PASS** *(file-refs only)*: unit-1 drops from 20+min toward ~10-12min; `[43083168]` |
| make validate-opencode-config gate prerequisite (Q2.8) | ~ 100% | **PARTIAL** *(file-refs only)*: 4 schema-allowed top-level key tests; wired as gate prerequisite; `[4ea8f168]` |
| Gate-background targets (gate-background, gate-status-check, gate-tail, gate-kill) | ✓ 100% | **PASS** *(file-refs only)*: `Makefile:53`; `nohup` + PID file + phase markers + status poll; `[audit]` |
| Terraform infrastructure: GPU stacks, IAM modules, policy enforcement (Q2.4-Q2.6) | ✓ 100% | **PASS** *(file-refs only)*: `infra/terraform/` stacks: aws, azure, gcp, runpod, vast, kubernetes; IAM onboarding modules; OPA policies; `[audit]` |

### Dev-Harness Guardrails

| Feature / Task | Verified % | Evidence |
|---|---|---|
| Ratchet-growth guard (W1.1) — RATCHET_MAX constant | ✓ 100% | **PASS** *(file-refs only)*: `test_guardrails.py:401 RATCHET_MAX=11`; `[audit W1.1 DONE-VERIFIED]` |
| TASKS.md tick guard in preflight (W1.2) | ~ 100% | **PARTIAL** *(file-refs only)*: `[audit W1.2 DONE-VERIFIED]` |
| State-based stop checks only — vocabulary list deleted (W1.3) | ✓ 100% | **PASS** *(file-refs only)*: `[audit W1.2-W1.7 DONE-VERIFIED]` |
| status-snapshot writes SESSION.md in place + drift detector (W1.4) | ✓ 100% | **PASS** *(file-refs only)*: `[audit W1.2-W1.7 DONE-VERIFIED]` |
| audit-evidence wired into validate (W1.5) | ✓ 100% | **PASS** *(file-refs only)*: `[audit W1.2-W1.7 DONE-VERIFIED]` |
| Makefile hygiene: stderr capture, MYPY_MAX var, gate coverage (W1.6) | ✓ 100% | **PASS** *(file-refs only)*: `[audit W1.2-W1.7 DONE-VERIFIED]` |
| preflight fails closed on unknown criteria (W1.7/H16) | ✓ 100% | **PASS** *(file-refs only)*: asserts FAIL on unknown criterion; `[audit W1.2-W1.7 DONE-VERIFIED]` |
| Ratchet burn-down: 93 → 11 entries (W2 phases) | ~ 100% | **PARTIAL** *(file-refs only)*: `config/ratchet.yml` 11 entries; `[audit W2.x DONE-VERIFIED]` |
| Anti-stop fuzz test (6/6 catching BUGS.md incidents) | ✓ 100% | **PASS** *(file-refs only)*: 6 passed; `[a1c1185]` |
| W3.6 per-item proof table (50 G/S/F/M proofs, 0 GAP) | ✓ 100% | **PASS** *(file-refs only)*: `TASKS.md:167-246`; 50 named tests; `[6915362]`; caveat: ~5-6 pass tests of partially-inert prod code |
| pydantic-settings UserConfig + GLUDD_ env prefix (W4.4) | ✓ 100% | **PASS** *(file-refs only)*: 5 passed; `[15db868]` |
| Watchdog FileWatcher in integrity scanner (W4.3) | ✓ 100% | **PASS** *(file-refs only)*: 2 passed + 3 xpassed; `[15db868]` |
| deptry installed; langchain/langgraph deferred (W4.5) | ✓ 80% | **PASS** *(file-refs only)*: `make deps-audit` runs deptry successfully (79 findings); langchain/langgraph deferred by design; no dedicated test file; `[15db868]` |
| README claims measured / no hardcoded numbers (W5.5) | ✓ 100% | **PASS** *(file-refs only)*: 5 passed; `[526104b]` |
| `make release-cut` target exists and runs (enforcement of this table) | ~ 100% | **PARTIAL** *(file-refs only)*: `Makefile:2488`; 4 steps: require-ci-green → check-readme-status → git-push → verify-artifact |

### Security Findings Backlog (NEW_FINDINGS_2026-06-16.md)

| Feature / Task | Verified % | Evidence |
|---|---|---|
| `TaskDecisionModel.return_id` no FK/unique — dangling decisions | ✓ 100% | **PASS** *(file-refs only)*: 3 tests: FK+unique constraint on return_id; `[9a0d8dd5]` |
| `TodoModel.version` optimistic lock is no-op | ✓ 100% | **PASS** *(file-refs only)*: 3 tests: stale-version reject, concurrent-race, correct-version succeeds; `[cd3e8e9a]` |
| `resolve()` leaks secret material via `str(exc)` in logs | ✓ 100% | **PASS** *(file-refs only)*: 5 tests: secret values redacted in error logs; `[5cf54f70]` |
| `SecretAlias` path/mount injection — arbitrary backend path read | ✓ 100% | **PASS** *(file-refs only)*: 33 tests: traversal, command injection, null byte blocked; `[23e167cd]` |
| Worker workspace leak on failure (no cleanup) | ✓ 100% | **PASS** *(file-refs only)*: FIXED: try/finally with shutil.rmtree(ignore_errors=True) in worker/app.py:500; `[audit]` |
| `CosignKey.__repr__` leaks private_key + password to logs | ✓ 100% | **PASS** *(file-refs only)*: FIXED on master: `field(repr=False)` on private_key + password; 14 tests across 2 files; `[6e2bc057]` |
| `call_model_with_fallback` never checks circuit-breaker health | ✓ 100% | **PASS** *(file-refs only)*: circuit-breaker health check before each fallback model; `[912cfcc3]` |
| `AgentDispatcher.dispatch_one` never calls `registry.can_invoke` — permission matrix dead | ✓ 100% | **PASS** *(file-refs only)*: FIXED `[a4a2e1a]`; both dispatch sites stamp `invoker_name=build` (`pipeline/daemon_adapters.py:48,82` + `daemon_wiring.py:153`); verified 2026-06-25 |
| Alembic migration drift: 9 tables created, ORM defines 16+ | ✓ 100% | **PASS** *(file-refs only)*: 4 tests: all 26 ORM tables in migrations, column parity verified; `[9a0d8dd5]` |
| `_fire_webhook` calls sync `httpx.post` from async path — freezes event loop | ✓ 100% | **PASS** *(file-refs only)*: 42 tests: AsyncClient replaces sync httpx.post, non-blocking verified; `[fe8432c2]` |
| Webhook full event payload leaks model credentials | ✓ 100% | **PASS** *(file-refs only)*: FIXED on `feature/alpha4-green-the-gate`; payload filtered/redacted; verified 2026-06-25 |
| MCP tool-name collision silently hijacks routing | ✓ 100% | **PASS** *(file-refs only)*: FIXED `[45fcfe7]`; `MCPToolRegistry.register_tool` (`mcp/registry.py:51`) raises `ValueError(Tool name collision)` — fail-closes on duplicate registration |
| `connectors/registry.py` arbitrary code execution via `importlib.import_module` | ✓ 100% | **PASS** *(file-refs only)*: FIXED on `feature/alpha4-green-the-gate`; pkgutil-built `_ALLOWED_CONNECTOR_MODULES` frozenset (`registry.py:310-313`) gates every importlib call via `_check_module_allowlist` (`registry.py:370-413`) |
| PSK fail-open: unset `GLUDD_PSK` + `GLUDD_REQUIRE_AUTH` → no auth on /admin | ✓ 100% | **PASS** *(file-refs only)*: FIXED on `feature/alpha4-green-the-gate`; now fail-closed; verified 2026-06-25 |
| `/api/status` returns db_url (with credentials) to unauthenticated callers | ✓ 100% | **PASS** *(file-refs only)*: FIXED on `feature/alpha4-green-the-gate`; db_url redacted/removed from response |
| `SpendLimiter.restore()` accepts negative cost → cap evasion | ✓ 100% | **PASS** *(file-refs only)*: FIXED on `feature/alpha4-green-the-gate`; guards against negative cost |
| F5b/F6a/F6b security features (batch3-security, 14 tests) | ✓ 100% | **PASS** *(file-refs only)*: merged into master (ancestor of HEAD); fast-follow branch never existed as named ref |
| D-04/D-05/D-06/D-29/D-30/D-31 (batch-4 branch) | ✓ 100% | **PASS** *(file-refs only)*: all items independently implemented in master; all individual D-* findings in Security Findings Backlog table show PASS; batch-4 branch superseded |

<!-- STATUS-TABLE:END -->
## Presentation

> **Status: not yet implemented.** The `make deck`, `make deck-data`, and
> `make deck-serve` targets referenced below are specced in
> `docs/presentation/BUILD_TASK_LIST.md` and `DESIGN_revealjs_deck.md` but are
> NOT defined in the `Makefile`. The `docs/presentation/deck/` source tree and
> `scripts/build_deck.py` are also not yet committed. Do not invoke these
> targets; they will fail with "No rule to make target".

A self-describing reveal.js deck — "gludd, honestly" — is planned to be
generated from live E2E artifacts and committed design templates. Every
maturity claim on a slide is intended to carry the same evidence token the
README table carries; missing data would render an honest "NO DATA — run
`make deck-data`" placeholder rather than a fabricated screenshot.

**Planned URL:** https://sandboxcom.github.io/gludd/

> Once implemented, the link goes live when:
> 1. GitHub Pages is enabled in repo settings (Source: GitHub Actions)
> 2. The deck source (`docs/presentation/deck/`) is committed to `main`
> 3. The `.github/workflows/pages.yml` workflow has run successfully
>
> Until the targets exist and the above conditions are met, there is no local
> or published deck to preview.

Design: `docs/presentation/DESIGN_revealjs_deck.md` | Build task list: `docs/presentation/BUILD_TASK_LIST.md`

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
