# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/); this project adheres to semantic versioning.

## [Unreleased] — next alpha — 2026-06-17

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
