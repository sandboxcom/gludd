# Exact-Gate Rerun Acceleration Decision

**Status:** Accepted; exact-SHA local admission and bounded two-slot cold queue implemented
**Decision date:** 2026-10-07
**Scope:** Same-head reruns of the canonical local Pytest/Coverage.py gate

The phase-one writer and phase-two audit boundaries are recorded in
[`docs/features/EXACT_GATE_BATCH_RECEIPTS.md`](../features/EXACT_GATE_BATCH_RECEIPTS.md)
and
[`docs/features/EXACT_GATE_SHADOW_REPLAY_AUDIT.md`](../features/EXACT_GATE_SHADOW_REPLAY_AUDIT.md),
with the bounded observability follow-up in
[`docs/features/EXACT_GATE_SHADOW_PROGRESS.md`](../features/EXACT_GATE_SHADOW_PROGRESS.md)
and sanitized diagnostics in
[`docs/features/EXACT_GATE_FAILURE_RECEIPTS.md`](../features/EXACT_GATE_FAILURE_RECEIPTS.md).
The bounded historical migration boundary is recorded in
[`docs/features/EXACT_GATE_LEGACY_FAILURE_IMPORT.md`](../features/EXACT_GATE_LEGACY_FAILURE_IMPORT.md).
The offline local trust boundary and unsigned migration are recorded in
[`docs/features/EXACT_GATE_RECEIPT_AUTHENTICATION.md`](../features/EXACT_GATE_RECEIPT_AUTHENTICATION.md).
Local gates now admit authenticated exact-SHA pass receipts before execution,
restore their coverage fragments atomically, and reconcile executed, resumed,
and not-started batches with measured time saved. Hosted CI remains cold.
Progress/ETA remains estimate-labeled, and the 85% aggregate/75% per-file
coverage floors are always recomputed from actual fragments.

## Decision

Do not put `pytest-testmon`, `pytest-split`, or an unbounded `pytest-xdist`
pool in the release-evidence path. Extend the existing serial runner's
resume boundary into a pass-only, content-addressed batch receipt layer. A
cached batch may replace execution only when its complete action identity is
identical to the current clean exact head. Coverage.py remains the owner of
branch-data validation and aggregation, and the terminal gate must reconcile
every planned batch exactly once whether it was executed or admitted from a
receipt.

This is an application-specific exact-attestation adapter, not a second test
runner or generic cache. It follows the mature action-cache model documented by
Bazel and Pants while keeping the existing Pytest, pytest-cov, Coverage.py,
resource supervision, shard registry, and exact-SHA attestation owners. A
separate Pants pilot is the preferred long-term replacement for the adapter,
but only for an allowlisted hermetic unit slice after input-declaration parity
is proved. Migrating the release gate to Pants before that proof would make
implicit assets, environment inputs, and external services invisible to its
cache key.

The rollout must always retain one cold hosted run for the same exact SHA.
Local composed evidence may accelerate a repeated exact-head run; it may never
substitute for the paired hosted lane, cross a commit boundary, or authorize a
release by itself.

## Current boundary and latency source

The control run reported 250 fresh-process batches at 16 files. After five
batches its ETA was 5,468 seconds (about 91 minutes); batch 10 failed, yet the
old collect-all behavior launched batches 11 through 18 before manual
cancellation. The implementation explains and closes that shape:

- `scripts/run_gate.sh` invokes `scripts/run_ci_shards_serial.py` with
  authenticated exact-SHA admission by default; `GATE_EXACT_SHA_RESUME=0`
  forces a cold run.
- The local gate defaults to 32 files per batch and two xdist `loadfile`
  workers, both hard capped. The runner still runs bounded batches and named
  shards serially so import state, native allocations, temporary roots, child
  processes, and coverage files have bounded owners.
- The existing resume schema checks only `schema_version`, `candidate_sha`, and
  the runner name. Its batch key contains shard, ordinal, and a digest of file
  names. It does not bind the interpreter, locked environment, plugins,
  coverage configuration, execution policy, platform, or declared non-source
  inputs.
