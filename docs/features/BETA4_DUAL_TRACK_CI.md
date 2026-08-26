# Beta 4 Dual-Track CI Evidence

Status: implemented for the `v0.1.0-beta.4` candidate pipeline on 2026-08-26.

## Incident

The failed candidate at commit
`12a7052c1d09303bad8fab7bb3655b28dd1d0268` exposed a release-process defect.
Local checks and GitHub Actions were treated as sequential confidence signals,
while the hosted workflow still used a different, long-lived test runner. Run
`32805553808` consequently exposed worker retention, delayed resource warnings,
and whole-shard retries that the bounded local runner did not reproduce.

The defect was procedural as well as technical: a local green result was allowed
to influence release confidence before terminal hosted evidence existed for the
same immutable commit.

The next frozen candidate,
`a7f037c59fd71082452249bb5f6ee9efa8f50739`, proved why both lanes remain
mandatory. Hosted run `32816856494` found that the runner's nested `TMPDIR`
could exceed Linux's AF_UNIX pathname limit, while the local macOS checkout did
not. It also exposed concurrent terminal-attestation writers sharing one fixed
temporary filename. The runner now uses a compact project-labelled temp root
for multiprocessing sockets and a unique, fsynced same-directory temporary file
for every atomic attestation publish. Both resources are cleaned by their owner.
The first bounded local replay then stopped on an omitted pygame LGPL notice;
the shipped third-party notice and license audit now explicitly cover every
reviewed LGPL allowlist entry instead of treating the allowlist as documentation.
After that correction, batch 11 exposed a long-lived-process import boundary:
an `ApplyTier.CODE` instance created before a module reload did not satisfy an
identity comparison against the recreated enum class and lost its exclusive
source-mutation resource. The scheduler boundary now compares the stable enum
wire value, preserving serialization across reloads without retaining old
module objects. Batch 17 then exposed drift in two older enforcement-spec tests:
they assumed the obsolete plural `plugins` manifest key, duplicated a raw
subagent environment check instead of the shared guard, and assigned explanation
blocking to the stop plugin instead of the registered anti-essay plugin. The
tests now assert the canonical singular manifest key and shared guard. The
anti-essay runtime also treats `let me explain` as a status-summary phrase, with
an actual hook invocation proving that pending-work output is replaced rather
than relying only on a source-text assertion.

Batch 18 exposed a second orchestration-specific contract gap while the hosted
run for the earlier candidate was still active: the normal development push
suppressed repository hooks, and the force-push wrapper made its mandatory
CI-in-flight guard visible only through an implicit recursive dependency. The
normal path now preserves hooks, and the force path invokes the rate/active-run
guard and guarded push as explicit goals in one Make process. Structural tests
also follow the current singular plugin manifest, implementation directory, TDD
mapping, and distinct `GATE`/`GATE-LITE` progress marker contracts.

The exact-SHA replacement then proved that candidate invalidation must continue
past the first repaired boundary. Local batch 21 found a backend-specific HSM
exception leaking through its public facade, an in-progress gate snapshot being
compared as terminal evidence, missing session markers, and an ambiguous README
completion percentage. Candidate `e640d07daa473faf552108062919faebb7ae6c56`
and hosted run `32827145131` were abandoned rather than reused after those owner
repairs.

Candidate `f3403750c184558a2fcb31715756f9da5a472ffa` passed the repaired local
batches 18 and 21, then local batch 22 exposed stale security-test assumptions
about SQLAlchemy bound parameters, Jinja autoescape callbacks/entities, Unicode
confusables, and the streak handoff grace window. In parallel, hosted run
`32828457339` found a Linux/GitHub-only source-of-truth mismatch: README had been
edited without updating `docs/features.yml`, so hosted feature-claim generation
failed closed. The manifest now owns the unambiguous title, README is regenerated,
and feature verification uses an explicit local Ansible inventory without warning.

