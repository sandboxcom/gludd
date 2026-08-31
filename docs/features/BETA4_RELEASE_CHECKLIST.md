# Beta4 Release-Cut Checklist

Status: implemented for the `v0.1.0-beta.4` candidate on 2026-08-29.

## Purpose

The pre-release checklist is an observable, read-only rehearsal of Gludd's
actual beta4 release guards. The former beta3 script had drifted from the
release path: it accepted a `.gate-status` file whenever it contained no
literal `FAIL`, called the hosted-only `require_ci_green.py` helper, buffered
long lint/type/collection checks, and maintained private Git and Make recipe
parsers. A running or truncated gate could therefore look green, local
exact-SHA evidence was absent, and an operator could see no progress for
minutes.

The beta4 checklist delegates instead of reimplementing. It invokes, in order:

1. `release-worktree-guard` for current and main checkout cleanliness;
2. `check-gate-fresh` for a terminal, complete, immutable local gate;
3. the canonical Ruff, mypy, and collection targets;
4. `check-readme-status` for the requested tag;
5. `release-dry-run`, which owns exact-SHA dual-track evidence plus runbook,
   changelog, atomic version, and prerelease-shape validation; and
6. `check-tag-immutability` before any publication is attempted.

This is deliberately not a second parser. Version normalization, workflow
state, gate phases, Make recipe structure, tag state, and dual-track
attestations remain owned by their existing guards. A beta3 tag fails the
canonical version check because the repository is at beta4; the checklist
does not carry another release-version table.

## Observable and fail-closed execution

Each phase reuses `security_audit_observability.run_phase`, which starts one
owned process session, inherits non-sensitive child output, emits a start event
and a heartbeat at most every 15 seconds, applies a phase-specific deadline,
and performs bounded TERM-to-KILL cleanup on timeout or cancellation. Phases
run serially, so the checklist never multiplies lint, mypy, collection, or CI
clients.

The terminal JSON event is followed by the compact operator report. Exit `0`
means every phase returned both status `passed` and code `0`; exit `1` means a
release blocker; exit `2` means evidence timed out or could not be collected;
and exit `130` records operator cancellation. An empty plan, runner exception,
missing executable, timeout, partial gate, wrong SHA, dirty tree, mismatched
tag, or nonterminal dual-track lane can never produce READY.

## Upstream and practitioner evidence

Research was refreshed on 2026-08-29.

- GitHub's official guidance describes release-specific tags as the boundary
  for an unchangeable release and recommends validating the candidate before
  publication: [Using immutable releases and tags](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/using-immutable-releases-and-tags-to-manage-your-actions-releases).
- The long-running GitHub Community discussion
  [#171210](https://github.com/orgs/community/discussions/171210), opened
  2025-08-26 and still receiving practitioner reports in 2026, records both the
  need to verify release attestations and the surprising irreversibility of a
  published tag. Gludd therefore checks exact-SHA evidence and tag
  immutability before publication rather than repairing a release afterward.
- GitHub Community discussion
  [#89879](https://github.com/orgs/community/discussions/89879), opened in
  2024, records practitioners losing live Actions log updates while work is
  still running. The checklist emits its own bounded phase events and streams
  every non-sensitive child check so silence is never interpreted as success.
- GitHub Community discussion
  [#180854](https://github.com/orgs/community/discussions/180854), active from
  2025 into 2026, documents release artifacts being associated with the wrong
  identifier when workflows infer "latest" instead of carrying the tag. Every
  tag-sensitive phase here receives the operator's explicit `TAG=<tag>`.

## Zero-downtime deployment

The checklist is pre-publication and read-only. It does not create, move, or
push a tag; publish a release; restart Gludd; alter a database; or stop a local
model. A failing phase leaves the running application and the candidate SHA
unchanged. Only the separate, already-guarded `release-cut` target can mutate
remote release state after this checklist is green.

## Resources

- At most one checklist child process group exists at a time.
- Heartbeats are emitted every 15 seconds; deadlines range from 60 seconds for
  local state checks to 1,200 seconds for exact-SHA dual-track verification.
- Timeout and Ctrl-C cleanup remain owned by the shared observable phase
  runner; the checklist adds no daemon, thread, temporary database, cache, or
  persistent artifact.

## Rollback

Revert the checklist commit to restore the previous operator report. No data
migration, tag rewrite, service restart, or artifact cleanup is required.
During rollback, release publication remains protected by the unchanged
`release-cut` and `release-dry-run` guards, so there is no availability gap.

## Execution-policy fail-fast preflight (2026-08-31)

A long local run can pass its assertions yet still be unusable as release
proof when its attested execution-policy digest differs from the hosted lane.
The checklist therefore starts with the existing canonical producer in
validate-only mode, with `PYTEST_ARGS=` and `MAX_FILES_PER_BATCH=64` stated
explicitly. Validate-only now applies `--require-release-policy` before it
prints a plan. A mismatch exits immediately, before repository inspection,
test collection, the general gate, or an attestation write. Release readiness
also runs and reports that same bounded command and exposes the canonical
policy digest; the exact-SHA dual-track verifier remains the final authority.

This ordering follows GitHub's documented matrix failure model, where
`strategy.fail-fast` cancels in-progress and queued matrix jobs after a
failure. The long-lived GitHub Community discussion
[#38361](https://github.com/orgs/community/discussions/38361), reviewed
2026-08-31, records that practitioners still lack an equivalent automatic
fail-fast boundary across independent jobs. Gludd therefore performs its
cross-phase compatibility check locally before starting expensive independent
phases instead of relying on a late workflow cancellation.

This is zero-downtime deployment protection: the preflight is read-only and an
invalid immutable candidate remains untagged and undeployed. Rollback reverts
the preflight/checklist commit; the unchanged exact-SHA verifier continues to
reject mismatched evidence. Resources are bounded to one short-lived
validate-only Python process under the existing observable checklist owner. It
starts no pytest worker, daemon, model server, network request, or artifact
writer, and its process group retains the existing timeout and cleanup rules.

- [GitHub matrix failure handling](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs#handling-failures),
  reviewed 2026-08-31, documents matrix fail-fast cancellation behavior.