- Resume coverage fragments are removed during normal terminal cleanup. The
  mechanism can help an abruptly interrupted producer whose files survive, but
  it cannot accelerate an ordinary terminal-red rerun.
- A resume hit compares the source fragment with the copy it just made. The
  durable state does not retain the expected fragment digest, semantic coverage
  mode, or batch outcome manifest needed to distinguish an intact pass receipt
  from self-consistent stale or corrupted state.
- `make gate` permits a dirty developer worktree, while `HEAD` identifies only
  committed content. Dirty runs remain useful diagnostics but are not release
  evidence and must never consume exact-head receipts.

The first-run slice therefore widens only to a tested 32-file ceiling and a
two-worker `loadfile` queue with restarts disabled. Same-head receipt reuse
addresses retry cost; those finite bounds address cold startup/runtime cost
without creating a custom scheduler or an unbounded resource envelope.

## Non-negotiable evidence invariants

1. A changed Git SHA is a cold cache. There is no affected-test inference across
   commits in a release gate.
2. Only a clean worktree at the expected full SHA is receipt-eligible. A dirty,
   untracked, ambiguous, or failed Git query disables reads and executes the
   batch normally.
3. Only terminal passing batches with valid branch-coverage data and successful
   owned-resource cleanup produce reusable receipt candidates. An executed
   nonzero batch may produce a sanitized, bounded diagnostic only after cleanup;
   failures, cancellations, timeouts, warnings-as-errors failures, missing
   coverage, and incomplete cleanup are never cacheable or admissible.
4. Cache uncertainty is a miss, never a pass. The batch executes normally when
   a manifest, identity field, input, digest, path, or coverage database is
   absent, malformed, mismatched, unreadable, or newer than the supported
   schema.
5. The final attestation enumerates every canonical batch exactly once and
   distinguishes `executed` from `receipt`. Missing, duplicate, foreign, or
   extra batches fail the gate before coverage aggregation or release evidence.
6. Aggregate branch coverage is recomputed from the admitted fragments on every
   attempt. The existing 85% aggregate and 75% per-file floors do not consume a
   cached summary or percentage.
7. The paired hosted lane executes cold at the same full SHA until a separate
   reviewed decision explicitly changes that rule.
8. The local worker limit is exactly two and the heavy-operation semaphore
   remains authoritative. No option may widen the pool above two.

## Candidate comparison

| Candidate | What it can improve | Exact-gate ruling |
| --- | --- | --- |
| `pytest-testmon` | Selects tests affected by executed Python code and prioritizes likely failures. | **Reject for release evidence.** Gludd requires branch coverage, while Testmon's implementation rejects simultaneous pytest-cov branch coverage. Its own documentation excludes static files and external services from tracked dependencies. The database is useful for a non-authoritative developer loop, not for proving the complete exact head. |
| `pytest-split` | Uses stored durations to balance complete groups across independent executors. | **Defer.** It does not cache results, so a serial 244-batch rerun still executes all work. A future hosted-tail pilot must first prove that the union of collected node IDs equals the canonical plan with no duplicates. |
| `pytest-xdist` | Schedules tests across worker processes and pytest-cov can combine their coverage. | **Use only the bounded local two-slot policy.** The mature runner caps xdist at two `loadfile` workers, disables worker restarts, preserves batch isolation, watches controller ownership, and retains a one-worker rollback. The hosted paired lane stays cold and single-worker. |
| Pytest `--lf` / `--stepwise` | Reruns known failures or resumes after the last failure. | **Reject as terminal evidence.** Pytest's cache records selection state, not passing coverage fragments or a complete exact-input receipt. It is a diagnosis tool only. |
| Pants test cache | Hermetic Pytest processes, fine-grained local/remote result caching, explicit environment inputs, batching, force-rerun, and combined coverage. | **Preferred mature pilot, not immediate gate replacement.** It has the right cache model, but Gludd must first declare every asset, dynamic import, environment input, service, fixture compatibility group, and non-cacheable test. Start with an allowlisted pure-unit slice in shadow mode. |
| Bazel test/action cache | Content-addressed declared inputs, hermetic execution, sharding, resource classes, and local/remote result reuse. | **Use as the semantic reference.** A second build-system migration is less Python-native than Pants and would not remove the same input-declaration work. |
| Existing serial runner plus exact batch receipts | Reuses already-passing coverage-producing batches from the same exact execution identity while retaining current process and cleanup boundaries. | **Select for the near term.** It is the smallest reversible change and the only option that directly accelerates a same-head rerun without changing collection order or concurrency. |