The next exact candidate,
`46df20c76d1f66d11e36d863668d1e5616981f1b`, proved that the paired loop must
continue beyond a repaired batch. Local batches 1 through 23 were green,
including the former batch-22 boundary, before batch 24 found eight cross-plugin
contract failures. Hosted run `32830158475` was cancelled immediately because
its SHA was already invalid. The owner repair moves subagent isolation ahead of
all other checks in the directives and task-tracking hooks, retains depth as the
single dispatch-only delegated-context exception, honors an explicit
`OPENCODE_SUBAGENT=0` over the marker fallback, and removes a stale nag baseline.
This is the intended invalidation behavior: neither a long green prefix nor an
active hosted run makes a failed SHA reusable.

Candidate `fc57c087f3c51c398d034231fb56faaa156ef834` passed the repaired batch
24, then batch 25 found an E2E `xfail` whose explanatory reason did not cite its
governing behavioral specification. Hosted run `32832065106` was cancelled, and
the marker now references the existing no-wait spec rather than weakening the
repository-wide marker audit. This demonstrates that candidate invalidation is
recursive: fixing the prior boundary only earns the right to discover the next
one on a new immutable SHA.

Candidate `5d4ddc7fa338c416ffc8ab46d1a4386455d0d79b` then passed that exact
batch-25 boundary before batch 26 found two independent ledger-ownership defects.
One structural guard flattened implementation paths to basenames after the
directives implementation moved under `impl/`, so it opened a nonexistent
top-level file instead of auditing the discovered source. The other defect was
real ledger drift: historical rows were checked while still describing
nonterminal work, and completed rows lacked the file or Make evidence now
required by the completion contract. Hosted run `32833535093` was cancelled.
The scanner now preserves plugin-relative paths, nonterminal ledger rows are
unchecked, and completed historical rows name their durable source or test
evidence. The guards remain strict.

Candidate `84e7c25a6381dcb1fe9bd00cd6c5e571df62e63e` passed local batches 1
through 26 while hosted run `32835393330` was active, then batch 27 exposed
Terraform/API drift that neither earlier local subsets nor a still-running hosted
lane could overrule. Azure stack metadata omitted descriptions and mirrored
outputs, a structural test still named the pre-namespace-migration vSphere
provider and misread valid multiline HCL, and the state router did not own empty
lock IDs, empty UNLOCK bodies, or URL-encoded workspace paths. The candidate was
invalidated and its hosted run cancelled immediately. The owner repair restores
the canonical `vmware/vsphere` contract, validates the Container App stack with a
real state-free Terraform E2E, and makes JSON/body/path handling explicit and
fail closed. This is the dual-track rule in practice: a green 26-batch prefix is
progress evidence, never release evidence.

The same repair pass exposed a local-validator lifecycle bug: Terraform's
`-backend=false` init mode still reused metadata from a prior `.terraform`
directory.
`tf-init-local` now gives every invocation a unique data directory beneath the
project resource namespace and removes that directory on success, failure, or
signal. Validation therefore cannot contact or depend on a Gludd daemon, and no
test harness starts one as compensation.

Replacement candidate `016ee5c97493f161d5709ab266015af8b70fd785` then passed
local batches 1 through 27, including 1,071 tests in the repaired Terraform
batch, while hosted run `32838352396` remained active. Batch 28 found eight stale
contract fixtures: the provider-cache test still expected the historical
`hashicorp/vsphere` namespace and an unused QEMU provider, a datetime test still
expected Python to reject ISO 8601 colonized offsets, and six timeout tests built
enabled metered OpenAI profiles without prices. The hosted run was cancelled as
soon as the local lane failed. The tests now follow the canonical provider graph,
Python's documented `%z` behavior, and Gludd's fail-closed nonzero metered-price
contract; the complete batch-28 slice passes 404 tests with warnings as errors.

