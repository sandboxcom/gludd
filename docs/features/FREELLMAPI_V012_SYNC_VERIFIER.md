# FreeLLMAPI v0.1.2 Offline Sync Verifier

## Outcome

The v0.1.2 verifier admits a proposed FreeLLMAPI update for **shadow evaluation
only**. It consumes an already-collected JSON-compatible manifest and emits a
content-free receipt. It never fetches source, loads an artifact, mutates
configuration, or promotes runtime behavior.

This is a new boundary alongside the v0.1.1 admission and build proof. It does
not alter the v0.1.1 candidate, vendor artifact, build plan, receipts, tests, or
Make targets.

## Immutable identity and supply chain

Both the baseline and candidate must bind all of the following:

- the fixed repository `tashfeenahmed/freellmapi`;
- a stable semantic-version tag, full 40-character commit, and full
  40-character tree;
- the source archive, package lock, license, deterministic build recipe,
  artifact, artifact signature, SBOM, and provenance SHA-256 digests; and
- affirmative tag, commit, tree, and artifact-signature verification results.

Each release also carries a domain-separated `identity_sha256` over that exact
upstream and supply-chain record. Changing or swapping a tag, commit, tree, or
digest without changing the independently collected identity binding fails
closed. The binding is evidence correlation, not a replacement for upstream
signature or provenance verification.

A candidate must be newer than the baseline and must change its commit, tree,
and archive identity. Mutable refs, all-zero placeholders, partial hashes,
unknown fields, a source/archive digest mismatch, or a failed verification
flag reject the update. Verification flags must be the exact JSON boolean
`true`; truthy numbers or strings are not signature evidence. A tag never acts
as identity by itself.

The verifier deliberately does not implement signature verification or archive
acquisition. Those remain mature upstream/build-tool responsibilities. This
boundary verifies that their immutable, digest-addressed results form a
complete admission record.

## Explicit Gludd-owned shim inventory

The manifest lists every baseline and candidate shim with a stable ID, Gludd
path, kind, digest, and the upstream export it adapts. The verifier accepts only
Gludd-owned adapter, schema, migration, or test-bridge records below
`src/general_ludd/`. Paths must be canonical ASCII POSIX paths attested as
regular files. Traversal components, backslashes, NULs, symlink attestations,
case-insensitive path collisions, any case spelling of a `vendor` segment, a
copied-upstream flag, duplicate ID/path, malformed export name, or incomplete
digest fails closed.

The normalized inventories produce a deterministic, sorted ID-only diff with
`added`, `changed`, `removed`, and `unchanged` groups. Input ordering cannot
change either that diff or the successful receipt. Receipts retain only stable
IDs, counts, and digests; they never echo paths, exports, source, signatures,
provider bodies, or other untrusted content.

This inventories ownership without copying or rewriting upstream code. Exact
upstream tests run only from a separately verified ephemeral archive.

## Schema, migration, and test reuse

Every update carries three independently digested plans:

1. A backward-compatible schema transition with explicit source and target
   versions; the target must strictly advance, so downgrades and no-op
   pseudo-migrations are rejected.
2. A non-destructive `expand-migrate-contract` plan whose ordered phases are
   `expand`, `dual_read`, `backfill`, and `cutover`; contract cleanup is deferred.
3. A test-reuse plan naming the existing Gludd suites and upstream-owned suites.
   Upstream suites must run from an ephemeral verified archive and may not be
   copied into Gludd.

The initial reuse set is the existing upstream admission and exact-source build
suite plus the upstream server suite. Later callers may add suites, but cannot
replace the provenance or no-copy rules.

## Hermetic and live boundary

`offline` is the default and requires zero requests, zero timeout/response
budgets, no downloads, and no config mutation. `live` requires both the
manifest declaration and the explicit `allow_live=True` call. Even then, this
pure verifier performs no I/O: it admits only metadata collection bounded to at
most eight requests, 30 seconds per request, and 1 MiB per response. Artifact
downloads and config mutation remain forbidden.

Successful receipts expose the normalized `declared_mode`, bind the normalized
boundary in `live_boundary_sha256`, and always report
`collection_observed: false`. The latter is deliberate: this pure verifier can
validate permission and bounds, but cannot honestly claim that a network
collection occurred. A separate observed collector must retain its own runtime
evidence; an admission receipt is never that evidence.

Tests use synthetic digests and do not open sockets, download bytes, inspect a
user config, or write admission state.

The JSON-compatible manifest is bounded before semantic validation: 64 KiB,
fixed nesting and node limits, bounded containers, and bounded individual
strings. Recursive, unsupported, over-deep, over-wide, and oversized inputs
produce stable content-free faults rather than unbounded serialization work.
Only exact JSON scalar and container types are accepted; subclasses with
attacker-controlled methods are rejected before those methods can execute.
Because the verifier deliberately performs no filesystem I/O, the process that
collects a shim manifest remains responsible for obtaining the regular-file
attestation without following a symlink. The verifier rejects a non-regular
attestation and all lexical escape forms; it does not pretend to resolve a live
filesystem path.

## ZDD and rollback

Admission is an expand-first shadow step, never a runtime cutover. The ZDD plan
must bind the exact baseline commit, tree, and artifact digest, prove the
rollback test is ready, retain no-downtime behavior, and prohibit state writes
before admission. Candidate inventories must retain every baseline shim; a
removal waits for a later observed contract phase.

Rollback therefore selects the still-present baseline artifact and shim set.
No schema contraction or destructive migration is needed, and a rejected
candidate leaves the active configuration and v0.1.1 evidence untouched.