`pytest-testmon` can remain a future opt-in command for fast local feedback only
if it is clearly named non-authoritative, runs without branch coverage, never
writes release attestations, and a later full gate is mandatory. `pytest-split`
may later balance hosted jobs after exact node-set reconciliation exists.
Neither belongs in the initial acceleration slice.

## Exact action identity

Each batch receipt key must be a canonical JSON SHA-256 over all fields below.
The manifest stores the canonical input document as well as its digest so a key
cannot conceal an omitted field.

| Identity family | Required fields |
| --- | --- |
| Source | Full 40-character Git SHA, clean/untracked-free worktree result, repository-state identifier, and canonical test-file list with its digest. |
| Plan | Shard name, batch ordinal, max-files value, complete shard-plan digest, canonical collection-manifest digest, and exact execution-policy digest. |
| Runner | Receipt schema, runner/attestation implementation digests, command arguments, timeout/no-progress policy, worker count, distribution mode, and cleanup-policy version. |
| Python/toolchain | Implementation, full patch version, resolved executable identity, UV version, lockfile digest, dependency-profile digest, and installed-distribution fingerprint. |
| Test plugins | Exact versions of Pytest, pytest-cov, Coverage.py, timeout/async/xdist plugins, and the complete loaded-plugin inventory digest. |
| Coverage | Coverage configuration digest, branch-mode flag, source/omit/path settings, concurrency mode, and data schema version. |
| Platform | Operating system, release, architecture, Python ABI, and resource-arbiter namespace schema. |
| Environment | Names and canonical values of an explicit non-secret allowlist. A credential, undeclared variable, external service, live clock dependency, or network dependency marks the batch non-cacheable unless it has an explicit immutable fixture identity. |
| Inputs outside Git | Digest and declared owner for every generated fixture, executable, model, database snapshot, or other admitted file. Absence of a declaration is non-cacheable, not an ignored input. |

The full Git SHA is intentionally not replaced by dependency selection. Even if
Pants or Testmon can prove a particular source file unaffected, release evidence
for SHA B must never import a passing result produced for SHA A.

Secret values must not be written or placed in an ordinary unsalted digest.
Credential-bearing or live-provider tests are non-cacheable in the first
implementation. If a later slice requires a secret-dependent identity, it needs
a separate keyed local digest design and security review.

## Receipt and admission protocol

One passing action writes an owned directory containing:

- a strict-version JSON manifest with the action identity, originating run ID,
  start/completion times, return code zero, and cleanup result zero;
- a normalized JUnit outcome manifest proving the expected node IDs were
  collected and reached terminal outcomes under the canonical marker policy;
- the branch-aware Coverage.py fragment, its byte length, SHA-256 digest, and
  semantic validation result;
- bounded log and warning summaries with their digests, never unbounded stdout
  or secrets; and
- an atomic completion marker published only after every other file is fsynced
  and the temporary directory is renamed.

The gate admission sequence is:

1. Resolve the clean exact-head, toolchain, policy, plan, environment, and input
   identities before the first batch.
2. For each canonical batch, compute its key and inspect only the project-owned
   cache root. Reject symlinks, traversal, unexpected owners/modes, duplicate
   manifests, and non-regular files.
3. Validate the manifest schema and exact identity, recompute all artifact
   digests, open Coverage.py data read-only, and prove branch compatibility.
   Any problem emits a bounded `RECEIPT-MISS reason=<class>` and executes the
   batch. It never degrades to a hit.