Candidate `ef437dd51b7f350d701d6bfacc43b92f348fc74b` then ran locally while
hosted run `32840654442` exercised the same immutable commit. Local batch 29
found 14 failures, so the hosted run was cancelled immediately. The failures
exposed a process-global todo limiter shared by independent application
instances, missing bounded fallback when the todo database connection failed,
non-atomic ungrouped token-bucket admission, a private parser error outside its
documented `ValueError` contract, and several mathematically stale token-bucket
assertions. The repaired 16-file batch passes 414 tests with warnings as errors.
The focused runtime slice passes 344 tests at 93% aggregate branch coverage;
each of the three changed production files is at least 90%. No harness cleanup
or daemon startup compensates for these application-owned fixes.

Replacement candidate `5758f849c205076db07ea4a169a27aa24b14a79d`
proved the invalidation rule again while hosted run `32843128782` was active.
Local batches 1 through 29 passed, including the repaired 414-test boundary,
before batch 30 found that the multitask enforcement plugin had lost its
operator-facing `MUST DISPATCH` marker. The hosted run was cancelled
immediately. The marker is part of the fail-closed observability contract: an
operator must be able to distinguish a mandatory dispatch denial from advisory
guidance. The owning plugin now restores that marker, and both structural and
runtime hook suites validate the behavior before a new candidate is created.

Candidate `012faf7a6d9e627006bb046b3ef42d8b4afb6e8c` passed the repaired
batch 30, then batch 31 found a TUI parity guard tied to the obsolete
`if current_view == ...` implementation syntax. The application already
rendered every required view through the canonical `match current_view`
dispatch, so changing production back to chained conditionals would have hidden
the test defect. Hosted run `32844962204` was cancelled. The guard now parses
the function AST and requires every literal view case to call its exact table
builder; the focused TUI surface passes 114 tests with warnings as errors.

Candidate `6a6bf83b82a1d8d14c130e3080afc01ade9e7e6e` passed local batches 1
through 27 while hosted run `32846967040` exercised the same commit. Batch
28 then exposed a scheduler-dependent duration test: a real 50 ms sleep sat
exactly on the anomaly floor after subtracting a 1 ms baseline, so host load
could move the result across the boundary. The hosted run was cancelled and
the candidate invalidated. `DurationTracker` now accepts an injected monotonic
clock while retaining `time.monotonic` as its production default. The tests use
an exact 80 ms interval, leave all anomaly thresholds unchanged, and pass 45
tests with warnings as errors at 98% branch coverage.

Candidate `b63528a3dfe63ed9c65ed55c88aa50582df6caec` passed local batches 1
through 33 while hosted run `32848939596` exercised the same commit. Batch
34 found that UUIDv7 values used independent random suffixes and therefore
were not ordered within one millisecond. It also found a ULID invalid-character
fixture whose 23-character length correctly failed before character validation.
The hosted run was cancelled immediately. UUIDv7 now uses RFC 9562 Method 1's
42-bit counter followed by a 32-bit random tail, matching the mature CPython
layout while retaining Gludd's explicit-timestamp API. The ULID fixture is now
exactly 26 characters and reaches the intended illegal-character branch. The
focused file passes 31 tests with warnings as errors at 93% branch coverage.

Candidate `96f617335db06ce71c42bf61b2ef62fc1c37bc40` then passed local
batches 1 through 35, including the repaired UUIDv7 batch, while hosted run
`32851257635` exercised the same commit. Batch 36 exposed a circular release
contract: ordinary branch validation required the not-yet-created beta4 tag,
even though tags are deliberately created only after the candidate is green.
The hosted run was cancelled immediately. Branch candidates now validate the
already-canonical version, changelog, README, and release-note inputs without
requiring an uncut tag. A GitHub tag-triggered run, or an explicit local
`GLUDD_REQUIRE_RELEASE_TAG=1` verification, still requires the exact current
tag and requires it to be the newest semantic-version tag. This preserves the
post-publication invariant without making pre-publication validation impossible.

