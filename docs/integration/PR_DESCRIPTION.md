# Integration Wave — next alpha (2026-06-17)

## Summary

This PR lands an integration wave that wires up previously-isolated execution and
model-routing work, hardens the cost/budget/routing accounting, and closes a batch of
fail-closed security gaps across the connector and dispatch surfaces. It corrects the
worker-model wiring (C1) and removes an event-loop stall in the model path (M9), adds
role-aware routing, ships a new Alembic migration with runtime tables, bundles the LICENSE
into release artifacts, and resolves all outstanding bandit HIGH-severity findings. The
changes are overwhelmingly additive or fail-closed; the one posture-changing item is that
`/api/dispatch` now defaults to DENY.

## Highlights

### Execution / model wiring (C1 + M9)
- C1: corrected worker-model wiring so the worker resolves the intended model.
- M9: moved a blocking call onto `to_thread` to avoid event-loop stalls in the model path.
- Event-bus failure surfacing: swallowed handler errors are now reported instead of lost.

### Cost / budget / routing
- `routing_roles`: per-task model weights plus a `TaskRole` abstraction for role-aware routing.
- Scoring: fixed `avg_cost` computation.
- Accounting: fixed line-of-credit (loc) handling.
- Gateway circuit-breaker: fixed double-count on failures and the missing success-reset.
- Metrics: fixed a recorder interface mismatch.
- `SelfImproveGate`: gates self-improvement actions behind explicit policy.

### Security fail-closed fixes
- Dispatch: switched to default-DENY.
- Secrets: fail-closed loading with HTTPS-only transport.
- MCP env resolution: fail-closed on missing/invalid config.
- Connector resolution: fail-closed on DNS and symlink anomalies.
- CsvExcel connector and feature-verifier: added path jails to block traversal.
- GitHubIssues / Okta / Entra connectors: re-guarded against SSRF.

### Schema / packaging
- Alembic migration 005 adding runtime tables.
- `LICENSE` now bundled into build/release artifacts.

### Tooling
- ripgrep-backed code search with result bundling for agent context assembly.
- TUI code-graph renderer for visualizing the code graph.
- `observe` router exposing observability endpoints.

## Security note

This wave closes several fail-closed and SSRF/jail gaps:
- SSRF re-guards on the GitHubIssues, Okta, and Entra connectors.
- Path jails added to the CsvExcel connector and the feature-verifier to block traversal.
- Connector resolution now fails closed on DNS and symlink anomalies.
- Secrets load fail-closed over HTTPS-only transport; MCP env resolution fails closed on
  missing/invalid config.
- Dispatch defaults to DENY.
- Bandit: the 5 outstanding HIGH-severity findings are fixed (bandit 0 HIGH).

## Testing

The full gate is run for this batch (lint + typecheck + unit/e2e). New connector, dispatch,
and verifier behavior is covered by added tests alongside the modules they exercise
(CsvExcel jail, SSRF re-guards on GitHubIssues/Okta/Entra, feature-verifier jail, dispatch
default-DENY, secrets/MCP fail-closed paths). Run the full `make` gate before merge — do not
rely on partial/filtered runs.

## Deferred follow-ups

Deferred items are tracked in `docs/integration/FOLLOWUP_2026-06-17.md` (ordered ship-blockers
first, then correctness/wiring, then cleanup). The P0 ship blockers that MUST be resolved
before any release:

1. **Relocate + rotate `sandboxcom_github_rsa`** — a private SSH key is committed in the repo
   tree. Move it out of version control and rotate it before release.
2. **Fill the `RG_SHA256` placeholder in `bundle-ripgrep`** — the Makefile target ships a
   placeholder checksum, so integrity verification is currently a no-op. Replace it with the
   real ripgrep release SHA256.

P1 (correctness/wiring) and P2 (dedup/cleanup) items — including forcing auth on
`POST /api/dispatch`, wiring `SaturationController` into the event loop, per-queue PID
buckets, and the batch-3 dedup set — are enumerated in the follow-up doc.

## Risk / rollout notes

Most changes in this wave are additive or fail-closed, so the blast radius is low: tighter
defaults reject rather than mis-process. The one behavior-changing item operators must note
is that **dispatch now defaults to DENY**, which changes the live `/api/dispatch` posture —
callers that previously relied on permissive dispatch will be rejected until explicitly
allowed. Validate the dispatch path against expected callers before/at rollout. The Alembic
005 migration adds runtime tables and should be applied as part of the deploy. Remaining P0
ship blockers above gate the actual release tag.
