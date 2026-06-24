# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/); this project adheres to semantic versioning.

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