An independent outcome verifier handles `completed`, `interrupted`,
`cancelled`, and `failed` observations without executing the update. Mutation
followed by interruption or cancellation requires a rollback attempt; every
outcome requires cleanup evidence. Failure precedence is stable and
content-free: rollback failure outranks cleanup failure, which outranks the
primary interruption/cancellation/failure. A completed shadow update still
never admits runtime behavior.

## Receipt scope and replay boundary

Every request binds a project identity digest, a unique operation digest, and
an optional prior-receipt digest. The caller must pass the same trusted values
separately; a manifest cannot choose its own trust scope. Successful and
rejected receipts carry only the normalized scope digest. Receipt consumers
recompute that digest before accepting an admission receipt, so copying one to
another project, operation, or receipt chain fails closed while an idempotent
retry of the same operation remains deterministic.

## Operator and maintainer evidence

FreeLLMAPI is young, so there is no multi-year forum history. The oldest
relevant operator threads reviewed again in October 2026 are still useful and are
recorded without claiming long-term stability:

- [Issue #268](https://github.com/tashfeenahmed/freellmapi/issues/268) reports a
  valid direct-provider credential being auto-disabled while a request silently
  fell back through a mutable `latest` deployment. This supports immutable
  source/artifact identity and forbidding runtime admission from a sync receipt.
- [Issue #488](https://github.com/tashfeenahmed/freellmapi/issues/488) describes
  provider model lists changing frequently; its follow-up says a shipped weight
  fix still did not behave as expected. This supports deterministic diffs and
  reusing behavioral tests instead of treating a successful update as proof of
  equivalent routing quality.
- [Issue #189](https://github.com/tashfeenahmed/freellmapi/issues/189) records
  stale fallback entries after a custom key was removed. The eventual fix had to
  cascade removal through dependent state. This supports retaining all baseline
  shims during shadow admission and deferring contraction until rollback proof.
- [Issue #531](https://github.com/tashfeenahmed/freellmapi/issues/531) was traced
  to hand-edited client/provider configuration rather than a server
  compatibility defect. This supports a verifier that never edits operator
  config and returns only an admission receipt.
- [Issue #674](https://github.com/tashfeenahmed/freellmapi/issues/674) asks for
  daily custom-provider auto-fetch and was closed as not planned. This supports
  explicit bounded live metadata checks instead of an ambient background poller.
- [Discussion #533](https://github.com/tashfeenahmed/freellmapi/discussions/533)
  records an operator finding that Docker `latest` did not identify the newest
  release as expected; the maintainer found that it tracked unreleased `main`
  rather than a release and endorsed the operator's version-tag pin. This
  supports treating tag, commit, tree, archive, and artifact digests as one
  immutable release identity instead of trusting a mutable tag name.
- [Issue #440](https://github.com/tashfeenahmed/freellmapi/issues/440) records a
  custom-provider SSRF report whose first guard needed two follow-up bypass
  fixes. This supports revalidating the complete live boundary on every
  admission, keeping live mode explicit, bounded, metadata-only, and unable to
  mutate config or download an artifact.
- The [v0.12.0 release](https://github.com/tashfeenahmed/freellmapi/releases/tag/v0.12.0)
  announces a request-history schema migration with first-start backfill and
  tells self-hosted operators to back up before updating. That concrete change
  is why v0.1.2 requires a schema, migration, test-reuse, and rollback plan even
  though this verifier itself writes no database state.

## Receipt boundary

A successful receipt says `admitted_for_shadow` and always says
`runtime_admitted: false`. It binds normalized baseline/candidate identities,
the full owned-shim inventory, plans, rollback contract, and normalized manifest
by SHA-256. The two identity fields are the validated, domain-separated release
identities themselves rather than second-order hashes with ambiguous meaning.
A rejection contains only a stable fault category and manifest digest. Both
forms also bind the trusted receipt scope; outcome receipts bind the admission
receipt and normalized outcome by digest. Receipt consumers revalidate fixed
literals, every digest, sorted disjoint diff groups, and exact diff counts before
accepting scope. Unknown, malformed, inconsistent, or oversized input fails
closed rather than being preserved for diagnostics.

## Reproducible verification

The verifier's focused proof is repository-owned rather than dependent on an
operator's ambient coverage configuration. Run the unit contract, measured
line/branch coverage, scoped lint and type checks, and documentation lint with:

```console
make test-files TESTFILES='tests/unit/test_freellmapi_sync_verifier.py'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_freellmapi_sync_verifier.py' COVERAGE_CONFIG=config/coverage_freellmapi_sync_verifier.ini COVERAGE_REPORT=.gate-logs/coverage-freellmapi-sync-verifier.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
make lint-files FILES='src/general_ludd/models/freellmapi_sync_contracts.py src/general_ludd/models/freellmapi_sync_inventory.py src/general_ludd/models/freellmapi_sync_plans.py src/general_ludd/models/freellmapi_sync_verifier.py tests/unit/test_freellmapi_sync_verifier.py'
make typecheck-scope FILES='src/general_ludd/models/freellmapi_sync_contracts.py src/general_ludd/models/freellmapi_sync_inventory.py src/general_ludd/models/freellmapi_sync_plans.py src/general_ludd/models/freellmapi_sync_verifier.py tests/unit/test_freellmapi_sync_verifier.py'
make lint-markdown MARKDOWN_FILES='docs/features/FREELLMAPI_V012_SYNC_VERIFIER.md' MARKDOWNLINT_CONFIG=config/markdownlint-cli2.jsonc
```