4. On a valid hit, copy or hard-link only after same-filesystem and ownership
   checks, then re-hash the admitted fragment. Emit one visible
   `RECEIPT-HIT shard=<name> batch=<n> source_run=<id>` marker.
5. On a miss, use the current supervised fresh-process path. Publish a receipt
   only after Pytest, coverage preservation, process-group teardown, temporary
   cleanup, and interpreter-drift checks all pass.
6. Reconcile the canonical plan against executed and admitted receipts. Then
   combine the actual fragments with Coverage.py and run the current coverage
   audit from scratch.
7. Publish a terminal exact-SHA attestation containing the action-identity
   digest, plan digest, executed/hit counts, each receipt digest, normalized
   coverage-result digest, and the existing repository identity.

The reusable cache is pass-only. A previously failed batch must execute again.
A bounded failure receipt may preserve only sanitized diagnostics and is a hard
non-reusable auditor refusal, even when contradictory pass evidence exists.
Passing batches from the same terminal-red attempt may be admitted in a future
phase because every batch already runs in a fresh process and retains its own
coverage/cleanup receipt. A safety stop invalidates the active batch and every
batch that never started; it does not invalidate earlier independently
completed receipts unless the stop reports repository, interpreter, policy, or
shared-input drift.

## Fail-closed cache lifecycle and resource bounds

The cache lives below the existing project-namespaced resource root, never in
the checkout and never in a user-global Pytest directory. One gate lock remains
the writer boundary. No daemon, remote service, database server, background
pruner, or custom parallel scheduler is introduced.

Initial bounds are deliberately conservative:

- retain at most two exact-SHA generations and at most 2 GiB total;
- require the existing disk-reserve check before every write;
- stop storing new receipts when the cap is reached, but continue uncached test
  execution and preserve the gate result;
- never delete an active or leased generation;
- prune synchronously before a gate or through an explicit validate/apply owner,
  with per-generation paths and visible byte counts; and
- treat lookup/validation time above 60 seconds for the whole plan as a cache
  failure and execute cold rather than waiting indefinitely.

The local gate defaults to exactly two `loadfile` workers and at most 32 files
per batch. The direct and hosted runner remains one worker with the 16-file
bound, and the explicit rollback restores those settings locally. The
heavy-operation semaphore remains authoritative. Cache reads must emit periodic
progress and a terminal hit/miss/corrupt/non-cacheable summary. A cache error
cannot suppress a test, coverage audit, warning, or cleanup failure.

The cache stores only JSON, bounded text, JUnit XML, and Coverage.py's existing
SQLite data format. It must not deserialize pickle or execute cache content.
Path confinement, regular-file checks, ownership/mode checks, exact digests,
schema allowlists, and a no-secret regression are release requirements.

## ZDD rollout

This feature changes validation artifacts only. It starts no application
service, changes no database schema, opens no listener, replaces no worker, and
publishes no release asset. A gate using the new code keeps the implementation
loaded at process start; an overlapping older gate is already excluded by the
project gate lock. Serving Gludd processes are unaffected.

Adopt in four reversible stages. The report-only audit is part of stage one,
not permission to enter stage two:

1. **Shadow write, audit, and progress:** execute every batch, write candidate
   pass receipts and sanitized non-reusable failure diagnostics, validate them
   immediately, report prior exact candidates only after the fresh
   outcome/coverage/cleanup control exists, and render only bounded
   estimate-labeled progress. Never skip work or infer terminal green. Compare
   normalized cold coverage and outcome manifests for at least three exact
   clean gates.
2. **Local opt-in admission (complete):** permit receipts to replace execution
   only for an explicit local mode and the same clean SHA. Keep the hosted paired
   gate cold. Dirty gates execute fully.
3. **Local default (this slice):** enable read/write for exact clean reruns,
   retain a bounded force-cold mode, and keep the hosted release verifier cold.