Replacement candidate `f71a84dced1febed7c40fb8e5027d92194dee102` passed the
repaired batch 36, then batch 37 exposed a second AF_UNIX path-budget defect.
The runner already assigned every batch a compact, owned `TMPDIR`, but pytest's
`--basetemp` still pointed into the much longer durable evidence workspace.
Firecracker's real Unix-socket tests consequently exceeded Darwin's `sun_path`
limit only under the complete runner. Hosted run `32853679064` was cancelled.
Pytest's ephemeral tree now shares the compact per-batch root, while coverage and
attestations remain in the durable external evidence workspace. The runner still
removes both resources through their respective owners on every terminal path.

Candidate `3cbf3ef4982007555e46a7d51ca2f3e409f7e6d5` proved that a generic
multiprocessing socket check was still insufficient. Local batches 1 through 36
passed while hosted run `32856380941` exercised the same commit, but batch 37
measured a 114-byte resolved Firecracker socket path after pytest added
`popen-gw0/<test-name>` beneath the compact root. Darwin permits only 104 bytes,
so four real Unix-socket tests failed and the hosted run was cancelled. The
runner now reserves the complete xdist, test-name, and endpoint suffix budget in
its contract and uses a shorter project-and-digest-owned root. Cleanup remains
restricted to the exact generated-name grammar; no test or external process
performs compensating cleanup.

Replacement candidate `d43441adae3bf28a4655aac734151f2243d899db` passed the
repaired Firecracker boundary, then batch 38 exposed two watchdog durability
gaps while hosted run `32859009012` exercised the same SHA. A corrupt kill audit
was logged but not recovered, and its direct rewrite could truncate prior audit
evidence. The audit owner now recovers corrupt input as a new bounded list and
publishes through a flushed, fsynced, same-directory temporary file plus atomic
replacement; replacement failure preserves the prior file and removes the
temporary artifact. The candidate was invalidated and its hosted run cancelled.

