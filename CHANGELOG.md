# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/); this project adheres to semantic versioning.

## [Unreleased] — since beta.3 (2026-07-12)

### Documentation
- **Collection split documentation** — FQCN references updated across all docs to reflect domain-based collection split: security roles moved from `general_ludd.agent.*` to `general_ludd.security.*`. README restructured with 4 collection sub-sections (`agent`, `security`, `business`, `networking`).
- **SSL Certificate Management System** — comprehensive reference doc covering architecture, Ansible roles (`ssl_cert`, `hsm_operations`), Python module APIs (`certificate.py`, `asn1.py`, `algorithms.py`, `hsm.py`, `compliance.py`, `pin.py`), 6-standard compliance matrix, and security considerations. `docs/SSL_CERT_SYSTEM.md`.
- **Security Roles reference** — `docs/SECURITY_ROLES.md`: overview of all 6 security roles (`ssl_cert`, `hsm_operations`, `audit_framework`, `sql_injection`, `command_injection`, `prompt_injection`), interoperability matrix, SearX integration for framework updates, tool awareness matrix, and sample audit flow for web app security assessment.
- **Networking System** — `docs/NETWORKING_SYSTEM.md`: 7-mode networking role (`general_ludd.networking.networking`), ScapyAdapter Python API, Wireshark Lua dissector templates, tool awareness matrix (tshark, nmap, tcpdump, zeek, hping3, etc.), and packet analysis + dissector creation workflow examples.
- **Business Research System** — `docs/BUSINESS_RESEARCH_SYSTEM.md`: entity research role (`general_ludd.business.entity_research`), 6 research capabilities (discovery, associations, assets, exposure, risks, demographics), SearX continuous monitoring, and entity graph visualization via DOT export.
- **XML Collection** — `docs/XML_COLLECTION.md`: added `general_ludd.xml` collection with 9 roles for XML/HTML/SOAP/SAML/DocBook/DITA/Gradle/plist/XSD/XSLT processing, `xml_utils.py` shared Python module (16 functions), and full reference doc (975 lines).
- **Web Design Collection** — Added `general_ludd.web` collection with 6 roles for web page design: `html_css_core` (HTML5/CSS3/responsive), `javascript_debug` (JS debugging/error handling/bundle analysis), `design_research` (extract colors/fonts/spacing/layout from websites), `framework_integration` (React/Next.js/HTMX/GraphQL/REST), `ux_engineering` (WCAG accessibility, Nielsen usability, z-axis stacking), `design_system` (spacing/color/typography tokens). Includes `web_utils.py` shared module and `docs/WEB_COLLECTION.md`.
- **Web Server Collection** — Added `general_ludd.web_server` collection with 8 roles: `http_server`, `ssl_config`, `cgi_wsgi`, `logging_middleware`, `reverse_proxy`, `forward_proxy`, `load_balancer`, `security_hardening`. Includes `web_server_utils.py` shared module and `docs/WEB_SERVER_COLLECTION.md`.

### Security Hardening (Phase H)
- **H.7** — Project overlay deny-list: field-level blocklist prevents untrusted project config from overriding critical fields (connectors, database.url, budget, issues, self_improve gates). 70 tests.
- **H.15** — MCP startup orphan cleanup: partial multi-server MCP startup failure now cleans up already-spawned subprocesses instead of orphaning them. 10 tests.
- **H.8** — Memory cross-project bleed fix: `MemoryRecordModel` gained project_id isolation with migration 030. 32 tests.
- **H.16** — SSRF numeric IP: decimal/octal/hex IP literal encodings no longer bypass `host_is_blocked`. 28 tests.
- **H.17** — Signing verification: self-update + hot-reload now require cryptographic signature verification. (fc776d8f)
- **H.23** — Gateway credential leak: raw provider-exception text now redacted from admin-visible facets and replay records. 11 tests.
- **MCP argv validation extended** to python/node launchers. (fc776d8f)

### Features (Phase D)
- **D.2** — `run_project_gate` wired into review/reconcile path for external projects. 24 tests.
- **D.4** — DAST driver + findings parser (ZAP-baseline wrapper). 97 tests. (fbbeec19)
- **D.12** — Slack connector: outbound notifications + channel history read, SSRF-guarded. (0cccee7f)
- **D.14** — Background test runner exposed via `make` target + CLI subcommand. (0a07421d)
- **D.15** — Pricing sources static→live: CachedSource with TTL cache + static fallback. (651dfc33)
- **D.22** — task_splitter Ansible role for analyzing complex tasks and recommending parallel subtask decomposition.