4. **Pants hermetic CAS follow-up:** run an allowlisted pure-unit slice through
   pinned Pants with local caching, concurrency one, declared transitive inputs,
   explicit assets/environment, and global branch coverage. Compare node sets,
   outcomes, coverage, resources, and cache invalidation against the canonical
   runner. Do not build a custom cross-commit dependency walker. Promotion
   requires a new decision; remote cache and broader concurrency are out of
   scope.

Rollback runs `make gate GATE_EXACT_SHA_RESUME=0
GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1`, after which the existing
runner executes every batch and ignores admission. Cache removal is a separate
owned validate/apply operation performed only when no gate lease is active.
Rollback never edits a Git ref, release attestation, serving process, or
published artifact. The current cold hosted lane remains available throughout.

## Measurable acceptance

| Gate | Required evidence |
| --- | --- |
| Failing-first behavior | Tests first prove that SHA, dirty state, interpreter, lock, plugin, arguments, coverage config, platform, environment, input, fragment, schema, node-set, and cleanup drift each prevent a hit. |
| Cold parity | With an empty cache, every canonical planned batch executes and the normalized node outcomes and branch-coverage report equal the current runner's result. |
| Shadow parity | Three clean exact-head shadow runs report zero receipt-validation, node-set, outcome, coverage, cleanup, or attestation mismatches. |
| Warm rerun | After one controlled terminal-red batch, a same-head rerun executes every failed/non-cacheable batch, admits only independently passing batches, and reconciles exactly the canonical unique batch count. |
| Cross-head refusal | A one-commit source, test, configuration, runner, or lock change produces zero cross-SHA hits even when the changed file is unrelated to a batch. |
| Corruption refusal | Truncated JSON/XML/coverage, changed bytes, symlinks, wrong modes, duplicate keys, missing completion markers, and unsupported schemas all execute cold or fail safely; none can produce a pass. |
| Coverage | The warm run recomputes at least 85% aggregate and at least 75% per production file from admitted fragments; normalized executed lines and branches equal the cold control. |
| Attestation | The release verifier rejects a missing/duplicate/foreign receipt, a cached summary without fragments, or a composed local result without the cold hosted exact-SHA pair. |
| Performance | Cold exact-head admission completes within 90 minutes and a same-head warm retry within 30 minutes. With at least 90% valid hits, test-phase wall time falls by at least 70% versus the cold control; plan validation stays below 60 seconds. Report medians over three runs, not the best sample. |
| Resources | Peak test-worker count is structurally and behaviorally capped at two, the cache stays at or below 2 GiB/two generations, disk reserve remains green, and no child, lease, temporary root, or background pruner survives completion. |
| Quality | New production code has at least 85% branch-aware coverage with no touched file below 75%; warnings, package notices, lint, types, collection, target contracts, and the full exact-head gate are green before promotion. |

## Upstream and practitioner evidence

- Testmon documents dependency selection based on executed Python code, but its
  [official limitations][testmon-about] exclude static files and external
  services. Its open [explicit file dependency request][testmon-12] dates to
  2015. Current source also rejects simultaneous pytest-cov branch coverage.
  Gludd therefore cannot use Testmon to produce its mandatory branch-coverage
  evidence or infer complete non-Python inputs.
