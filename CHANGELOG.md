# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/); this project adheres to semantic versioning.

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