The same batch exposed a Python 3.14 import-contract violation in a test helper:
it executed a dataclass-bearing file without first registering the module under
its spec name. Python's official
[programmatic import recipe](https://docs.python.org/3/library/importlib.html#approximating-importlib-import-module)
registers `sys.modules[name]` before `exec_module`. A 2025
[CPython practitioner report](https://github.com/python/cpython/issues/140704)
records the same `dataclasses` `NoneType.__dict__` failure when a module is not
available under `cls.__module__`. The helper now uses a unique registered name,
so it neither depends on another test's import order nor changes production
dataclass behavior.

Candidate `9c9a4fcc31f8776ef9762fc8fd0588bc03f6f35a` passed local batches 1
through 38 while hosted run `32862553727` exercised the same immutable SHA.
Batch 39 then exposed four wavelet defects and seventeen stale or missing
watchdog/enforcement contracts, so the hosted run was cancelled immediately.
The wavelet owner now composes PyWavelets' supported single-level periodization
operations and constructs synthesis columns through `idwt`, eliminating the
hand-maintained phase offset and warning suppression. Tests now express the
quadrature-mirror filter relation, idempotent watchdog generation, and
load-adjusted wave width rather than obsolete behavior. The multitask plugin's
result-arrival refill reminder is restored with a real runtime test and a bounded,
validated interval override. No candidate evidence from batches 1 through 38 is
reused for the replacement SHA.

Candidate `1f1af024ff9c0d9ada0a827f32d40a8de76913e1` then passed local
batches 1 through 39 while hosted run `32865912548` exercised the same
immutable SHA. Batch 40 exposed nine WebMCP self-description failures, so the
hosted run was cancelled immediately and the candidate invalidated. The owner
repair synchronizes the exact daemon public-path allowlist, documents the
public OpenAPI and human-todo endpoints, preserves method-aware authentication
for same-path GET/POST pairs, and supplies the missing POST and facts response
schemas. The test contract now calls the daemon's authoritative
`is_public_path(method, path)` predicate instead of incorrectly treating a path
as public for every HTTP method. The repaired live and structural WebMCP surface
passes 212 tests with warnings as errors and the changed production module has
100% branch coverage.

Candidate `fc71b3222056749532a71fc9bcd7f5212d7f8ed4` exposed an artifact-
ownership defect after hosted run `32882197592`, job `97916977435`, passed all
450 unit-1a2 tests. Batch 6 durably saved an 815,104-byte
`.coverage.unit-1a2.batch-006` fragment, but the job asked coverage.py to combine
into `.coverage.unit-1a2-3.11`. Coverage.py discovers only dotted suffixes of the
configured output basename, so the valid fragment was invisible and aggregation
failed with “No data to combine.” The owner now inventories non-empty regular
fragments, copies each to a bounded alias with the exact aggregate basename,
streams source/destination/size evidence, and always removes those aliases.
Successful hosted attestations bind the final artifact name, byte count, SHA-256,
and Python major/minor; absent, malformed, symlinked, empty, or wrong-interpreter
evidence fails closed.

The integrated candidate at `e4bf63b16b0fd3dda87245cb1346cd652ccbf039`
then exposed the missing local producer. The verifier required one local terminal
attestation covering all eight canonical shards at
`resource_root/ci-shards/attestation.json`, but the only public local target forced
one shard and wrote `<shard>-attestation.json`. Operators therefore could not
produce the evidence that the release precondition consumed. The new
`make test-ci-dual-track-local` target delegates once to the existing serial
runner, omits a shard override so its canonical registry remains authoritative,
runs isolated tests plus aggregate coverage, and publishes the expected terminal
artifact. Its validate-only mode resolves the same one-worker, 16-file batches
and prints the complete plan without inspecting release identity, running pytest,
or writing evidence; an empty pytest selector is consequently safe for contract
checks.

## The rule

One clean commit is frozen as the candidate. Local and GitHub-hosted tests start
from that exact SHA and run concurrently through
`scripts/run_ci_shards_serial.py`. A candidate is not release-ready unless both
lanes publish terminal, successful, exact-SHA attestations.

The following evidence is invalid:

- an attestation for a different or abbreviated commit;
- an attestation produced from a dirty checkout;
- a queued, cancelled, skipped, timed-out, or incomplete run;
- a successful retry that used changed workflow code from another commit;
- coverage without the corresponding terminal shard attestation; or
- a locally compensated cleanup that hides an application-owned resource leak.

Every bounded shard batch uses one worker, disables worker restarts, has a unique
base temporary directory, emits heartbeats, and terminates its owned process group
with bounded TERM-to-KILL cleanup. Molecule shards execute each assigned scenario
once and preserve their individual logs; a completed scenario is never replayed
as a substitute for fixing a failed one.

`make require-dual-track-green SHA=<full-sha>` is the release precondition. It
first requires a successful GitHub workflow for that exact SHA, downloads only
that run's `coverage-*` artifacts, validates all eight hosted shard attestations,
and compares them with the local all-shard attestation. Both `release-dry-run` and
`release-cut` invoke this target. The verifier rejects missing, duplicate,
malformed, dirty, failed, wrong-lane, or wrong-SHA evidence.

`make test-ci-dual-track-local` is the canonical local producer for that
precondition. `test-ci-shard` remains a focused diagnostic and its per-shard
attestation is not release evidence. The producer takes no shard list: the
runner's `DEFAULT_SHARDS` registry, bounded batch planner, single xdist worker,
heartbeats, cleanup, aggregate coverage, and terminal writer are the sole source
of execution truth. `DUAL_TRACK_LOCAL_VALIDATE_ONLY=1` is the read-only Make
contract and never creates a successful attestation.

Schema 3 makes that comparison semantic instead of trusting matching lane names.
Every terminal attestation records, for each shard, its deterministic ordered
test paths, path count, and SHA-256 of the canonical JSON path list. It also
records and hashes the release execution policy: warnings are errors, xdist and
process limits are one, worker restarts are disabled, distribution is
`loadgroup`, and branch-aware greenlet coverage is enabled. The verifier
independently recomputes every digest, requires that exact policy, and pairs each
hosted shard fingerprint with the same shard in the local all-shard attestation.
Legacy schemas, internally inconsistent digests, extra or missing plans, a
weakened policy shared by both lanes, and two valid but different plans all fail
closed. A green status and matching commit SHA alone can never advance beta 4.

`make ci-run-summary RUN=<numeric-id>` provides the operator view for a single
immutable hosted run. It asks `gh run view` for that exact database ID and a fixed
JSON field set, verifies the returned ID and full head SHA, and exits successfully
only when the run and every job are terminal and successful. Empty jobs, pending
work, malformed responses, mismatched identity, and GitHub API errors remain
visible and fail closed. `CI_RUN_SUMMARY_VALIDATE_ONLY=1` validates arguments and
the Make contract without network access or mutable state.

## Zero-downtime development

Candidate validation does not mutate the running Gludd service or the external
local-model endpoint. Mutable test resources live under the project namespace
returned by `scripts/resource_arbiter.py`; each shard gets an additional unique
batch namespace. The runner removes only the workspaces and coverage fragments it
owns. It does not stop externally owned services, including a user-started model
server.

Coverage transfer is also owner-bounded. The durable fragment directory remains
outside each ephemeral pytest workspace; aggregate-prefix aliases exist only for
the combine call and are removed in `finally` on success, failure, or cancellation.
The original fragments are retained for diagnosis, and the terminal attestation
is published only after the aggregate has been validated and hashed. This is a
zero-downtime evidence change: it does not restart or mutate Gludd, its database,
or an externally owned model service.

The canonical local producer uses the same project-scoped resource root and
per-batch owner cleanup. It does not start or stop Gludd or the external model.
Validate-only mode creates no batch workspace, process, coverage fragment, or
attestation, so operators can inspect the exact plan while a deployed service
continues serving traffic.

Compact socket-safe temporary roots have an explicit cancellation boundary. While
an owned root is being removed, the runner defers `SIGINT` and `SIGTERM`, restores
the prior handlers immediately afterward, and converts the first deferred signal
to the conventional `128 + signal` return code. A repeated cleanup observes an
already-absent root as success, while ownership mismatches and filesystem errors
still fail closed. This keeps Ctrl-C bounded through child TERM-to-KILL and owner
cleanup without leaking resources or replacing the terminal shard result with a
cleanup traceback.

Hosted shard failure collection always writes one non-hidden, shard-and-Python
named diagnostic log while streaming the same bounded host evidence to the job.
Each probe reports its own unavailable state without aborting later probes, and
the step verifies that the log is non-empty before upload. This guarantees a
downloadable artifact even when pytest and coverage produced no files; hidden
coverage data remains an optional companion rather than the artifact's existence
condition. Rollback removes only this immutable diagnostic-file contract and does
not affect the tested service or any running candidate.

Feature work may continue on another branch while a candidate is tested, but the
candidate SHA, workflow definition, and evidence set remain immutable. A failed
candidate is abandoned and a new commit starts both lanes again. This gives
development continuity without weakening release evidence.

## Rollback and recovery

No tag or release is created until dual-track verification succeeds. On failure,
retain the hosted logs and attestations, remove only the candidate's namespaced
temporary resources, fix the owner, and create a new commit. Do not rerun an old
commit after changing the workflow on a different branch: GitHub reruns retain the
original `GITHUB_SHA` and `GITHUB_REF`.

If a worker dies or stops producing output, the runner fails closed instead of
restarting it. This avoids xdist cases where completed work is requeued or the
controller waits indefinitely on a dead worker. Coverage remains gated at 85%
aggregate and at least 75% per measured file.

Rollback removes the prefix-transfer and hosted coverage-attestation fields as
one unit, then starts a new immutable candidate; existing evidence must not be
reinterpreted. No running service is rolled back. Retain the original fragments
and diagnostic log until the replacement candidate is terminal so recovery can
distinguish missing collection from failed transfer.

Rollback removes the public producer, its read-only validation flag, and contract
entry together. Existing signed evidence is retained, but no replacement release
candidate may proceed until an equivalent canonical all-shard producer is
restored; composing eight focused shard artifacts is not a valid fallback.

The schema-3 plan and execution-policy fields are one release contract. Rollback
must remove their producer, verifier, and tests together and invalidate the
candidate; schema-2 evidence is retained only for diagnosis and is never promoted.
This is control-plane rollback only: it does not stop, restart, or mutate a
running Gludd service, database, or externally owned model process.

## Upstream and practitioner evidence

Reviewed 2026-08-25:

- [pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/latest/distribution.html),
  reviewed 2026-08-25, defines explicit worker counts and zero as the supported
  way to disable crashed-worker restarts. The local producer inherits the
  runner's fixed one-worker/zero-restart policy instead of duplicating it in Make.
- [pytest-xdist issue 18](https://github.com/pytest-dev/pytest-xdist/issues/18),
  opened 2015-12-02 and reviewed 2026-08-25, preserves long-lived practitioner
  evidence that distributing tests can recreate fixtures on multiple workers.
  Gludd bounds cumulative state with fresh batches while keeping each batch on
  exactly one owned worker.
- [Coverage.py combine documentation](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html)
  defines discovery as the configured data-file basename plus a dotted suffix,
  documents cross-Python aggregation, and recommends path remapping or relative
  files when collection and reporting locations differ.
- [Coverage.py API documentation](https://coverage.readthedocs.io/en/latest/api_coverage.html)
  specifies strict combination as the mature fail-closed mechanism when no
  similarly named input exists.
- [coverage.py issue 1752](https://github.com/nedbat/coveragepy/issues/1752),
  opened 2024-02-22 and reviewed 2026-08-25, records practitioner CI evidence that
  platform/path drift can duplicate or omit runs during cross-environment combine.
- [coverage.py issue 1837](https://github.com/coveragepy/coveragepy/issues/1837),
  opened in 2024 and reviewed 2026-08-25, records the long-lived distinction
  between ephemeral runtime paths and stable reporting paths. Gludd therefore
  transfers the owned data artifact and preserves its stable source identity
  instead of depending on a pytest workspace lifetime.
- [GitHub documentation: rerunning workflows and jobs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs?tool=cli)
  confirms reruns use the original commit and ref.
- [GitHub Community discussion 27083](https://github.com/orgs/community/discussions/27083)
  documents the practical consequence that a rerun uses the workflow from the
  original SHA rather than a later fix.
- [GitHub Community discussion 17854](https://github.com/orgs/community/discussions/17854)
  reports artifact replacement and visibility surprises across reruns, supporting
  immutable, SHA-bound terminal evidence instead of artifact-name trust alone.
  Schema 3 additionally validates the downloaded contents and pairs each hosted
  test-plan digest to its local counterpart instead of treating the artifact name
  or green conclusion as proof of equivalent work.
- [pytest-xdist issue 1323](https://github.com/pytest-dev/pytest-xdist/issues/1323)
  documents hangs and completed-work requeue behavior after worker restart.
- [pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313)
  documents controller hangs after a worker dies with retained pipes.
- [pytest-xdist issue 1278](https://github.com/pytest-dev/pytest-xdist/issues/1278)
  documents missed nonzero worker exits, supporting fail-closed controller parsing.
- [Git worktree documentation](https://git-scm.com/docs/git-worktree) describes
  locked worktree ownership and pruning behavior used to preserve active candidate
  state.