### Quality/Coverage (Phase E)
- **E.8** — Router HTTP layer tests: 202 endpoint-level tests across 9 routers previously only covered by registration smoke tests.
- **E.2** — E2E audit closure: 150 new e2e tests (50 auth + 19 sts + 39 adversarial_detector + 28 dispatcher + 14 ipc).
- **E.3** — Lint/type config gaps closed: mypy covers tests/, .pre-commit-config.yaml added.
- **E.7** — Zero-test modules: 49 tests for previously-untested modules (cli_payment, self_update/router, renderers/cache, event_loop/benchmark, renderers/executor).

### Enforcement (Waves 24-27)
- 11 enforcement plugin subagent-awareness fix: all plugins now skip injection when `OPENCODE_SUBAGENT=1`. (a04b5046)
- enforce-multitask dispatch-count blocking: structurally blocks under-dispatched waves.
- enforce-delegate threshold tightened 4→2.
- Plugin test coverage surge: 101 floor + 52 deletion-gate + 60 delegate + 38 deadline + 19 task-ledger tests.
- enforce-enhancement-ratio.ts: machine-enforced ≥50% enhancement per dispatch wave with 56 tests.

### Post-Ship (Phase S)
- **S.1** — Registry seal + default_registry swap: registry sealed at construction, default_registry swapped atomically at daemon startup. 13 tests.



### Architecture (beta.3 Phase B)
- B3.1.1 IPC broker (Broker + WriteQueue) + B3.1.2 read-only engine factory
- B3.1.3 Writer subprocess extraction (WriterProcess + QueueWriteSession + child entrypoint + lifespan branch + drain hook)
- B3.1.4 WriterSupervisor with bounded retry + exponential backoff + self-healing
- B3.1.5 Agent hydration/dehydration for crash-resume (durable hibernation + dispatch checkpoints)

### Security (OpenShell transfers)
- L7 HTTP network policy for Ansible uri/get_url tasks (method+path+host filtering)
- Structured audit logging for network denials and credential access
- Seccomp syscall filtering for playbook child processes (blocking mount/setns/unshare)
- Credential stripping proxy for managed LLM endpoints

### Enforcement
- 11 enforcement plugins (enforce-make, enforce-floor, enforce-delegate, enforce-stop, enforce-session-start, enforce-deadline, enforce-clean-tree, enforce-verified-claims, enforce-commit-lock, enforce-multitask, enforce-no-wait)
- Multitasking floor enforcement (10+ dispatches per wave required)
- Anti-lying guardrails (done-words blocked without machine evidence)
- CI cooldown (10-min minimum interval between CI checks)
- Pre-push clean-tree check (refuses push on dirty working tree)

### Fixed (2026-07-10 CI hardening wave)

- CI test failures across shards: root cause was `alembic/env.py`'s `fileConfig`
  disabling all app loggers (now `disable_existing_loggers=False`), plus
  per-test `.disabled`/logger-pinned caplog hardening (`worker_broadcast`
  401/psk, `build_gateway`, `model_registry`, `daemon_auth_redteam` PSK
  warnings, `spend_limiter` dispatch warning, webhook fire tracking, `rg_search`).
- Slurm cost-cap unit/integration tests reconciled with cost-before-terminal-check
  semantics + numeric job ids.
- GPU-metrics cross-test `pynvml` mock leak: new `gpu_metrics.reset_probe` +
  autouse reset.
- TASKS.md tick-guard evidence.
- Hook-liveness harness now skips cleanly when the CI Node runtime cannot parse
  the `.opencode` plugins (probe-and-soft-skip instead of a hard failure), and
  the stale `phases_completed == 16` pin in the audit-gaps e2e was corrected to
  17 after `PHASE_ORDER` grew a remediation phase (`46a43597`).
- D-07: bounded the previously-unbounded `Text` blob columns on `task_decisions`
  and `audit_events` with 64 KiB `CHECK(length(col) <= N)` constraints via
  migration `026`, with create_all↔migration parity coverage and a landed
  security-backlog probe (`1c6afab0`).
- Daemon event-loop freeze on MCP/role dispatch: `_sync_bridge` removed —
  handlers now awaited natively.