- `pytest-split`'s [official documentation][pytest-split] says it balances groups
  from a stored duration file and assumes an average for unknown tests. Issue
  [#100][pytest-split-100] reported tests duplicated across groups and omitted
  from all groups in 2024. A release integration would need its own exact
  collected-node reconciliation, so it is scheduling work rather than the
  chosen rerun cache.
- The [xdist architecture][xdist-how] states that every worker performs full
  collection. The controller-side timeout request [#220][xdist-220] has remained
  open since 2017, and the 2016 hang report [#61][xdist-61] records workers
  waiting before tests ran. Gludd already owns outer deadlines and process-group
  cleanup; more workers would widen that risk and resource envelope.
- Pytest's [cache guidance][pytest-cache] presents `--lf` and stepwise as rerun
  aids and recommends clearing cache in CI when isolation and correctness matter
  more than speed. Those selectors do not produce complete release receipts.
- Coverage.py officially supports [combining independent data
  files][coverage-combine], including data from different environments, and
  retains measurement contexts. This is the mature owner for recomputing
  Gludd's aggregate from exact batch fragments.
- Pants' [test documentation][pants-test] describes hermetic environments,
  per-file process caching, explicit environment inputs, batch compatibility,
  combined coverage, and a force-rerun mode. The 2020 user report
  [#10379][pants-10379] shows why a force-cold escape is operationally required;
  the 2021 snapshot/input report [#11622][pants-11622] shows that undeclared data
   files are a real migration boundary rather than a cache detail.
- Pants reports a configuration invalidation gap in
  [#19579][pants-19579] and dependency acquisition despite a cached test result
  in [#17958][pants-17958]. Those are separate reasons to bind configuration
  exactly and to avoid resource-saving claims during a shadow audit.
- Pytest's root-directory/node-ID reports [#11107][pytest-11107] and
  [#6399][pytest-6399] show why filenames cannot reconstruct a canonical node
  plan. Coverage.py's path-remapping report [#1840][coverage-1840] similarly
  keeps semantic coverage comparison inside an exact config/platform identity.
- Pants' LMDB exhaustion report [#5755][pants-5755] supports hard candidate,
  index-byte, generation, and total-storage ceilings instead of partial cache
  admission under pressure.
- Pants' cache-poisoning discussion [#115][pants-115] and remote-cache
  rate-limit report [#20133][pants-20133] support force-cold recovery and
  bounded offline verification. Cosign's trust-expiry report
  [#1273][cosign-1273] supports distinct unknown, revoked, and expired signer
  states instead of silent migration. Bazel's remote-cache credential report
  [#28598][bazel-28598] reinforces the no-remote-endpoint and no-path/key-output
  boundary.
- Bazel's normative [test encyclopedia][bazel-tests] requires hermetic tests to
  access only declared dependencies for reproducibility, release auditability,
  and resource isolation. Its [remote cache documentation][bazel-cache]
  supplies the content-addressed action/result model used by this decision.

[testmon-about]: https://www.testmon.org/
[testmon-12]: https://github.com/tarpas/pytest-testmon/issues/12
[pytest-split]: https://github.com/jerry-git/pytest-split
[pytest-split-100]: https://github.com/jerry-git/pytest-split/issues/100
[xdist-how]: https://pytest-xdist.readthedocs.io/en/stable/how-it-works.html
[xdist-220]: https://github.com/pytest-dev/pytest-xdist/issues/220
[xdist-61]: https://github.com/pytest-dev/pytest-xdist/issues/61
[pytest-cache]: https://docs.pytest.org/en/stable/how-to/cache.html
[coverage-combine]: https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html
[pants-test]: https://www.pantsbuild.org/stable/docs/python/goals/test
[pants-10379]: https://github.com/pantsbuild/pants/issues/10379
[pants-11622]: https://github.com/pantsbuild/pants/issues/11622
[pants-19579]: https://github.com/pantsbuild/pants/issues/19579
[pants-17958]: https://github.com/pantsbuild/pants/issues/17958
[pants-5755]: https://github.com/pantsbuild/pants/issues/5755
[pants-115]: https://github.com/pantsbuild/pants/issues/115
[pants-20133]: https://github.com/pantsbuild/pants/issues/20133
[cosign-1273]: https://github.com/sigstore/cosign/issues/1273
[bazel-28598]: https://github.com/bazelbuild/bazel/issues/28598
[pytest-11107]: https://github.com/pytest-dev/pytest/issues/11107
[pytest-6399]: https://github.com/pytest-dev/pytest/issues/6399
[coverage-1840]: https://github.com/nedbat/coveragepy/issues/1840
[bazel-tests]: https://bazel.build/reference/test-encyclopedia
[bazel-cache]: https://bazel.build/remote/caching