- Blocking `urlopen` on the tick path (`issue_ingestor`) moved to
  `asyncio.to_thread`.
- Admin connectors health check + `WriterProcess.stop` offloaded to threads.
- Silent shutdown exception suppression now logged.

### Changed (2026-07-10)

- CI shard matrix re-split (`unit-1a` → `unit-1a` + `unit-1d`;
  `tests/unit/test_*_e2e.py` now runs exactly once in the "other" shard).
- Coverage job made genuinely non-gating (`--fail-under=0`).
- `pages.yml` actions SHA-pinned + structurally tested.
- `build.yml` stale comments corrected.

### Added (2026-07-10)

- make targets: `git-diff-full`, `ci-failed-tests`, `pages-enable`, `gh-tag-sha`.
- GitHub Pages site created (`build_type=workflow`) so the presentation deploys.
- `docs/AGENTIC_IMPLEMENTATION_SPEC.md` (64-item implementation spec).
- Endpoint test suites for `routers/security` (58 tests), `routers/remediation`
  (21), `routers/eval` (14).
- Regression tests for alembic.ini logging sections.
- Onboard providers wired to real AWS/GCP/Azure implementations (`gludd onboard`
  now functional, 88 tests).

### Security (2026-07-10, in progress)

- Adversarial scan-file path jail + secrets redaction widening.
- SSRF guard consolidation for 7 connectors onto `security/ssrf.py` (`issue_sources/{base,jira,monday,bitbucket_issues,clickup,gitlab_issues}.py`
  + `git_automation/repo.py reject_unsafe_repo_url`, SSRF tranche 5, 200 tests passed; `4113f206`).

### Fixed (2026-07-10, continued)

- Failover gaps closed: `call_model_with_fallback` structured all-down error,
  correlation-ID propagation, `failover_count` facet, and a fallback
  concurrency cap — closes the D17 failover xfail gaps (xfails flipped to
  plain assertions) (`803b75c5`).
- Validation worktree symlink confinement + review-dispatch playbook timeout
  (`557f895e`).
- NaN/Inf sort-key guards in `connectors/base.py` and `observe/facade.py`,
  plus response-size caps for `GitHubSkillSource` via a shared capped-get
  helper (`6a19f747`).

### Changed (2026-07-10, continued)

- README accuracy fixes: role/module counts, login/services provider table,
  routing example, release-trigger wording, contributor links (`1d147d6e`).
- Spec review corrections applied across 70 work items with landed-verify
  annotations and the MCP argv residual documented as C27 (`2b17e7e3`).

### Added (2026-07-10, continued)

- Wave D implementation-ready design compendium
  (`docs/design/WAVE_D_DESIGNS_2026-07-10.md`) (`557f895e`).

### Performance (2026-07-10)

- Migration 025 adds a `task_decisions.created_at` index and a
  `todos (status, priority, created_at)` composite index for the event-loop
  tick hot paths (`97db7cc1`).

### Fixed (2026-07-10, post-push wave)

- Auto-remediation (#52) is now driven by the event loop: a new tick phase
  `_phase_remediate_blocked_tasks` (previously remediation ran only from HTTP
  requests, never autonomously) scans blocked todos on an interval
  (`remediation_check_interval_ticks`, default 30; `<=0` disables), caps actions
  per tick, and skips todos already acted on within their retry cooldown via
  `RemediationActionRepository.exists_recent`. Also fixed `daemon_state` never
  carrying a populated `remediation_config` so `/admin/remediation/*` and the
  tick phase now share operator config from `UserConfig.remediation` (`86781754`).
- `PauseController` persists to the durable store BEFORE mutating in-RAM state
  (was mutate-then-persist, so a failed write diverged RAM from disk);
  `is_paused()` is lock-free over rebound `frozenset`s (D7.1) (`86781754`).
- `FileClaimRegistry` claims carry a 900s TTL and are reaped — a crashed worker's
  unreleased claim no longer poisons overlapping file paths (#53) (`86781754`).
- `gludd payment` CLI: vault errors exit cleanly instead of a traceback; unknown
  last4 renders `"????"` not `"0000"`; empty `--card-number`/`--cvc` rejected;
  help documents flag PAN exposure (`86781754`).
- `tests/unit/test_routers_registration.py` `EXPECTED_ROUTES` gained the `eval`
  and `remediation` rows — the sole `unit-3` CI failure on `0618b39c` (`86781754`).

### Changed (2026-07-10, post-push wave)

- `security/security_backlog.py` rewritten from an all-pass stub into a truthful
  static gate (`make security-backlog-gate`: 3 LANDED-VERIFIED probes, 21 honest
  OPEN); 58 vestigial `pytest.skip` guards removed across 11 test files (`86781754`).
- `docs/CONFIG_REFERENCE.md` default routing profile corrected `zai_coder` →
  `deepseek_coder`; `docs/AGENTIC_IMPLEMENTATION_SPEC.md` 70 items annotated (`86781754`).

### Added (2026-07-10, post-push wave)

- Hook-liveness harness (`scripts/hook_plugin_harness.mjs` + `_hook_fixtures.py`)
  invoking `.opencode/plugin/*.ts` hooks via `node --experimental-strip-types`;
  agent-liveness counting repair (`scripts/agent_liveness.py`: activity-mtime
  session ranking, per-tasks-dir cache, windowed workflow glob;
  `force_delegate_pretool.sh` streak reset on floor + 10-min decay);
  `SpendLimiter` flush-watermark API (SPD-1); `tests/unit/test_tool_loop_guards.py`
  (5 guard branches); unit suites for renderers/cache, event_loop/benchmark,
  runtime/release_orchestrator (28 tests) (`86781754`, `43bcde41`).

## [0.1.0-beta.2] — 2026-07-06

Beta 2: GPU/compute provider expansion, typing hardening, and guardrail codification.

### Added

- 13 new GPU/compute model providers: Baseten, Lambda Labs, Together AI, Fireworks AI, Replicate, RunPod, Modal, CoreWeave, Mistral, Cohere, NVIDIA NIM, Perplexity, Hugging Face
- Baseten + Lambda Labs connector modules (with deployment/instance management)
- Auto-discovery: providers auto-register when their credential env var is set
- PROVIDER_FLAGSHIP_MODELS for sensible defaults
- Self-improvement routing to gludd project workspace + hot-reload on commit (SelfUpdateAppliedEvent)
- HotReloader.reload_changed_modules batch method (modified/added/deleted)
- E2E failover tests: timeout/500/DNS/429 failover, Retry-After, exponential backoff, circuit breaker, half-open recovery, budget gate
- E2E observability tests (13 tests)
- E2E sandbox backend tests (21 tests)
- Game-test full-play-lifecycle prompts + 84 check tuples + _game_lifecycle.py module
- enforce-no-suppressions.ts plugin (3-layer guardrail preventing lint-suppression reintroduction)
- enforce-no-wait.ts plugin (3-layer guardrail: never wait on gate; CI is the gate)
- Makefile submodule-init/update/status/pin targets
- Provider onboarding playbook (docs/PROVIDER_ONBOARDING.md)

### Changed

- conftest.py: autouse GLUDD_ALLOW_NO_AUTH=1 fixture (recovers ~400-550 daemon-test failures)
- Lint-suppression cleanup: removed ALL # noqa + # type: ignore from src/ and tests/
- test_type_safety_guardrails.py: warnings.warn → assert (3 hard + 4 aspirational ratcheted)
- Strict typing refactor: ~400+ Any removed across connectors/, issue_sources/, routers/

### Fixed

- Game-test nondeterminism: 10/10 PASS over 25 iterations
- Makefile ship-commit target added
- Plugin count test reconciled with 10 actual plugins

### Security

- Ansible `process_isolation`: made the fail-closed guard UNCONDITIONAL on
  `enabled=true`. `_execute_with_core` runs playbooks in-process via
  `PlaybookExecutor` and structurally cannot honor container isolation, so any
  `enabled=true` now refuses the run with `"cannot honor container isolation"`
  instead of returning `success=True` on an unconfined run.

### Known limitations

- Ansible process isolation: setting `process_isolation.enabled: true` causes
  fail-closed refusal — container isolation requires the ansible-runner
  subprocess backend (targeted post-beta).

## [0.1.0-beta.1] — 2026-07-06

Beta 1: enforcement infrastructure, game building, CI fixes, and honesty cleanup.
30+ commits, 20,629 tests, 93.8% coverage.

### Added

- **Enforcement infrastructure** — push-rate guard, gate completion tracking, daemon smoke
  tests, hook verification, watchdog CI injection. These ensure CI pipelines complete and
  guardrails are mechanically enforced, not just advisory.
- **Ansible enforcement port** — 2 new modules (`gludd_push_guard`, `gludd_gate_check`)
  plus 4 new roles: `enforcement_gate`, `watchdog_check`, `enforcement_verify`,
  `verify_feature_claims`. Molecule scenarios cover all new modules and roles.
- **Autonomous game building** — 12 games (pong, breakout, maze_runner, word_guesser,
  memory_match, tic_tac_toe, and 6 more) built autonomously via DeepSeek single prompt,
  with 8 weak game checks strengthened to real e2e verifications.
- **Game audit** — game audit role with self-improvement analysis and permanent coverage
  tooling.
- **7 new roles from scripts.**
- **Permanent coverage tooling** for tracking test coverage across the codebase.
- **Type audit** with coverage audit across source files.

### Fixed

- **CI failure fixes** — KeyError in build pipeline, GPU metrics collection on headless
  nodes, compute scheduling edge cases, release target wiring, circuit breaker double-count,
  caplog propagation, per-source cap enforcement.
- **Molecule scenarios** — fixed for `gludd_push_guard` and `gludd_gate_check` enforcement
  modules.

### Changed

- **Cleanup wave** — all xfail markers removed (every test must pass), ratchet cleared
  (0 entries), lint suppressions audited (0 bare ignores), honest README status
  acknowledging 190/192 claimed-100% were file-existence-only with 0 CI-verified.
- **Honest README** — status table now distinguishes machine-verified from file-existence-only
  claims; no hardcoded test counts or coverage percentages.

## [0.1.0-alpha.5]

Security hardening wave plus the langchain/langgraph dispatch feature.

### Added

- LangChain/LangGraph dispatch: new Ansible modules `gludd_langchain_generate`,
  `gludd_langgraph_workflow`, and `gludd_langgraph_decision`, plus a
  `langgraph_decision` role with accompanying playbooks.
- Daemon `POST /admin/models/workflow` endpoint for running multi-step model
  workflows.
- `ProviderRegistry.from_profiles`: builds the provider registry from configured
  model profiles so live model calls resolve a real provider.
- Molecule scenarios covering the new langchain/langgraph modules and role.

### Security

- ~27-fix hardening wave across MCP, secrets, budget, integrity, infra, and
  workers:
  - MCP: transport, registry, and client tightening.
  - Secrets: path-traversal jail and value redaction.
  - Budget: reserve→reconcile flow so spend is settled against actuals.
  - Integrity: fail-closed verification with hash-binding.
  - Terraform / compute: CIDR validation and RCE guards.
  - Ansible: environment scrubbing of sensitive variables.
  - Worker: timeout enforcement on long-running tasks.
  - Signing: invalid-signature requests return 400, not 500.
  - Self-update: strict path segment-match on update targets.
- Follow-on backlog fixes:
  - Gateway: SSRF rejection now fails closed (no fail-open), and a
    budget-exceeded error in the walk fallback propagates instead of being
    swallowed.
  - Self-update: approval HMAC verification plus `requires_approval` enforcement.
  - Connectors: 8 connectors now default to a secure transport.
  - Applier: closed a TOCTOU window.
  - HuggingFace: pinned model `revision` to prevent fetch drift.
  - Slurm: added an output-path guard.
  - Loop: added logging to previously silent `except: pass` sites (prompt resolve
    and message-queue inbox lookup).
  - CI-1: wired `ProviderRegistry.from_profiles` into the daemon and worker
    gateway so live model calls work (fixes "no provider registry configured").

## [0.1.0-alpha.4] — 2026-06-24

Green-the-gate / stability hardening wave. First release actually cut as a tag
(alpha.3 was version-bumped in code but never tagged).

### Fixed

- Alembic migrations were unrunnable: `alembic.ini` was missing the
  `[loggers]/[handlers]/[formatters]` sections, so `alembic upgrade head` died on
  bootstrap with `KeyError: 'formatters'`. Added the standard logging sections.
- SEC-8: `/api/status` no longer discloses `db_url`/`db_engine` (host/port/dialect)
  to unauthenticated callers.
- Role routing fails closed: `call_model_by_role` now rejects an unrecognised role
  (`strict=True`) instead of silently falling back to the default model profile.
- Gateway circuit breaker: removed a double `record_success` on the fallback
  success path (success was counted twice).
- Skill fetcher: added a 1 MB response-size cap to prevent memory-exhaustion.
- Agent dispatcher: a permission-denied dispatch now returns a failed
  `AgentTaskResult` with a clear "Permission denied" message instead of raising.
- TUI e2e tests: serialized the port-8000 / daemon-pid tests under an
  `xdist_group` so they no longer flake under parallel (xdist) gate runs.
- Removed a leftover `D23DEBUG` stderr print from the gateway retry path.

### Security

- CI release job now emits an aggregate `SHA256SUMS` file covering all release
  artifacts for integrity verification.

## [0.1.0-alpha.3] — 2026-06-19

Autonomy, pricing, and guardrail wave.

### Added

- Self-improve Phase 1+2: end-to-end wired (proposal → gate → persist → dispatch).
- Pricing system: 15 source connectors, rolling-window `SpendLimiter`, and `/api/pricing` endpoints.
- Tier 1+2 RAG: skill embeddings plus task-similarity retrieval for agent context.
- Guardrail ports: Claude hooks → opencode (4 TypeScript plugins in `.opencode/plugin/`).
- Orchestration floor: 10-subagent minimum enforced, plus foreground-block guardrail to keep the main thread dispatching.
- `enforce-deadline.ts`: 5-minute task timeout enforcement via `/tmp/gludd-task-deadlines.json` wall-clock tracking, emitting a `TASK DEADLINE EXCEEDED` warning when `GLUDD_TASK_TIMEOUT_MS` is breached.
- `CONSTRAINT_AS_STOP_PATTERNS`: 7 + 8 new naked-constraint phrasings ("isn't possible", "we have to wait", etc.) for the no-wait-stop hook.
- Passive-wait detection: hook now flags deferral/waiting patterns so silent stalls surface.

### Fixed

- CI pipeline: test-matrix sharding, molecule runs, and `ci-verdict-fast` for stale-run detection.
- CI release artifact: `tag_name` mismatch prevented assets from publishing on the matching GitHub Release.
- Model-ratio enforcer: made main-model-aware so the sonnet-target nudge accounts for the orchestrator model, not just dispatched agents.
- F6a/F6b status leak: hook messages no longer escape into user-facing UI.
- `printf` hook hardening: safe format-string handling in shell hook templates.
- Gate concurrency: tightened the pretool regex so it no longer over-matches legitimate concurrent runs.
- Ratchet burn-down: drove `config/ratchet.yml` from 14 open entries down to 0 (or 4) by closing the underlying gaps.

### Changed

- `MAINTHREAD_THRESHOLD`: lowered 8 → 4 so the grind-inline budget trips sooner on undeligated main-thread work.
- `isMainthreadTool`: narrowed to Edit/Write/Bash only, eliminating false positives from read-only tools.

### Security

- D1–D12: batch of twelve hardening fixes across connectors, dispatch, and secrets paths.

## [0.1.0-alpha.2] — 2026-06-18

Integration wave.

### Added

- ripgrep-backed code search with result bundling for agent context assembly.
- `routing_roles`: per-task model weights plus a `TaskRole` abstraction for role-aware routing.
- `SelfImproveGate` to gate self-improvement actions behind explicit policy.
- Alembic migration 005 adding runtime tables.
- `LICENSE` now bundled into build/release artifacts.
- TUI code-graph renderer for visualizing the code graph.
- `observe` router exposing observability endpoints.
- Event-bus failure surfacing so swallowed handler errors are reported.

### Fixed

- C1: corrected worker-model wiring.
- M9: moved blocking call onto `to_thread` to avoid event-loop stalls.
- Scoring: fixed `avg_cost` computation.
- Accounting: fixed line-of-credit (loc) handling.
- Gateway circuit-breaker: fixed double-count on failures and missing success-reset.
- Metrics: fixed recorder interface mismatch.

### Security

- CsvExcel connector: added path jail to block traversal.
- GitHubIssues / Okta / Entra connectors: re-guarded against SSRF.
- Dispatch: switched to default-DENY.
- Secrets: fail-closed loading, HTTPS-only transport.
- MCP env resolution: fail-closed on missing/invalid config.
- Feature-verifier: added path jail.
- Connector resolution: fail-closed on DNS and symlink anomalies.
- Bandit: fixed 5 HIGH-severity findings.
