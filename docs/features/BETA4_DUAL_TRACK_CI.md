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

Candidate `0b5a685a02f100e6ed292d25dca64d940ad41b77` and hosted run
`32946737866` then exposed one remaining duplicate shard-plan reader. The
workflow and canonical runner already shared one registry, but
`test_shard_split_balance.py` still reconstructed paths from the workflow's
isolated-test `matrix.include` entry and mistook that exception for the complete
plan. Hosted `unit-3b` failed two exact assertions; the paired local run and every
remaining hosted job were cancelled immediately. The contract now validates
workflow membership separately from canonical registry paths. It does not add a
cleanup task, retry, or copied matrix field. Rollback is the single contract
commit; zero-downtime behavior is candidate invalidation before any tag or
deployment, and the runner owns all interrupted child and temporary resources.

Candidate `75975e86c7da6bf29993b8da715b249d3ad9011b` and hosted run
`32948517640` then exposed a scheduler-dependent temporary-root test. The test
gave a newly created root only 100 milliseconds of wall-clock life even though
creation deliberately flushes and fsyncs its ownership manifest before return.
Hosted storage latency consumed that interval, so the production reaper
correctly classified the root as expired while the same test passed on the local
filesystem. The regression now gives old, fresh, and observation events exact
wall-clock values and removes the real sleep. Production expiry, durable
manifests, and fail-closed cleanup are unchanged. The paired local and hosted
lanes were cancelled as soon as the immutable candidate failed; rollback is the
single test-contract commit, and no retry or compensating cleanup task exists.

Run `32931575307` at commit
`9ef2488da54cc2da1c2778f750270e6e72ddb8d5` exposed the inverse ordering
hazard: three test-created in-memory SQLite engines discarded their ownership
handle, then their `aiosqlite` connections were garbage-collected during a later
daemon lifecycle test on hosted Python 3.11. The daemon had already disposed its
own engine correctly. The test database acquisition boundary now yields its
session factory from one async owner scope and disposes that exact engine in a
`finally` block. The complete 16-file hosted batch is the regression boundary,
so a delayed warning cannot be hidden by an isolated passing test. There is no
release-time cleanup task or garbage-collection fallback: acquisition and
teardown remain paired, cleanup failure remains observable, and rollback is the
single test-support commit.

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

Candidate `cf9fcd3d7154e2a03cf3012db74ca92b51dde796` exposed a peer-lane
cancellation defect after hosted run `32886106353` failed. Cancelling the local
producer interrupted its active child and cleaned that batch, but the parent
runner then advanced into the next shard; repeated interrupts were required to
stop the complete plan. A child return code of `128 + SIGINT` or
`128 + SIGTERM` now cancels the entire canonical plan, prevents later shards and
coverage aggregation from starting, preserves bounded owner cleanup, and returns
the signal-derived status. This keeps candidate invalidation atomic across both
lanes instead of treating cancellation as a per-batch failure.

The same candidate's hosted jobs exposed two GitHub-only evidence assumptions.
Run `32886106353`, job `97928481080`, could not validate the recorded
`SESSION.md` head because the shard checkout retained only GitHub Checkout's
default single commit. Job `97928481003` configured `GLUDD_RESOURCE_ROOT` as a
project leaf even though the resource arbiter owns appending the project
namespace, so hosted resource identity differed from local execution. Test-shard
checkouts now fetch complete history, and the workflow supplies only the
runner-temporary `gludd-resources` container root. The existing arbiter remains
the single owner of project namespace construction on both platforms.

Replacement candidate `500f415354848f7013bf54794516c4d9ce56d098` proved the
paired rule again. Hosted run `32889131313`, job `97938327981`, timed out in
`test_broadcast_and_register_concurrent_no_dict_mutation_error` after a test
thread restored the module-global `httpx.post` while another thread was still
broadcasting to synthetic worker addresses. That second thread consequently
performed real DNS attempts for test-only hosts. The local peer stopped with
status 130, cleaned its active batch, and did not start later batches or shards.
`WorkerBroadcaster` now binds one POST transport per instance under its existing
registry lock and reuses that stable callable for reload and model-sync paths.
The default remains HTTPX, and the security boundary still enforces HTTPS,
send-time SSRF validation, no redirects, certificate verification, the bounded
timeout, and PSK headers. Tests inject the instance seam rather than mutating a
shared transport during concurrent work.

Candidate `96283dbd571c47774b1eddf74fde3975a9c146d2` then exposed a
cross-platform confinement gap in hosted run `32891975584`, job
`97947440679`. Linux preserved Windows backslashes, dot-only segments, and
percent-encoded separators as literal filename characters, so three adversarial
paths remained lexically below the workspace even though another platform or
downstream decoder could reinterpret them as traversal. The paired local lane
was cancelled during `unit-1b` batch 22, reaped its worker, and started no later
batches or shards. `ExecutionEngine` now repeatedly percent-decodes within a
small fixed bound, normalizes Windows separators, rejects drive/UNC roots, NUL,
dot-only traversal segments, and excessive encoding, then applies the existing
realpath/commonpath jail. Benign portable paths retain the same workspace owner
and are resolved only after canonicalization.

Candidate `c06ff4144a6333f882964fbbb6018d231f83fe72` then reached sixteen
successful hosted jobs before run `32894848924`, job `97956809609`, failed the
Linux binary smoke. The build itself completed, but the normalized x86_64
PyInstaller warning graph moved from the reviewed digest `2c13f658...` to the
unknown digest `837c969e...`. The local peer was cancelled during `unit-1b`
batch 22, returned status 130, removed its owned batch root, and started no later
batch or shard. The unknown graph is not approved by digest alone. Audit failure
now emits a bounded first-50 normalized edge set and the Molecule failure
artifact retains the complete raw `warn-gludd.txt` for review.

Candidate `60c85252c968d973fa17c708b7c135a5127ad1fa` and hosted run
`32899884121`, job `97972670602`, independently reproduced the same
`837c969e...` graph. The retrieved raw artifact contains 1,265 normalized
third-party/transitive edges; the complete fail-closed audit reported no new
actionable Gludd-owned edge before rejecting only the unknown digest. That
second exact-SHA reproduction is the review evidence for admitting the digest
as an x86_64 alternate. Its local peer independently failed on the missing
`GLUDD_CANDIDATE_SHA` configuration-reference contract, cancelled shard
`unit-1b` with status 130, reaped its worker, and started no later shard.

The same incident exposed an operator tooling gap. The repository could list
run artifacts but could not retrieve one without bypassing the Make-only
boundary, and `gh run download` provides no durable transfer progress. The
run-bound `ci-artifact-download` target verifies exactly one live named artifact,
confines it beneath `.gate-logs/ci-artifacts/run-<id>`, downloads into an owned
same-directory temporary root with heartbeats, forwards cancellation to the
child, and atomically publishes only after success. Existing destinations are
never overwritten.

The cleanup replay moved the local environment to Python 3.14 and exposed an
ansible-lint schema-refresh `ResourceWarning` while the lint itself returned
success. The release lint now uses ansible-lint's documented schema-update skip
and promotes every Python warning to an error. This keeps the full local
collection and syntax checks while removing the network/cache owner from the
lint path; it is not a warning filter or a reduced rule profile.

Candidate `9324889fc9049ea66643805710b3fc7d8830898c` then demonstrated two
distinct retry boundaries. Local validation passed two complete shards while
hosted run `32902659192` reached its platform fan-out. GitHub-hosted Termux and
game-building runners in different Azure regions both failed during `Set up
job`: GitHub's internal action-download hostname did not resolve after all three
platform retries. Because no repository step ran, the immutable candidate was
eligible for a paired infrastructure retry rather than a code change. Both
peers were still cancelled immediately to preserve resource and evidence
pairing.

That retry exposed an orchestration defect: `ci-trigger-committed-head` treated
the completed cancelled run as reusable and claimed dispatch success, leaving a
new local producer without a live hosted peer. Exact-SHA signaling now reuses
only active or successful runs. A completed non-success conclusion is explicit
terminal evidence that authorizes one replacement dispatch and refreshes its
durable marker; a merely delayed run still cannot be duplicated. This behavior
matches the long-lived practitioner reports about missing and duplicate
workflow signals cited below while retaining one immutable candidate SHA.

Candidate `a8969daba137e11c8a217826509e4811bad0fcbf` reached eighteen
successful hosted jobs before run `32905890724` exposed two Python 3.11-only
contract failures. Job `97990926998` inherited the shard runner's compact
`/tmp/g…` pytest base and then correctly had that non-project namespace refused
by the `search` target. The compact root now begins with `gludd-` while retaining
the four-character digest and AF_UNIX path budget; teardown still accepts only
the exact generated grammar. Job `97990927020` assumed that
`datetime.utcnow()` emits a deprecation warning on Python 3.11 even though the
deprecation begins in Python 3.12. The test now proves the naive result on every
supported interpreter and requires the warning only from 3.12 onward. The local
peer independently found a stale release-cut assertion for the older
`require-ci-green` guard; the structural contract now requires the stronger
exact-SHA `require-dual-track-green` prerequisite already used by production.
Both peers were cancelled and the candidate invalidated before repair.

The first clean-SHA gate on follow-up commit `c1df1c5c55ee86f2344c447ef5239425b5b94737`
then exposed four further contract drifts after 406 neighboring router tests
passed. The Make audit treated the comma-bearing tail of a nested
`$(if $(filter ...))` expansion as a literal prerequisite, two release tests
still expected the weaker hosted-only guard, and router registration expected
`{stack_name}` instead of FastAPI's deployed `{stack_name:path}` converter.
The gate was cancelled immediately at that first red boundary. The audit now
consumes balanced nested Make expansions, while the release and router tests
pin the deployed interfaces without changing production behavior.

Candidate `c9657db8c194ca5da2e32ef13c7003b6407d5e32` demonstrated the
hosted-runner timing boundary directly. Local Python 3.11 and the complete
macOS prefix passed, while hosted run `32925158696`, job `98047439833`, failed
`unit-1a2` because time spent *inside* the floor plugin's dispatch hook was
counted as time between tool calls. Reading a larger pending-work ledger and
publishing dispatch preflight evidence crossed the deliberately short test
boundary on hosted Linux, so the next edit was misclassified as a new message
with an undersized dispatch wave. The runner had ample disk and memory; this was
not load shedding or an orphaned process. The first repair moved completion
timestamp publication into `finally`; rebuilding the generated hot module then
showed that pre-hook owner work could still cross the boundary before the entry
timestamp was sampled. The final hook captures true entry time in its default
argument and publishes completion time in `finally`. A no-sleep regression makes
dispatch bookkeeping nontrivial and proves that the immediately following call
remains in the same message. When hosted failed, the local peer and every
remaining hosted job were cancelled before a replacement candidate was formed.

Candidate `aeb8000b3e47f8747ae58faef4f6e7a01b5ba6bd` then proved that
dual tracking must also bind application clocks. Hosted run `32926849125`
completed eighteen jobs successfully before job `98052237385` reached the
00:00-06:00 off-peak window on its UTC Linux host. Two scheduler tests expected
deferral while production correctly observed that off-peak was already active,
so both returned `None`. The local peer was cancelled immediately and every
remaining hosted job was cancelled before repair. `OffPeakScheduler` now owns an
injectable wall clock and samples it once for the off-peak decision, ticket
timestamp, and next-window calculation. Tests supply a stable local noon rather
than depending on the runner's time zone or wall-clock hour. Rollback is one
constructor parameter and three call sites; no persisted ticket schema, pricing
rule, daemon resource, or external service changes. The zero-downtime boundary
is unchanged because existing callers default to `time.time`, while new and
in-flight schedulers remain process-local.

Candidate `c73db71802ee39aaf92e15a20328043742d62da8` verified the
optional-media boundary after eighteen hosted jobs passed. Hosted run
`32929544501`, job `98060008995`, ran `unit-3b` on the base Python 3.11
environment where `yt-dlp` is intentionally absent. Four video-reference tests
failed because invalid clip bounds loaded the optional adapter before validating
their inputs, while an injected acquisition adapter was followed by a second,
hidden `yt-dlp` version lookup. The remaining hosted jobs and the local peer were
cancelled immediately. The adapter now validates bounds before dependency
resolution and publishes its version through the existing sanitized metadata
seam; injected adapters record `unknown` instead of causing an unrelated import.
The exact Python 3.11 file and its adjacent deep surface then passed 32/32 and
139/139 applicable tests respectively, with seven dependency-gated skips; the
changed production file has 94% branch coverage. This
keeps zero-downtime behavior intact: cache objects and provenance sidecars are
still atomically promoted only after validation, no network call occurs for
invalid inputs, and rollback is confined to the adapter ordering and one
provenance field source. No service, worker, or persistent process is added.

Candidate `0519c8d2a8d612692a5efb836b29f54e80681306` paired local canonical
shards with hosted run `32941994870`. Every platform build, both Python gates,
all four Molecule partitions, and six hosted shards passed before job
`98096044834` failed the `other` shard. The failure was a structural ownership
regression: workflow YAML still duplicated every shard's `testpaths` and
`exclude` values, while the hosted and local execution step had already moved
to `scripts/ci_named_shard_files.py`. The copied matrix fields were inert, and
an older security assertion still required the removed shell filter. Both lanes
were cancelled immediately. The workflow now carries shard labels only, the
Python registry exclusively owns expansion and exclusion, and structural tests
fail if workflow-local plan data or the old adaptive runner returns. Rollback is
one workflow/test/docs commit; no daemon, external service, persistent state, or
test-harness cleanup is added. GitHub Community discussion
[141795](https://github.com/orgs/community/discussions/141795) records a
practitioner encountering similarly non-obvious `matrix.include` behavior, and
discussion [26284](https://github.com/orgs/community/discussions/26284) records
the long-lived user concern that repeated matrices are error-prone when one copy
changes. Gludd therefore keeps only workflow-specific isolated-suite metadata
in `include` and binds the actual plan through schema-3 attestation digests.

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
- coverage without the corresponding terminal shard attestation;
- hosted coverage whose artifact, size, digest, or Python identity is unbound; or
- a locally compensated cleanup that hides an application-owned resource leak.

Every bounded shard batch contains at most 16 files, uses one worker, disables
worker restarts, has a unique base temporary directory, emits heartbeats, and
terminates its owned process group with bounded TERM-to-KILL cleanup. The 16-file
process lifetime prevents cumulative native-library and multiprocessing state
from crossing hundreds of unrelated files. Molecule shards execute each assigned
scenario once and preserve their individual logs; a completed scenario is never
replayed as a substitute for fixing a failed one.

`make require-dual-track-green SHA=<full-sha>` is the release precondition. It
first requires a successful GitHub workflow for that exact SHA, downloads only
that run's `coverage-*` artifacts, validates all eight hosted shard attestations,
and compares them with the local all-shard attestation. Both `release-dry-run` and
`release-cut` invoke this target. The verifier rejects missing, duplicate,
malformed, dirty, failed, wrong-lane, or wrong-SHA evidence.

`make test-ci-dual-track-local` is the canonical local producer for that
precondition. `test-ci-shard` remains a focused diagnostic and its per-shard
attestation is not release evidence. The producer takes no shard list: the
runner's `DEFAULT_SHARDS` registry, bounded batch planner, one owned pytest
process per batch,
heartbeats, cleanup, aggregate coverage, and terminal writer are the sole source
of execution truth. `DUAL_TRACK_LOCAL_VALIDATE_ONLY=1` is the read-only Make
contract and never creates a successful attestation.

Executable mode also runs the repository's locked `node-deps-sync` contract
before starting Python shards. This mirrors the hosted workflow's Node setup and
prevents a clean worktree from reaching hot-module tests without `esbuild`.
Validate-only mode deliberately skips installation, preserving its no-network,
no-write planning contract. This boundary was pinned after the 2026-08-27 local
exact-SHA run reached `unit-2` and failed five hot-module tests solely because
the canonical local producer had not provisioned the lockfile-backed Node plane.

Schema 3 makes that comparison semantic instead of trusting matching lane names.
Every terminal attestation records, for each shard, its deterministic ordered
test paths, path count, and SHA-256 of the canonical JSON path list. It also
records and hashes the release execution policy: warnings are errors, the
process limit is one, nested xdist is disabled, distribution is `none`, and
branch-aware greenlet coverage is enabled. The verifier
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

Molecule shard 1 separately verifies that the raw Linux PyInstaller warning file
is non-empty before upload. This explicit check is necessary because
`if-no-files-found: error` applies to the path set as a whole: an always-present
Molecule log must not mask a missing second diagnostic. Download temporaries are
removed on success, failure, or signal; the immutable retrieved artifact remains
operator-owned evidence until the candidate is replaced. Removing the diagnostic
step or download target is a zero-downtime rollback, but no warning digest may be
changed without a new raw graph and exact-SHA hosted replay.

Feature work may continue on another branch while a candidate is tested, but the
candidate SHA, workflow definition, and evidence set remain immutable. A failed
candidate is abandoned and a new commit starts both lanes again. This gives
development continuity without weakening release evidence.

## Hosted coverage dotfile artifact contract

Candidate `99562640dc0e582b8e369651c5dece65b1f28882` proved a
hosted-only producer defect: all eight test shards passed and uploaded their
marker and attestation, but the coverage consumer found zero `.coverage.*`
databases. The databases were named explicitly in the artifact path, yet the
upload action excluded them because they are dotfiles.

GitHub's upload-artifact documentation says hidden files are ignored by
default and require `include-hidden-files: true`. The upstream announcement
records that this became the default for security, and practitioners report
that even an explicitly named dotfile is omitted without the opt-in. One user
spent roughly a day and twelve long workflow retries discovering the behavior,
which is why Gludd pins it mechanically rather than relying on operator memory.

- [upload-artifact hidden-file documentation](https://github.com/actions/upload-artifact#uploading-hidden-files)
- [upstream hidden-file default announcement](https://github.com/actions/upload-artifact/issues/602)
- [practitioner report: explicitly named hidden files](https://github.com/actions/upload-artifact/issues/614)
- [practitioner report: missing diagnostics and repeated retries](https://github.com/actions/upload-artifact/issues/737)

The shard upload now opts in only for its explicit three-file path set. A
workflow regression requires the opt-in, while the downstream combine step
continues to fail closed unless at least six shard databases exist. ZDD means
the bad candidate is discarded, both local and hosted lanes stop, and no merge,
tag, or release can reuse its partial evidence. Rollback removes the opt-in and
its regression together; that intentionally restores a red coverage consumer
and therefore cannot silently publish a release.

The current Node 24 `download-artifact` action also emits a `Buffer()`
deprecation warning. The project is already pinned to the latest v8.0.1 action;
the active upstream report reproduces the warning on both operating systems and
states that it is separate from artifact transfer correctness. Gludd does not
suppress it: the warning remains observable until an upstream action release
removes the deprecated call, at which point the immutable action pin must be
updated and rerun through both lanes.

- [download-artifact v8 Buffer warning report](https://github.com/actions/upload-artifact/issues/811)
- [actions/toolkit artifact release notes](https://github.com/actions/toolkit/blob/main/packages/artifact/RELEASES.md)

## Paired-lane fail-fast and coverage source boundary

Candidate `41a6d38e50a381ac077e433b6394592c77df716a` and hosted run
`32962870788` verified the hidden-file repair: all eight hosted shards uploaded
and the consumer combined all eight coverage databases. The pair still failed.
Locally, `unit-2` returned nonzero but the serial producer started `unit-3b` and
later shards; on GitHub, the coverage audit correctly enforced independent 75%
line and branch floors but graded four measured collection files outside its
declared `src/general_ludd` source tree along with 111 genuine source gaps.

Pytest documents `-x` as the immediate stop contract. A long-lived practitioner
request specifically calls out the hosted-time cost of continuing after failure,
and xdist issue 868 records that process-level `--maxfail` behavior has cleanup
edge cases. Gludd therefore owns fail-fast at the serial shard boundary after the
failed child's cleanup, rather than delegating cross-process policy to xdist.

- [pytest practitioner request for hosted fail-fast behavior](https://github.com/pytest-dev/pytest/issues/9515)
- [xdist maxfail cleanup report](https://github.com/pytest-dev/pytest-xdist/issues/868)

Coverage.py defines `source` as the file trees eligible for measurement and its
JSON reporting interface provides explicit include/omit selection. Gludd's audit
now applies that source boundary before accumulating either aggregate counters or
per-file results. Files within the source tree still must independently meet both
floors; filtering cannot turn a genuine low-coverage source file green.

- [coverage.py source contract](https://github.com/coveragepy/coveragepy/blob/main/coverage/control.py)
- [coverage.py JSON include/omit contract](https://github.com/coveragepy/coveragepy/blob/main/doc/python-coverage.1.txt)

The zero-downtime response is candidate invalidation: after either lane fails,
the producer stops later shards, completes only owner cleanup, emits the terminal
failure, and creates no tag or deployment. Rollback is one commit per contract;
rolling back source filtering intentionally restores the false-positive files,
while rolling back fail-fast restores wasted work but cannot make a release green.

## Diagnostic artifact workspace isolation

The clean `unit-2` replay at `dba16899a124d4bf7c9d7f078db3422c1d7495b7`
exposed a controller-caused failure rather than an application defect. While the
immutable shard was running, the operator downloaded hosted `coverage.xml` into
`.gate-logs/ci-artifacts` inside the checkout. The game acceptance test correctly
observed that new path during its repository snapshot and rejected the candidate.
The downloaded artifact was reproducible and removed; no test expectation or
application side-effect detector was weakened.

Pytest's temporary-directory documentation warns that an explicit base directory
is cleared and must be dedicated to that run. Practitioner issue 11790 records
that even pytest's default test directory can collide across independent
concurrent invocations. GitHub runner issue 4357 shows the same ownership class at
the hosted layer: overlapping workers sharing one temporary root can delete or
replace each other's active files. These reports support isolating diagnostics at
acquisition, not adding test retries or ignore patterns.

- [pytest temporary-directory ownership documentation](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.rst)
- [practitioner report: concurrent pytest temp-path collision](https://github.com/pytest-dev/pytest/issues/11790)
- [hosted runner shared-temp cleanup race](https://github.com/actions/runner/issues/4357)

`ci-artifact-download` now resolves its output beneath the project-namespaced
external resource root. Callers may name the symbolic `RESOURCE_ROOT` or an exact
descendant of that project's `ci-artifacts` directory; checkout paths and other
projects' roots fail closed. Atomic same-root publication, bounded heartbeat, and
signal cleanup are unchanged. This is ZDD for the tested application: diagnostic
downloads neither alter its checkout nor stop its services. Rollback removes the
target change and contract together, but restores the detectable workspace race.

## Hosted raw-coverage reconstruction

Hosted run `32962870788` exposed two materially different reporting boundaries.
Its merged Cobertura document contained 111 in-scope files below the independent
75% line and branch floors after four collection files were correctly excluded.
Combining the eight original shard databases retained richer branch information
and reported 110 in-scope files below those floors. The raw databases are the
authoritative release evidence; XML remains a portable diagnostic view and may not
replace or override raw-data failure evidence.

Coverage.py documents `coverage combine` as the supported operation for merging
parallel data and documents `[paths]` as the mechanism for reconciling equivalent
source roots across machines. Gludd therefore requires exactly the eight hosted
shard artifacts and maps the hosted checkout root to the current checkout before
creating JSON. It does not rewrite source files, omit low-coverage files, or weaken
thresholds. The first path in every mapping exists locally, as required by the
Coverage.py configuration contract.

- [coverage.py combine documentation](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html)
- [coverage.py path configuration](https://coverage.readthedocs.io/en/7.13.4/config.html#paths)
- [coverage.py change history](https://github.com/coveragepy/coveragepy/blob/main/CHANGES.rst)
- [practitioner report: coverage paths differ between developers](https://www.reddit.com/r/learnpython/comments/mybsu0)
- [practitioner report: rebuilding a report from retained data](https://www.reddit.com/r/learnpython/comments/szj8yq)

All downloads, combined data, JSON, and audit reports live beneath the exact
project/run resource namespace outside the checkout. Combination and JSON export
emit bounded heartbeats; interruption terminates the owned child, removes only the
temporary combine directory, and retains immutable downloaded evidence. This is
ZDD because neither the tested checkout nor a running Gludd service is mutated.
Rollback removes the audit target, contract, configuration, and tests together,
then invalidates the candidate; it cannot reinterpret the retained raw databases
as passing evidence.

`ci-coverage-gap-plan` reuses the retained coverage.py JSON and the existing
missing-line reporter. It orders failures by the worse of line or branch coverage,
prints a caller-bounded number of exact missing lines and arcs, and never writes to
the checkout. Run `32962870788` produces 110 source gaps; the first remediation
batch starts with the zero-covered chemistry core and the lowest chemistry,
router, daemon, approval, release, and process-ownership surfaces. This ordering is
diagnostic only: every source file must still reach both floors before a new
candidate can start.

## Xdist batch-fragment ownership

The first enforced concurrent candidate,
`98660c6d41a14d03d248523c4d72d5a68d0afcd0`, started the canonical local lane
and GitHub run `32974962502` together. Every hosted test, packaging, container,
execution-environment, and Molecule job passed. The hosted coverage consumer alone
failed. Gludd invalidated the candidate and terminated only its owned local runner
tree; the external model process remained untouched.

The retained raw databases initially appeared to show 110 genuine source gaps.
A same-checkout focused replay disproved that interpretation for the three worst
files: 445 chemistry tests measured `core.py` at 92% and both
`electrochemistry.py` and `process.py` at 100%. The hosted log also proved that
batch 6 ran 508 tests and saved a coverage file. The defect was the batch handoff:
with xdist and `parallel = True`, pytest-cov left an existing controller data file
plus dotted worker data files. Gludd treated existence of the controller file as
proof that combination was complete and published it without the worker evidence.

Coverage.py documents that parallel mode creates distinct dotted data files, that
`coverage combine` reads those files, and that `--append` is required to retain an
existing base data file while accumulating more results. The long-lived pytest-cov
xdist report 232 describes the same observable symptom: the distributed report
contains only code seen by the controller while the non-xdist report is complete.
The runner now always appends owned worker fragments into the controller file before
publishing a batch. A nonzero combine status fails the batch closed; no threshold,
source scope, or test expectation is weakened.

- [Coverage.py combine contract](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html)
- [Coverage.py parallel configuration](https://coverage.readthedocs.io/en/latest/config.html#run-parallel)
- [pytest-cov xdist controller-only practitioner report](https://github.com/pytest-dev/pytest-cov/issues/232)
- [pytest-cov xdist support contract](https://github.com/pytest-dev/pytest-cov)

The zero-downtime boundary is evidence-only. Each batch owns its controller and
worker coverage files beneath the project resource namespace, combines them before
workspace cleanup, copies one immutable result, and retains no process after the
batch exits. Rollback reverts the union logic and its regression together and
invalidates the candidate; it cannot reinterpret the incomplete hosted artifact as
passing release evidence.

## Pre-imported module measurement

Candidate `a94df0c10adf56bb8fd4236b10012b54b9894a65` and hosted run
`33012278432` isolated a second coverage-evidence defect after the fragment owner
was repaired. All eight raw shard databases existed, unit-1b passed all 35 direct
`test_chemistry_core.py` cases, and a no-xdist focused run measured
`chemistry/core.py` at 88%. The raw hosted reconstruction nevertheless reported
that file at 0%. Replaying the hosted pytest-cov command produced coverage.py's
`module-not-measured` warning: the dotted `--cov=general_ludd` source was imported
before measurement and its already-loaded chemistry module was not traced.

Coverage.py explicitly says `module-not-measured` means the requested module was
imported before coverage started. Pytest-cov has carried fixes for the same
"module already imported" class and, since version 2.0, documents bare `--cov` as
the way to use sources from the coverage configuration. Practitioner issue 232
records the matching xdist symptom: distributed coverage contains only the subset
seen by the controller while the non-xdist report is complete.

- [Coverage.py measurement warnings](https://coverage.readthedocs.io/en/7.15.4/messages.html)
- [pytest-cov change history](https://github.com/pytest-dev/pytest-cov/blob/master/CHANGELOG.rst)
- [pytest-cov xdist incomplete-measurement report](https://github.com/pytest-dev/pytest-cov/issues/232)

The canonical runner now passes bare `--cov`; `.coveragerc-greenlet` binds both
production trees by filesystem path. This avoids importing application packages to
resolve the measurement boundary while preserving the governance module-utils
plane and reporting unexecuted files. The coverage consumer also requires exactly
eight shard databases: incomplete evidence is an error, never a warning. The ZDD
boundary remains evidence-only—no service, database, or candidate checkout is
mutated. Rollback reverts the runner, source configuration, workflow assertion,
and tests as one unit and invalidates the candidate; it cannot bless the known
incomplete report.

## macOS DMG teardown ownership

Replacement candidate `bca04764fac5a9f25878f69cb6110388b90fa0c4` exposed a
hosted-only lifecycle failure in GitHub run `32982779016`. Both the tar and mounted
DMG binaries completed `version` and `--help`, but the normal-path unforced
`hdiutil detach` returned exit 16 because Disk Arbitration still considered the
mounted image busy. The EXIT owner already used `detach -force` and successfully
removed the mount, proving that the application artifact was healthy and the
workflow's two teardown paths had drifted.

The `hdiutil` contract documents `detach ... -force` as the option that ignores open
files on mounted volumes and documents `EBUSY` when exclusive access cannot be
obtained. Practitioner reports show the same intermittent resource-busy behavior on
GitHub-hosted macOS runners, including builds that work locally and fail in hosted
automation. Gludd now uses the same fail-closed force-detach operation on both the
success and EXIT paths. A detach failure still fails the job; there is no retry,
sleep, ignored status, or weakened artifact smoke.

- [hdiutil detach and EBUSY reference](https://ss64.com/mac/hdiutil.html)
- [GitHub Actions resource-busy practitioner report](https://github.com/create-dmg/create-dmg/issues/190)
- [long-lived hdiutil detach failure report](https://github.com/electron-userland/electron-builder/issues/7137)
- [CMake community report for hosted macOS file-I/O races](https://discourse.cmake.org/t/macos-hdiutil-packaging-on-github-actions-can-fail-if-prepackage-scripts-are-used/14990)

The smoke owns one run-attempt-namespaced root, mount point, attached image, and
trap. It detaches before removing the root and publishes an attestation only after
successful teardown, so candidate failure does not interrupt any running Gludd
service. This is ZDD for the application plane. Rollback reverts the workflow and
its structural lifecycle regression together and invalidates the candidate; it may
not restore the unforced success path or reinterpret run `32982779016` as passing.

## Coverage audit database ownership

The 2026-08-26 committed `unit-3a` replay exposed an application-owned cleanup
gap after batch 21 was repaired: batch 27 stopped at the repository hygiene
guard because `.coverage.audit.<pid>` remained after an earlier coverage-audit
failure. The audit command created that database in the checkout, but did not
unlink it on success, shard failure, report failure, or timeout.

The coverage-audit owner now unlinks its PID-namespaced database in one
`finally` boundary. At startup it also recovers databases whose numeric owner
PID no longer exists and legacy databases that predate PID ownership. A live
PID or a PID whose liveness cannot be inspected is preserved fail closed. This
recovery runs in the application owner, not in a later test or release harness.
The durable JSON progress and terminal audit report remain outside this
ephemeral database lifecycle.

Coverage.py documents `Coverage.erase()` as deleting collected data and its
API supports explicit data-file selection; practitioners have also reported
parallel and interrupted runs leaving surprising data files when lifecycle
ownership is implicit. Gludd therefore binds one data file to one audit PID and
tests success, failure, timeout, dead-owner recovery, live-owner preservation,
and permission-ambiguous preservation.

- [Coverage.py API documentation](https://coverage.readthedocs.io/en/latest/api_coverage.html)
- [Coverage.py parallel data documentation](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html)
- [coverage.py issue 1752](https://github.com/nedbat/coveragepy/issues/1752)

ZDD is unchanged: no running service or candidate deployment is mutated.
Rollback reverts the owner/test/documentation commit; retained JSON evidence is
not deleted, and a possible live database is never reclaimed speculatively.

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

- [GitHub Community discussion 141795](https://github.com/orgs/community/discussions/141795),
  opened 2024-10-17 and reviewed 2026-08-26, documents practitioner confusion
  when `matrix.include` fields do not attach to combinations as expected.
- [GitHub Community discussion 26284](https://github.com/orgs/community/discussions/26284)
  records the long-lived user report that repeating the same matrix across jobs
  is error-prone when entries are added or removed. Gludd avoids the analogous
  workflow/runner split by keeping shard paths and exclusions in one registry.

Reviewed 2026-08-25:

- [GitHub Checkout documentation](https://github.com/actions/checkout), reviewed
  2026-08-25, states that only one commit is fetched by default and documents
  `fetch-depth: 0` as the complete-history contract used by hosted session
  evidence.
- [GitHub Community discussion 25950](https://github.com/orgs/community/discussions/25950),
  opened 2020-10-09 and reviewed 2026-08-25, records practitioner `bad object`
  failures caused by the default shallow checkout and the complete-history fix.
- [GitHub Community discussion 26818](https://github.com/orgs/community/discussions/26818),
  opened 2020-03-27 and reviewed 2026-08-25, preserves the long-lived
  `Needed a single revision` failure and explains why explicit depth zero is
  required for history-sensitive automation.
- [HTTPX discussion 1633](https://github.com/encode/httpx/discussions/1633),
  opened 2021-05-10 and reviewed 2026-08-25, records maintainer and practitioner
  guidance that a synchronous client is designed to be shared across threads,
  while lifecycle changes such as closing or replacing a client during another
  thread's request are a distinct unsafe boundary. Gludd therefore stabilizes
  the transport dependency for an instance instead of swapping a module global.
- [SQLAlchemy issue 13039](https://github.com/sqlalchemy/sqlalchemy/issues/13039),
  opened 2025-12-17 and reviewed 2026-08-26, records practitioner evidence that
  abandoned `aiosqlite` connections can retain worker resources and leave the
  underlying database in an unknown state. This supports explicit owner disposal
  rather than relying on interpreter exit or garbage collection.
- [SQLAlchemy discussion 10457](https://github.com/sqlalchemy/sqlalchemy/discussions/10457),
  opened 2023-10-11 and reviewed 2026-08-26, records maintainers explaining that
  graceful async-driver cleanup requires connections to be closed inside the
  active event loop and the engine to be explicitly disposed. The E2E helper
  therefore keeps teardown in the same async scope as acquisition.
- [aiosqlite connection implementation](https://github.com/omnilib/aiosqlite/blob/main/aiosqlite/core.py),
  reviewed 2026-08-26, emits a `ResourceWarning` when a live connection reaches
  finalization and directs callers to an async context or explicit `close()`.
  The beta4 gate treats that warning as a resource-ownership failure.
- [pytest issue 13768](https://github.com/pytest-dev/pytest/issues/13768), opened
  2025-09-30 and reviewed 2026-08-25, explicitly calls out fixtures such as
  `monkeypatch` as not feasibly thread-safe. The hosted failure is the concrete
  manifestation: fixture restoration and a live executor overlapped.
- [HTTPX transport documentation](https://www.python-httpx.org/advanced/transports/),
  reviewed 2026-08-25, defines explicit transport injection as the supported
  boundary for deterministic request behavior and testing.
- [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal),
  reviewed 2026-08-25, enumerates forward-slash, backslash, percent-encoded, and
  double-encoded traversal variants and notes that Windows accepts both separator
  forms. Gludd canonicalizes every model-supplied workspace path before its jail
  decision rather than trusting host-specific `realpath` parsing alone.
- [Flask issue 2546](https://github.com/pallets/flask/issues/2546), opened
  2018-01-11 and reviewed 2026-08-25, preserves long-lived practitioner reports
  that safe path handling differed between slash and backslash forms on Windows.
  The execution engine therefore applies one portable separator contract on all
  hosts.
- [Werkzeug changelog](https://github.com/pallets/werkzeug/blob/main/CHANGES.rst),
  reviewed 2026-08-25, records multiple years of `safe_join` Windows hardening,
  including empty-root containment and device-name fixes. This supports Gludd's
  fail-closed cross-platform validation before OS path resolution.
- [PyInstaller “When Things Go Wrong” documentation](https://pyinstaller.org/en/stable/when-things-go-wrong.html),
  reviewed 2026-08-25, defines `warn-<name>.txt` as the complete missing-module
  analysis record and `xref-<name>.html` as the import graph. Gludd therefore
  retains the raw warning file instead of treating a digest-only message as
  sufficient review evidence.
- [actions/upload-artifact issue 457](https://github.com/actions/upload-artifact/issues/457),
  opened 2023-11-28 and still reported against v4 in 2025, records the
  practitioner-visible gap where `if-no-files-found: error` succeeds when one of
  several requested paths exists. Gludd verifies its mandatory warning file in a
  separate step before the multi-path upload.
- [GitHub CLI issue 8536](https://github.com/cli/cli/issues/8536), opened
  2024-01-07 and reviewed 2026-08-25, records the long-lived request for visible
  `gh run download` progress. The Make boundary adds a project heartbeat without
  replacing the mature GitHub CLI transfer.
- [GitHub CLI issue 12437](https://github.com/cli/cli/issues/12437), opened
  2026-01-07 and reviewed 2026-08-25, reports that rerun attempts can make an
  artifact name resolve to older evidence. The download target first binds one
  live artifact name to the explicit immutable run ID and refuses ambiguity.
- [Ansible Lint usage documentation](https://docs.ansible.com/projects/lint/usage/),
  reviewed 2026-08-25, documents schema-refresh control and defines offline
  execution as the supported hermetic boundary. Gludd uses the narrower
  documented schema-update skip because collection dependencies are already
  locked and remain part of the production-profile lint.
- [ansible-lint issue 5086](https://github.com/ansible/ansible-lint/issues/5086),
  opened 2026-06-05 and reviewed 2026-08-25, records Python 3.14 practitioner
  evidence that ansible-lint still creates and uses cache directories despite
  configured ownership paths. The release lint therefore removes schema-network
  cache acquisition entirely and treats any remaining resource warning as fatal.
- [pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/latest/distribution.html),
  reviewed 2026-08-25, defines explicit worker counts and zero as the supported
  way to disable crashed-worker restarts. The local producer inherits the
  runner's fixed one-worker/zero-restart policy instead of duplicating it in Make.
- [pytest-xdist issue 18](https://github.com/pytest-dev/pytest-xdist/issues/18),
  opened 2015-12-02 and reviewed 2026-08-25, preserves long-lived practitioner
  evidence that distributing tests can recreate fixtures on multiple workers.
  Gludd bounds cumulative state with fresh batches while keeping each batch on
  exactly one owned worker.
- [pytest temporary-path documentation](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.rst),
  reviewed 2026-08-25, states that an explicit `--basetemp` owns the complete
  `tmp_path` tree and is cleared without retention. Gludd therefore gives the
  base one exact project namespace and removes it through the shard owner.
- [pytest discussion 12283](https://github.com/pytest-dev/pytest/discussions/12283),
  opened 2024-05-03 and reviewed 2026-08-25, records practitioner reports of
  multi-gigabyte retained pytest trees and the recommendation to use explicit
  retention policy. Gludd instead uses per-batch owner cleanup with no retained
  passing workspace.
- [Python 3.12 datetime deprecations](https://docs.python.org/3/whatsnew/3.12.html),
  reviewed 2026-08-25, identifies `datetime.utcnow()` as deprecated beginning in
  Python 3.12 and recommends `datetime.now(UTC)`.
- [Python.org core-development discussion 26221](https://discuss.python.org/t/deprecating-utcnow-and-utcfromtimestamp/26221),
  opened 2023-04-26 and reviewed 2026-08-25, preserves the long-lived
  practitioner compatibility debate around naive UTC data. Gludd's application
  contract remains aware UTC; its compatibility test distinguishes 3.11 from the
  3.12 warning boundary rather than treating debate as runtime behavior.
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
- [GitHub CLI `gh run view` manual](https://cli.github.com/manual/gh_run_view)
  documents explicit run-ID selection and the structured run/job fields used by
  the immutable summary rather than human-oriented output scraping.
- [GitHub REST workflow-run documentation](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run)
  defines the run-ID resource boundary used to reject a response for any other
  execution.
- [GitHub Community discussion 27083](https://github.com/orgs/community/discussions/27083)
  documents the practical consequence that a rerun uses the workflow from the
  original SHA rather than a later fix.
- [GitHub Community discussion 17854](https://github.com/orgs/community/discussions/17854)
  reports artifact replacement and visibility surprises across reruns, supporting
  immutable, SHA-bound terminal evidence instead of artifact-name trust alone.
- [GitHub Actions variables reference](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
  defines `GITHUB_REF_TYPE` as exactly `branch` or `tag`, providing the hosted
  boundary between an uncut branch candidate and post-tag verification.
- [GitHub Community discussion 7118](https://github.com/orgs/community/discussions/7118)
  records practitioner demand for the safe order used here: commit the version,
  build and test it, and only then create or publish the release tag.
  Schema 3 additionally validates the downloaded contents and pairs each hosted
  test-plan digest to its local counterpart instead of treating the artifact name
  or green conclusion as proof of equivalent work.
- [pytest-xdist issue 1323](https://github.com/pytest-dev/pytest-xdist/issues/1323)
  documents hangs and completed-work requeue behavior after worker restart.
- [pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313)
  documents controller hangs after a worker dies with retained pipes.
- [pytest-xdist issue 868](https://github.com/pytest-dev/pytest-xdist/issues/868),
  opened 2022-02-16 and reviewed 2026-08-25, records practitioner evidence that
  stop conditions can still allow another test to start. Gludd therefore binds a
  signal-derived child result to the entire serial plan rather than only the
  current bounded batch.
- [pytest-xdist issue 1278](https://github.com/pytest-dev/pytest-xdist/issues/1278)
  documents missed nonzero worker exits, supporting fail-closed controller parsing.
- [pytest-xdist issue 60](https://github.com/pytest-dev/pytest-xdist/issues/60)
  preserves the long-lived practitioner report of Ctrl-C interrupting xdist worker
  teardown, supporting a runner-owned cancellation boundary around final cleanup.
- [pytest issue 1120](https://github.com/pytest-dev/pytest/issues/1120) records
  practitioner disk exhaustion from accumulated pytest temporary directories,
  supporting deterministic removal rather than leaving interrupted roots behind.
- [Python 3.14 `shutil.rmtree` documentation](https://docs.python.org/3/library/shutil.html#shutil.rmtree)
  defines top-level absence and non-`OSError` interruption behavior; Gludd handles
  absence idempotently and defers cancellation explicitly instead of suppressing
  arbitrary filesystem failures.
- [CPython issue 93852](https://github.com/python/cpython/issues/93852) records
  the long-lived practitioner failure where nested temporary paths exceed the
  107-byte Linux AF_UNIX limit.
- [CPython multiprocessing temp-directory implementation](https://github.com/python/cpython/blob/main/Lib/multiprocessing/util.py)
  documents the platform socket limits and the same short-system-temp fallback
  Gludd applies explicitly for supported Python versions.
- [GitHub Actions runner issue 3760](https://github.com/actions/runner/issues/3760)
  reports cross-runner state and ownership failures when runtime directories are
  shared, supporting Gludd's per-project, per-batch namespaces.
- [GitHub Actions runner issue 1031](https://github.com/actions/runner/issues/1031),
  opened 2021-03-30 and reviewed 2026-08-25, preserves a practitioner reproducer
  where work completing in under a second locally takes seconds or minutes for
  the hosted runner to process. Gludd therefore does not infer message boundaries
  from time consumed by its own synchronous hook work.
- [Node.js issue 21822](https://github.com/nodejs/node/issues/21822), opened
  2018-07-25 and reviewed 2026-08-25, records long-lived practitioner evidence
  that event-loop contention makes wall-clock timer delivery drift. The plugin
  uses elapsed time only between completed hook calls, never as a proxy for the
  duration of the hook itself.
- [GitHub's `upload-artifact` documentation](https://github.com/actions/upload-artifact#uploading-hidden-files),
  reviewed 2026-08-25, specifies that dotfiles are excluded by default and that
  `if-no-files-found: error` fails when no eligible path exists.
- [`upload-artifact` issue 602](https://github.com/actions/upload-artifact/issues/602),
  reviewed 2026-08-25, records practitioner impact from the hidden-file default;
  Gludd therefore materializes an explicit non-hidden diagnostic log.
- [pygame license clarification issue 3521](https://github.com/pygame/pygame/issues/3521)
  records practitioner concern about ambiguous LGPL version notation, supporting
  a version-pinned upstream license link in the shipped notice.
- [pygame 2.6.1 license text](https://github.com/pygame/pygame/blob/2.6.1/docs/LGPL.txt)
  is the authoritative license artifact for the exact locked release.
- [Git worktree documentation](https://git-scm.com/docs/git-worktree) describes
  locked worktree ownership and pruning behavior used to preserve active candidate
  state.
- [Python Enum HOWTO](https://docs.python.org/3/howto/enum.html#comparisons)
  warns that reloading a module recreates its enum classes and members, so old
  and new members may no longer compare identical or equal.
- [Python.org discussion 105716](https://discuss.python.org/t/how-to-deal-with-enum-reload-problem/105716)
  records a current practitioner report of the same reload identity failure.
- [CPython issue 74730](https://github.com/python/cpython/issues/74730) preserves
  the long-lived 2017 report and reproducer for enum equality across reloads.
- [HashiCorp vSphere provider namespace migration](https://github.com/hashicorp/terraform-provider-vsphere/issues/1400)
  records practitioner impact from the provider's move to the `vmware` namespace;
  the canonical stack contract must not regress to the historical `hashicorp`
  source merely to satisfy a stale structural assertion.
- [CPython issue 31800](https://bugs.python.org/issue31800) records the original
  practitioner request to accept colonized UTC offsets with `%z`; Python 3.7
  implemented that behavior, so a modern compatibility test must not retain the
  pre-3.7 failure expectation.
- [Stack Overflow discussion 30999230](https://stackoverflow.com/questions/30999230/how-to-parse-timezone-with-colon)
  preserves the long-lived user impact and the version-dependent workaround that
  motivated native colonized-offset parsing.
- [Python datetime documentation](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes)
  is the authoritative contract: `%z` accepts colon separators in
  `datetime.strptime()` beginning with Python 3.7.
- [FastAPI discussion 3958](https://github.com/fastapi/fastapi/discussions/3958)
  records the long-lived practitioner requirement that two FastAPI instances
  own separate service state rather than sharing a module-global singleton.
- [FastAPI discussion 8239](https://github.com/fastapi/fastapi/discussions/8239)
  captures years of practitioner experience with app-scoped resources,
  including test applications and reusable routers that cannot safely rely on
  one global instance.
- [Starlette lifespan discussion 2067](https://github.com/encode/starlette/discussions/2067)
  documents teardown and thread-safety footguns in globally shared resources
  and recommends context-managed, application-scoped ownership.
- [OpenCode plugin documentation](https://opencode.ai/docs/plugins/) documents
  the supported plugin-loading contract used by the singular manifest entry and
  the runtime hook acceptance test.
- [Python structural pattern matching documentation](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement)
  defines the literal `match`/`case` dispatch used by the TUI renderer; contract
  tests inspect that syntax tree instead of requiring an obsolete conditional
  spelling.
- [Python `time.monotonic` documentation](https://docs.python.org/3/library/time.html#time.monotonic)
  defines the production clock as one that cannot move backwards and is not
  affected by system-clock updates.
- [pytest issue 13384](https://github.com/pytest-dev/pytest/issues/13384)
  records a practitioner report of invalid duration results from the wrong clock
  source and the resulting move to a monotonic performance clock. Gludd also
  makes that clock injectable so threshold tests do not depend on scheduler load.
- [pytest flaky-test guidance](https://docs.pytest.org/en/stable/explanation/flaky.html),
  reviewed 2026-08-26, identifies uncontrolled system state, parallel execution,
  and overly strict timing assertions as common CI flake sources. The temp-root
  regression now controls its wall clock directly rather than inferring age from
  scheduler and storage latency.
- [CPython issue 120754](https://github.com/python/cpython/issues/120754), opened
  2024-06-19 and reviewed 2026-08-26, records practitioner evidence that ordinary
  filesystem system calls can be particularly slow on networked storage. Gludd
  retains its durable manifest fsync and removes the test's 100-millisecond
storage-performance assumption instead.

Cancelling that same paired candidate exposed a second owner boundary after all
pytest children and temporary roots had already been reaped: terminal evidence
re-expanded every shard from the checkout, and a repeated interrupt during that
long scan escaped as `KeyboardInterrupt`. The runner now snapshots the canonical
shard plans before execution, reuses that immutable pairing during publication,
and defers SIGINT/SIGTERM through the bounded atomic write. A signal received in
that phase remains observable as `128 + signal`; if it changes an otherwise
successful result, terminal evidence is rewritten with the cancellation status.
No signal is ignored, no child survives, and no cleanup task exists outside the
runner owner. Rollback is the isolated runner/test commit; zero-downtime behavior
remains candidate invalidation before tag creation.

Hosted run `32950480801` on 2026-08-26 exposed a separate outer-lifecycle
boundary on exact candidate `a66ae3acb`: `unit-2` and `other` were still
emitting passing tests when the workflow's 40-minute test-step deadline killed
their process trees. The subsequent missing coverage and attestation files were
effects of that forced termination, not independent test failures. The hosted
test step now has 90 minutes inside the existing 120-minute job ceiling, leaving
a mechanically pinned 30-minute reserve for owner cleanup, diagnostics, and
artifact publication. Test selection, single-worker execution, warnings-as-
errors, coverage policy, and fail-closed missing-artifact behavior are unchanged.
ZDD remains pre-release candidate invalidation: a timed-out lane can never be
promoted, and rollback is the workflow/test/documentation commit before a new
exact-SHA pair is started.

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes),
  reviewed 2026-08-26, defines a step timeout as the point where GitHub kills
  the process and distinguishes it from the enclosing job timeout. Gludd keeps
  the step deadline below the job deadline so cleanup and evidence publication
  retain an explicit resource budget.
- [GitHub Community discussion 38004](https://github.com/orgs/community/discussions/38004),
  opened 2022-11-01 and reviewed 2026-08-26, records practitioner experience
  that a `timeout-minutes` expiry is reported as failure because the application
  did not finish. Gludd therefore never reclassifies expiry as success or uploads
  placeholder evidence.
- [GitHub Community discussion 10690](https://github.com/orgs/community/discussions/10690),
  opened 2022-01-28 and reviewed 2026-08-26, records the long-lived request for
  centrally managed step/job timeouts and the operational drift caused by
  repeating them. The regression reads the canonical workflow structurally and
  pins the relationship between the one shard step and its enclosing job.

- [Python `time.localtime` documentation](https://docs.python.org/3/library/time.html#time.localtime),
  reviewed 2026-08-26, defines a timestamp-free call as using the current time
  and returning local-time fields. Off-peak policy therefore requires one
  explicit wall-clock sample, not ambient calls whose result varies by runner.
- [Freezegun issue 176](https://github.com/spulec/freezegun/issues/176), opened
  2017-03-22 and reviewed 2026-08-26, preserves practitioner reports where
  fixture construction escapes a frozen interval and time-zone offsets change
  the result. Gludd evaluated that mature test tool but uses constructor-level
  clock injection so the application decision itself is coherent without a
  process-wide time monkeypatch or another runtime dependency.
- [Freezegun's documented time-zone behavior](https://github.com/spulec/freezegun#timezones)
  demonstrates that local and UTC dates can differ under an explicit offset.
  The regression therefore fixes a local hour intentionally rather than assuming
  the GitHub runner and developer machine share a zone.
- [RFC 9562 section 6.2](https://www.rfc-editor.org/rfc/rfc9562.html#section-6.2)
  specifies a counter immediately after the timestamp for ordered UUIDv7 batch
  generation within one millisecond.
- [CPython's UUIDv7 implementation](https://github.com/python/cpython/blob/main/Lib/uuid.py)
  is the mature implementation reference for the 42-bit counter and 32-bit
  random-tail layout adopted by Gludd.
- [CPython issue 102461](https://github.com/python/cpython/issues/102461)
  preserves the multi-year practitioner and maintainer discussion that led to
  UUIDv7 support in the standard library.
- [PyWavelets signal-extension documentation](https://pywavelets.readthedocs.io/en/latest/ref/signal-extension-modes.html)
  defines periodization as the smallest-coefficient boundary mode and requires
  the inverse transform to use the same mode for reconstruction.
- [PyWavelets multilevel implementation](https://github.com/PyWavelets/pywt/blob/main/pywt/_multilevel.py)
  documents that levels above `dwt_max_level` warn because every coefficient has
  boundary effects; Gludd composes supported single-level transforms instead of
  hiding that warning.
- [PyWavelets issue 306](https://github.com/PyWavelets/pywt/issues/306) records
  the long-lived practitioner confusion around “maximum useful level” and the
  distinction between invertibility and coefficients unaffected by boundaries.
- [PyWavelets issue 472](https://github.com/PyWavelets/pywt/issues/472) records
  practitioner evidence that periodization preserves the inverse/adjoint
  properties expected by matrix-based reconstruction.

## Canonical import coverage boundary

Hosted run `33000301777` on 2026-08-26 passed every test shard but correctly
failed its terminal coverage audit: 111 production files missed the 75 percent
per-file branch floor. The first remediation group exposed chemistry tests that
executed source through ad-hoc `spec_from_file_location` module identities.
Those tests could pass while canonical package execution remained absent or
under-attributed in combined coverage. Chemistry tests now use installed
`general_ludd.chemistry` imports, and additional owner-focused cases exercise
the real validation, rollback, safety, and resource branches. No source is
excluded and no partial-branch pragma or threshold reduction is used.

Candidate `d28ae16cc4e1df95cee19ed89d0b3aa7fe8fd01c` proved the same import
boundary must also govern the repository's coverage-gap checker. Hosted run
`33009531821` failed `unit-1b`; the paired local producer stopped on the same
immutable SHA because the checker regression still required chemistry's removed
private `_ANALYTICAL_PATH` file-loader fixture. The contract now asks the real
checker to map `analytical.py` to the installed-package chemistry suite, and the
exact node passes on both Python 3.11 and the local Python runtime. Production
chemistry and checker behavior are unchanged.

The producer stopped fail-closed at the first invalid batch, published no green
attestation, and started no daemon or model process. Pytest retained ownership
of its temporary batch resources, so no release cleanup task or application
lifecycle compensation was added. Rollback is the single test/documentation
commit; ZDD remains rejection of the untagged SHA before a new paired local and
hosted candidate starts. The coverage.py and pytest-cov upstream/practitioner
evidence below remains the governing import-measurement contract.

The zero-downtime rule is candidate invalidation: a green test matrix with red
coverage is never tagged or released. Rollback is the isolated test and
documentation commit. Resources remain bounded to the existing one-worker
local shard runner and the eight exact-run hosted artifacts; the invalid local
candidate was interrupted through its owner, which reaped the active worker and
removed its namespaced temporary root before remediation began.

- [coverage.py branch documentation](https://coverage.readthedocs.io/en/latest/branch.html),
  reviewed 2026-08-26, defines a branch opportunity as each possible
  source-to-destination transition. This is why passing statements alone cannot
  satisfy Gludd's per-file branch contract.
- [coverage.py change history](https://github.com/coveragepy/coveragepy/blob/main/CHANGES.rst),
  reviewed 2026-08-26, records issue 1232, where source packages imported before
  measurement could be partially measured. Gludd avoids ambiguous test-only
  module identities by exercising the same installed import path as production.
- [pytest-cov issue 578](https://github.com/pytest-dev/pytest-cov/issues/578),
  opened 2023-01-27 and reviewed 2026-08-26, preserves practitioner evidence
  that executed multiprocessing work can appear uncovered without an explicit
  process-measurement boundary. The beta4 shard configuration retains
  coverage.py's subprocess patch and combines exact-run fragments fail-closed.
- [pytest-cov 7 change history](https://github.com/pytest-dev/pytest-cov/blob/master/CHANGELOG.rst),
  reviewed 2026-08-26, documents the migration from the implicit `.pth`
  subprocess hook to coverage.py's explicit `patch = subprocess` mechanism used
  by the canonical local and hosted lanes.

### Algorithm branch ownership

The second remediation group covers the six algorithm files named by the same
hosted audit. Canonical branch tests now exercise malformed elliptic-curve
points, bounded OPRF failure, deep persistent/transient trie transitions,
finger-tree shape guards and wrappers, sweep-line neighbor exposure, and Qhull's
degenerate fallbacks. The measured slice passes 252 tests at 94 percent
aggregate coverage, with every file above both the 75 percent line and branch
floors. It also removed three unreachable implementations: an unused polygon
sweep triangulator, two unused finger-tree node deconstructors, and impossible
post-pop zero-count branches. Public behavior and the stricter coverage policy
remain unchanged.

This is owner-side cleanup, not a release workaround. No hosted retry, cleanup
job, coverage exclusion, or partial-branch pragma was added. Rollback is the
single algorithms commit; zero-downtime behavior is still rejection of the
untagged candidate until a new exact SHA passes both local and hosted lanes.

### Agent recovery and research boundaries

The next exact hosted group covers dispatch checkpointing and research. Ten
canonical tests exercise durable-key regeneration, hostile checkpoint names,
invalid and legacy snapshot envelopes, corrupt spool offsets, no-bus resume
observability, the real SearX client type boundary, URL and low-quality-domain
filtering, bounded result caps, page-fetch success and failure, and confidence
band reporting. The focused surface passes 151 tests at 97 percent aggregate;
dispatch checkpointing is 100 percent and researcher is 96 percent.

No test starts a daemon or external search service, and no cleanup task
compensates for the application. Temporary keys, snapshots, and sidecars remain
inside pytest-owned roots; page/search failures are handled by the existing
owner contracts. Rollback is the isolated test/documentation commit, while ZDD
continues to mean invalidating the untagged candidate before another exact-SHA
local/hosted pair is launched.

### Generated-test symbol ownership

The generated-test slice now pins parser absence, decorated and nested
definitions, malformed tree-sitter nodes, class-owned methods, and caller-owned
``conftest.py`` preservation. The TDD boundary exposed a real ownership defect:
class methods were also reported as module-level functions. The analyzer now
recurses through blocks and bodies without re-walking a class as a module. The
focused surface passes 53 tests with 100 percent line and branch coverage for
both measured production files.

Tree-sitter remains an optional controller capability. Its absence is cached,
observable, and fail-soft; no parser process, daemon, network call, or cleanup
task is introduced. Generated files stay inside pytest-owned temporary roots,
and pre-existing configuration remains caller-owned. Rollback is the isolated
analyzer/test/documentation commit. ZDD continues to reject the untagged
candidate until a new immutable local/hosted pair validates the same plan and
policy.

### Hosted issue-source safety branches

The hosted artifact also separated ClickUp, Monday.com, Bitbucket, and Linear
adapter branches from their existing behavior suites. The added contracts cover
literal-host SSRF classifications, non-dictionary and invalid response bodies,
missing credentials and repository identifiers, normalization fallbacks,
GraphQL errors, and client reuse versus client ownership. The combined replay
passes 106 tests at 94% aggregate coverage; each adapter is between 94% and 96%
and clears the separate 75% line and branch thresholds.

All network activity is injected, clients opened by an adapter remain scoped to
that adapter, and no harness finalizer compensates for production ownership.
Invalid hosts and malformed responses fail closed before state write-back. ZDD
is preserved because the change adds evidence only: no connector configuration,
credentials, issue state, or remote comments are changed, and rollback removes
only the regression commit. The earlier upstream SSRF, GitHub Actions, and
coverage.py practitioner references remain applicable to this hosted-only gap.

### Git and release control-plane branches

The hosted aggregate showed that Git history dispatch, release orchestration,
and duplicate-target enforcement were behaviorally green but split away from
the tests that exercised their branch surfaces. The beta4 regression file now
drives every allowlisted Git operation, validation and unsupported-result
boundary, non-interactive `git` and `gh` failure, CI conclusion, README version
check, release repository failure, and duplicate-target CLI outcome. The
focused replay passes 147 tests at 95% aggregate coverage; the three measured
files report 93%, 94%, and 97%, with both line and branch coverage above 75%.

These tests do not push, tag, delete, or contact GitHub. All transports are
injected at the application boundary, malformed input remains fail-closed, and
the existing ZDD ordering remains unchanged: exact-SHA CI evidence precedes
branch/tag publication, while failed publication never advances release state.
Rollback removes only the regression commit; no Git refs or release artifacts
are mutated. The upstream GitHub Actions and coverage.py practitioner evidence
recorded earlier in this document continues to govern the exact-SHA pairing and
coverage-artifact policy.

### Control-plane decision and session branches

The hosted coverage artifact exposed three control-plane files whose local
behavior suites were green but whose branch evidence was incomplete. The
beta4 slice now exercises approval persistence and status mapping, browser
credential storage and OAuth failure paths, and daemon-chat streaming and
interactive shutdown paths. The focused replay passes 591 tests with two
dependency-gated skips at 93% aggregate coverage; each measured file clears
the separate 75% line and branch floors.

The approval replay also reproduced a Python 3.14 runtime defect: synchronous
decision lookup depended on an implicit event loop, so completed approvals
could silently remain pending. The owner now creates a bounded event loop only
when no loop is already running, never blocks an active loop, and treats
repository-construction or lookup failures as pending. This preserves ZDD:
no approval is inferred during migration or failure, and rollback is the
single approval-gate commit without changing stored todo data. Browser callback
servers and HTTP clients retain their owner-side shutdown; tests add no cleanup
task that compensates for application lifecycle behavior. The upstream and
practitioner sources recorded earlier in this document remain the basis for the
dual-track, exact-SHA, fail-closed policy.

### AI and ML safety branches

The hosted artifact's next seven files cover accelerator planning, adapter
training, model distillation, bounded reasoning, immutable registries, speech,
and vision. Canonical negative-path tests now exercise resource budgets,
checkpoint and resume state, stop dispositions, retention and safety gates,
registry mutation ownership, voice consent and audio retention, and grounded
image provenance. The measured surface passes 243 tests at 97 percent aggregate
coverage; every production file is between 95 and 99 percent with separate line
and branch measurement.

The tests allocate no accelerator, model process, speech service, image
service, registry daemon, or network client. They validate the typed owner
boundaries in process, while pytest owns the coverage and temporary artifacts.
No release cleanup task, retry, coverage exclusion, or warning suppression was
added. Rollback is the isolated tests/documentation commit, and ZDD remains
fail-closed candidate invalidation before the next exact-SHA local and hosted
lanes start.

### Interpreter identity and terminal hosted coverage

Candidate `a94df0c10adf56bb8fd4236b10012b54b9894a65` and hosted run
`33012278432` made two independent release blockers observable. All 23 hosted
execution jobs passed, including every named Python 3.11 shard, but the terminal
coverage job rejected 79 production files below the separate 75 percent line or
branch floor. The release job therefore remained skipped. The paired local
producer passed through unit-2, then its mutable `.venv/bin/python3` launch path
changed from CPython 3.11.14 to CPython 3.14.0 while the long-lived owner process
still reported its original runtime. Recording only that owner process would
have produced misleading evidence for later batches.

The run-bound coverage planner now reads either coverage.py JSON or the
Cobertura XML that the hosted aggregate actually publishes. XML condition
coverage is converted into exact source-to-destination branch arcs, filtered to
`src/general_ludd`, sorted by the lower of line and branch coverage, and bounded
by the caller's explicit limit. It reads only the external artifact namespace
and writes nothing to the checkout. Missing artifacts remain a hard error, so
the planner cannot manufacture a remediation list from stale local coverage.

The canonical runner now binds Python major/minor and implementation into the
paired execution-policy digest. It also probes the exact resolved executable,
implementation, and patch version before and after every batch. Any probe
failure or identity change stops the plan with an observable interpreter-drift
result before another batch can start. This is owner-side fail-closed behavior,
not a test retry or cleanup task. The batch process and temporary root still use
the existing bounded TERM-to-KILL and idempotent cleanup paths.

ZDD remains candidate invalidation: no tag, branch promotion, artifact publish,
or deployment can consume the failed SHA. Rollback removes the isolated runner,
tests, task evidence, and this section. The added resource cost is one bounded
five-second child interpreter identity probe at each batch boundary; it uses the
same executable path the next pytest process would use, opens no network or
daemon resource, and leaves no persistent artifact.

- [uv command reference](https://docs.astral.sh/uv/reference/cli/), reviewed
  2026-08-26, defines `uv sync` as management of the project `.venv` and notes
  that `--python` participates in interpreter discovery.
- [uv project environment documentation](https://docs.astral.sh/uv/concepts/projects/config/),
  reviewed 2026-08-26, defines `.venv` as the default mutable project
  environment and provides `UV_PROJECT_ENVIRONMENT` for an alternate isolated
  path.
- [uv issue 15603](https://github.com/astral-sh/uv/issues/15603), opened
  2025-08-31 and reviewed 2026-08-26, preserves practitioner and maintainer
  reports that sync can recreate an active environment when Python requests
  differ; maintainers describe project environments as automatically managed
  and ephemeral.
- [uv issue 17283](https://github.com/astral-sh/uv/issues/17283), opened
  2026-01-02 and reviewed 2026-08-26, records a long-lived user report of a
  shared environment switching from Python 3.12 to 3.14 during project commands.

### Generated-game budget ownership

Candidate `a25ca77f6996d2fa955d676b0eb57942e7c44398` passed 19 hosted
jobs without a failure while the paired macOS/Python 3.14 lane rejected a
minimal generated game in `unit-2`. The rejection claimed that module import
exceeded the five-second game budget. The game itself was not slow: the CLI's
in-process path armed `SIGALRM` before recursively snapshotting the entire
checkout, so repository size and coverage-fragment churn were charged to
untrusted generated code.

The CLI now uses the existing subprocess acceptance boundary. The child starts
with the generated file's directory as its working directory, preserves the
explicit module-name contract, writes its verdict through the existing owned
temporary file, and remains subject to the bounded subprocess timeout. The
filesystem comparison therefore covers the only directory the generated game
may mutate without making repository traversal part of its runtime budget.
Static forbidden-call checks, child timeout, verdict validation, and idempotent
temporary-verdict cleanup remain fail closed.

Replacement candidate `6bd86f3de7109d1867556cccd5dd90d76336a746` and hosted
run `33022851420` reached 21 successful hosted jobs, while the paired local
lane found the remaining direct `check_source` API still executing generated
code under the caller's process-wide signal timer. Repository traversal consumed
that shared budget late in the ordered batch, so the timer fired while the phase
label named `restart()` and obscured the generated game's real restart exception.
The hosted run was cancelled as soon as the local lane invalidated the SHA.

`check_source` now stages source in an owned temporary directory and delegates
to the same bounded child used by file and CLI acceptance. The obsolete
in-process signal executor and repository-wide snapshot implementation are
removed, not excluded from coverage. The child also preserves the established
distinction between a dynamically missing method and a method that raises. The
complete prior failure batch passes 475 tests with two dependency-gated skips;
the focused source and file acceptance surface passes 43 tests at 91 percent
branch coverage.

This is ZDD by candidate invalidation: the discrepant SHA is not promoted,
tagged, published, or deployed. Local and hosted lanes must restart on the new
exact commit and produce matching plan/policy attestations. Rollback is the
single acceptance/test/documentation commit. The resource boundary is one
short-lived Python child per accepted file, one same-directory audit scope, and
one owned temporary verdict file; no daemon, port, retry task, cleanup job,
warning suppression, or coverage exclusion is added.

- [Python `subprocess.run` documentation](https://docs.python.org/3/library/subprocess.html#subprocess.run),
  reviewed 2026-08-26, defines the child timeout and working-directory contract
  used by the acceptance boundary.
- [pytest-timeout signal documentation](https://github.com/pytest-dev/pytest-timeout#signal),
  reviewed 2026-08-26, warns that `SIGALRM`-based timeouts can interfere with
  code under test that also uses the signal, supporting process isolation.
- [pytest-cov issue 608](https://github.com/pytest-dev/pytest-cov/issues/608),
  opened 2023-09-02 and reviewed 2026-08-26, preserves practitioner evidence
  that subprocess execution and coverage instrumentation have materially
  different behavior from the parent test process.

### Exact hosted floor enforcement and connector branches

Candidate `b8132738e65d8806215ba242e0245747d8836dd1` and hosted run
`33025997411` passed all 23 execution jobs, then the terminal coverage job
`98377370114` rejected 78 production files below the required per-file line or
branch floor. The release job remained skipped. The paired local producer had
passed shards 1 through 4 and unit-2 through batch 38, including the prior
hosted-only timeout boundary, when the hosted terminal failure invalidated the
SHA. Its owner received the interrupt, returned status 130, reaped its worker,
and removed its namespaced temporary resources before remediation began.

That run also exposed a policy-wiring defect. The workflow enforced 75 percent
as both the aggregate and per-file threshold even though the project contract
requires 85 percent aggregate and 75 percent for each file. The canonical
runner and `coverage-files` target could reproduce the same weaker aggregate
floor. All three producers now pass the aggregate and per-file thresholds as
separate explicit values. Structural tests pin the workflow, runner, and Make
contracts so a future hosted lane cannot silently weaken one while keeping the
other.

The first six remediated groups cover 35 of the 78 exact hosted gaps. Their
focused replays pass 326, 473, 951, 259, 72, and 100 tests respectively. The measured
groups report between 92 and 97 percent aggregate line-and-branch coverage, with every
production file independently above both 75 percent floors. Negative-path
coverage includes connector transport failures, malformed response shapes,
secret absence, SSRF rejection, timestamp coercion, empty query bounds, and
normalization fallbacks. The fourth group exercises gossip convergence,
SearX model indexing, Redmine normalization, pipeline daemon adapters, and the
macOS Seatbelt backend. A test-first case found that an otherwise valid JSON
model-index cache with the wrong top-level shape raised during application
startup; the index owner now ignores non-object and non-entry values without
discarding valid entries or mutating the corrupt file. Another test-first case
found a real Opsgenie defect: epoch
microseconds and milliseconds were classified with thresholds too large for
contemporary Unix timestamps and could raise an out-of-range year error. The
owner now applies the documented microsecond/millisecond magnitude boundaries;
ISO and seconds behavior is unchanged.

The fifth group closes the Nagios, osquery, and Azure Boards hosted gaps at 97
percent aggregate coverage. It exercises the real default Nagios HTTP adapter
through an injected `httpx` client, total state coercion, malformed service
payloads, health error bodies, osquery runner and JSON failures, and Azure
Boards literal-host and transport failures. No monitoring binary, Azure client,
credential, daemon, or cleanup process is created: every external boundary is
injected, and invalid data remains observable before any remote write.

The sixth group closes regex safety analysis at 93 percent coverage. The
test-first regex case exposed that a quantified
non-capturing group nested inside another quantified group was not recognized
as dangerous. The analyzer now recursively inspects the bounded inner group;
escaped quantifiers and disjoint alternatives remain safe. No regex execution
timeout, warning suppression, or cleanup job was added. ASN.1 is intentionally
excluded from this core slice pending its collection-ownership migration.

This remains ZDD by candidate invalidation. No successful job prefix, retry, or
partial local attestation can make the failed SHA releasable. No daemon,
connector service, model process, or cleanup job is added by these tests; all
transports are injected and pytest owns the temporary coverage artifacts.
Rollback is split between the isolated policy commit and focused coverage
commits, while the untagged candidate remains unavailable to publication or
deployment until a replacement exact SHA passes both lanes.

The governing mature-tool and practitioner evidence remains the coverage.py
branch documentation and change history plus pytest-cov issues 578 and 608
linked in the canonical import section above. Those reports document the
long-lived subprocess and import-identity differences that make terminal hosted
coverage evidence necessary even after an extensive local pass.

### Canonical local interpreter and release-policy ownership

Candidate `202ee0c81c8b4c96c0649aec6fb9d459fbd59d9e` and hosted run
`33203067758` passed all 24 executable GitHub jobs, while the exact-SHA
dual-track verifier correctly rejected the pair. The hosted shard attestations
bound CPython 3.11 and the canonical `-W error` policy. The local producer had
used the checkout's CPython 3.14 environment and accepted a duplicate caller
policy, `-q -W error`, even though its Make target already supplies `-W error`.
The verifier's combined fingerprint diagnostic then mislabeled the policy-only
drift as a plan mismatch for every shard.

The canonical local target now selects CPython 3.11 explicitly and places its
managed toolchain in the project-namespaced sibling resource path
`<resource-root>-toolchain/ci-shards/python-3.11`; it never replaces the
checkout's shared `.venv` and it is not inside the disposable test-resource
tree.
Both the local target and hosted workflow opt into a fail-closed
`--require-release-policy` boundary before repository inspection, test launch,
or attestation write. Diagnostic runner invocations remain flexible because
they do not opt into that release-only boundary. The verifier reports plan and
policy drift separately, preserving the exact cause.

This is zero-downtime candidate invalidation: the green hosted run did not
permit a dry run, tag, publication, or deployment because the paired evidence
was invalid. A replacement immutable commit must produce both lanes again.
Rollback removes the isolated runner/Make/workflow contract commit and this
section; it does not change application data. The only added resource is one
namespaced, reusable Python 3.11 project environment beside Gludd's external
test-resource root. No daemon, port, model process, retry, warning suppression,
or cleanup task is introduced.

- [uv command reference](https://docs.astral.sh/uv/reference/cli/), reviewed
  2026-08-28, defines `uv run --python` as the interpreter request for the run
  environment and documents that uv manages the project environment.
- [uv issue 19563](https://github.com/astral-sh/uv/issues/19563), opened
  2026-05-26 and reviewed 2026-08-28, records a practitioner report where
  `uv run` recreated an active environment with the system interpreter despite
  a different local pin. Gludd therefore uses an explicit interpreter request
  and an external namespaced `UV_PROJECT_ENVIRONMENT` for release evidence.

### Nested project environment ownership incident (2026-08-28)

The first exact local replay reached integration batch 20 before its next
worker failed to import both `pytest` and `coverage`. The preceding dependency
management E2E had correctly used a temporary manifest, but its nested `uv add`
and `uv sync` inherited the release runner's absolute
`UV_PROJECT_ENVIRONMENT`. The nested project therefore synchronized the
runner's toolchain and removed packages that were extraneous to the temporary
manifest.

`DependencyManager` now binds every uv subprocess to the managed project's own
resolved `.venv`. Ambient project-environment selection cannot cross that
ownership boundary. The release runner additionally keeps its reusable Python
3.11 toolchain in a namespaced sibling of the disposable test-resource root.
This is ZDD: the failed immutable candidate remained unpublishable, the hosted
lane stayed available, and no live daemon or external model was restarted.
Rollback reverts the manager environment binding and sibling toolchain path;
the project-owned `.venv` and runner toolchain are disposable cache resources,
with no application-data migration.

- [uv project environment documentation](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path),
  reviewed 2026-08-28, says an absolute `UV_PROJECT_ENVIRONMENT` is used as-is
  and warns that `uv sync` removes extraneous packages by default.
- [uv issue 20060](https://github.com/astral-sh/uv/issues/20060), opened
  2026-06-30 and reviewed 2026-08-28, describes a practitioner whose exported
  project-environment variable propagates into child jobs and asks for a
  command-scoped alternative. Gludd therefore supplies a project-owned value
  on every child uv invocation rather than trusting ambient inheritance.
- [uv issue 19540](https://github.com/astral-sh/uv/issues/19540), opened
  2026-05-20 and reviewed 2026-08-28, records practitioner confusion about the
  different `VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT` command semantics. The
  Gludd contract uses only the documented project variable for uv project
  operations and pins it explicitly.

### Verifier-interpreter policy incident (2026-08-28)

Exact commit `4ff5102d19fa1b49a91b4ce74517a7227130b535` passed the
complete local eight-shard run at 93% aggregate coverage with every measured
file at or above 75%. Hosted run `33218338435` also completed successfully.
The paired verifier nevertheless rejected all nine attestations because it
constructed the expected release policy from its own CPython 3.14 process,
while both release lanes correctly attested CPython 3.11.

The runner now owns one canonical release-runtime policy: CPython 3.11,
warnings as errors, one owned pytest process, no nested xdist, and the shared
branch-coverage configuration. Attestations still record the actual producing
runtime, while `--require-release-policy` rejects any producer whose observed
runtime or pytest policy differs from that canonical contract. The verifier
imports the same canonical policy and is therefore independent of the Python
version used to execute the verification command.

This remains ZDD by immutable-candidate invalidation: two passing lanes were
not enough to permit a tag or deployment while their policy evidence could not
be verified. Rollback reverts only the policy helper and verifier binding; no
application data, daemon, model endpoint, or collection runtime changes. The
only resources are the already-owned local attestation and downloaded hosted
artifacts, both under the project-namespaced external resource root.

- [Python `sys.version_info` documentation](https://docs.python.org/3/library/sys.html#sys.version_info),
  reviewed 2026-08-28, defines the running interpreter's version tuple. The
  verifier must not use that ambient tuple as the expected release runtime.
- [uv issue 19563](https://github.com/astral-sh/uv/issues/19563), opened
  2026-05-26 and reviewed 2026-08-28, is the practitioner report already used
  above showing that an active tool environment can resolve a different
  interpreter than intended. Gludd therefore binds release Python explicitly
  and treats the verifier interpreter as an implementation detail.

### Nested xdist finalization incident (2026-08-29)

Exact commit `6d21b0c62b97713cf89e1ed3b626e57dd37a3a50` reached the
sixth batch of local shard `unit-1d` with every assertion passing. The nested
xdist worker then stopped producing output after the final bundled-binary test.
The owner emitted heartbeats for ten minutes, applied its bounded
`TERM`-to-`KILL` cleanup, returned 124, and prevented later batches and shards
from starting. The identical 16-file order exited in seconds on CPython 3.11
when coverage ran without the redundant xdist controller/worker layer.

Each batch is already a fresh subprocess with a unique temporary root, explicit
coverage database, progress deadline, and process-group cleanup. Running
`pytest -n 1` inside that owned process added an execnet pipe and a second
process without adding concurrency or isolation. The canonical local and hosted
commands now run that one pytest process directly. The attested policy records
`xdist_workers: 0`, `distribution: none`, and a null restart policy, so neither
lane nor the verifier can silently restore the nested lifecycle.

This remains zero-downtime candidate invalidation: the 124 result published no
passing attestation and cannot authorize a tag, artifact, or deployment. No
retry, harness cleanup, coverage exclusion, or warning suppression was added.
Rollback restores the runner, tests, and policy fingerprint together; it also
invalidates evidence created under either policy. Resource use decreases by one
controller/worker process and one execnet pipe per batch while retaining the
single owned pytest process and the existing bounded cleanup path.

- [pytest-xdist distribution documentation](https://pytest-xdist.readthedocs.io/en/latest/distribution.html),
  reviewed 2026-08-29, defines `-n` as creation of worker processes; one nested
  worker provides no parallel execution inside Gludd's already serialized batch.
- [pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313),
  opened 2026-03-24 and reviewed 2026-08-29, records a practitioner report of
  an intermittent hang after every test passed because execnet receiver threads
  remained blocked on dead-worker pipes; the report also records serial mode as
  the reliable boundary.
- [pytest-cov xdist support contract](https://github.com/pytest-dev/pytest-cov),
  reviewed 2026-08-29, confirms that xdist is optional coverage integration,
  not a prerequisite for subprocess coverage collection.

### Make jobserver inheritance incident (2026-08-29)

The first clean-commit gate replay advanced past the former `unit-1d` hang and
then exposed a second process-boundary defect in `unit-2:batch-030`. Two nested
`make -n help` checks timed out while the same nodes passed outside the serial
gate. The owned pytest child had inherited `MAKEFLAGS`, `MFLAGS`, `MAKELEVEL`,
`MAKEOVERRIDES`, and `GNUMAKEFLAGS` from the enclosing top-level Make process.
That made a Python-spawned Make process look like a recursive Make invocation
even though it was not launched through the repository's `$(MAKE)` contract.

Every owned pytest process now starts from a copy of its namespaced shard
environment with those recursion-only variables removed. Ordinary Gludd,
coverage, temporary-directory, and shard variables are preserved. Legitimate
recursive recipes still use `$(MAKE)` in the Makefile and therefore receive the
real parent jobserver directly; arbitrary test subprocesses cannot consume or
wait on that parent's slots.

This remains zero-downtime candidate invalidation: the timed-out gate published
no passing terminal attestation and could not authorize a tag, artifact, or
deployment. No retry, timeout increase, warning suppression, or test harness
cleanup was added. Rollback restores the environment helper and its regression
together and invalidates evidence from the changed runner. The resource effect
is bounded to one copied environment per owned pytest process and removes the
possibility that a child waits on a jobserver it does not own.

- [GNU Make recursive-use documentation](https://www.gnu.org/software/make/manual/html_node/Recursion.html),
  reviewed 2026-08-29, defines recursive Make as Make invoked by a Make recipe;
  Gludd's Python test subprocess is not that relationship.
- [GNU Make `MAKEFLAGS` and sub-Make documentation](https://www.gnu.org/software/make/manual/html_node/Options_002fRecursion.html),
  reviewed 2026-08-29, explains that `MAKEFLAGS` communicates options and the
  parallel jobserver to sub-Make processes, while `MFLAGS` is the historical
  compatibility variable.
- [GNU Make bug 62397](https://lists.gnu.org/archive/html/bug-make/2022-05/msg00000.html),
  reviewed 2026-08-29, is a practitioner report showing that Make launched
  through an indirect shell/function boundary can receive jobserver metadata
  without valid recursive-jobserver ownership.
- [Gentoo forum: slow Ninja when invoked incorrectly from Make](https://forums.gentoo.org/viewtopic-t-1110388-start-0.html),
  reviewed 2026-08-29, records the same operational shape: a non-recursive child
  receives jobserver arguments through `MAKEFLAGS` after Make closed the protocol
  descriptors it did not intend to delegate.

### Release worktree, exact-SHA, and ETA evidence (2026-08-29)

The final release decision has three independent identities: the filesystem that
produced local evidence, the immutable commit tested in both lanes, and the
hosted run attempt. A branch name, a green prefix, or an estimated completion
time cannot substitute for any of them.

| Signal | What it proves | What it cannot prove |
|---|---|---|
| Clean current and main worktrees plus the machine-readable worktree inventory | No tracked or untracked release input is hidden outside the candidate commit, and the release branch has one visible owner. | That any test passed or that a similarly named remote branch has the same SHA. |
| Local attestation commit | The canonical local plan and policy completed for one immutable object. | Hosted completion, even when a branch still points to that object. |
| Hosted job `head_sha`, run ID, and attempt | Which object and attempt produced each terminal GitHub result. | Equivalence to local evidence until the full SHA, plan, policy, and artifact digests match. |
| Queue state, start/completion timestamps, and elapsed time | Observable current state and measured duration so far. | A supported ETA; the documented API exposes status and timestamps but no completion estimate. |

Git deliberately refuses a second checkout when a branch is already used by a
linked worktree. A missing path may also retain administrative state; a locked
or dirty worktree cannot be removed normally. These safeguards prevent two
indexes from moving the same ref independently. Gludd therefore treats an
unexpected branch owner, lock, prunable registration, dirty current worktree, or
dirty main worktree as a release blocker. It must not bypass the condition with
`--ignore-other-worktrees`, double `--force`, direct `.git/worktrees` deletion,
or broad pruning.

The owner either finishes and commits its work, or cleanly unregisters and
removes that exact worktree before a fresh release worktree is created from the
candidate SHA. A stale missing-path record may be pruned only after the inventory
proves it has no active owner and is not locked. This is a ZDD control: resolving
checkout ownership changes no running service, while a dirty or ambiguous tree
cannot create a tag, artifact, or deployment. Rollback is to abandon the
untagged release worktree and retain the prior deployed artifact; evidence from
the abandoned filesystem is never reused.

The [official Git worktree documentation](https://git-scm.com/docs/git-worktree),
reviewed 2026-08-29, defines the refusal for an already checked-out branch,
machine-stable porcelain inventory, lock semantics, and pruning of missing
worktrees. A practitioner question from
[2017-01-09](https://stackoverflow.com/questions/41545293/branch-is-already-checked-out-at-other-location-in-git-worktrees),
still updated through 2024 and reviewed 2026-08-29, records the long-lived
`already checked out` blocker, stale metadata, forgotten rebases, and the advice
to prune only a worktree that was actually removed. The age and continued
updates show that this is an ownership invariant, not a transient Git defect.

For the CI pair, equality is over the full 40-character commit object. GitHub's
[`GITHUB_SHA` documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
and [event table](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
reviewed 2026-08-29, explicitly make SHA meaning event-dependent: a push or tag
identifies the pushed object, while a pull-request run normally identifies its
synthetic merge ref. The official
[`actions/checkout` contract](https://github.com/actions/checkout/blob/main/README.md),
reviewed the same day, accepts an explicit branch, tag, or SHA and documents the
separate pull-request-head checkout.

Accordingly, release evidence comes from the release push/tag candidate or an
explicitly selected candidate SHA. The local attestation SHA, workflow/run
`head_sha`, every hosted job `head_sha`, and checked-out `HEAD` must all equal it.
A re-run is a distinct attempt and its artifacts cannot be mixed with an earlier
attempt. GitHub Community question
[#25191](https://github.com/orgs/community/discussions/25191), opened
2020-03-19 and reviewed 2026-08-29, shows the persistent practitioner confusion
caused by comparing a PR head to its generated merge SHA. This is why branch
labels and abbreviated hashes are diagnostic only.

CI time is bounded but not predictable. The
[workflow-job REST schema](https://docs.github.com/en/rest/actions/workflow-jobs),
reviewed 2026-08-29, exposes `head_sha`, status, conclusion, start, and completion
timestamps but no ETA field; the absence of an ETA is an inference from that
documented schema, not a GitHub service guarantee. GitHub's
[Actions limits](https://docs.github.com/en/actions/reference/limits), reviewed
the same day, permit a hosted job to execute for up to six hours and a
self-hosted job to remain queued for 24 hours. Those ceilings are failure bounds,
not expected durations.

Gludd reports `queued`, `in_progress`, or terminal state, elapsed queue/runtime,
last observed progress, run ID, attempt, and candidate SHA. It must say “ETA
unknown” rather than extrapolate from prior batches or successful-job count.
Bounded heartbeats and polling continue while the candidate remains valid. If
either lane invalidates the SHA, the peer is cancelled and its owned process
tree, temporary root, and incomplete artifacts are reaped before replacement;
otherwise slowness alone does not authorize a retry, tag, or deployment.

The [GitHub concurrency contract](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
reviewed 2026-08-29, supports one running candidate and bounded pending work but
warns that actual start ordering is not guaranteed. Community discussion
[#160687](https://github.com/orgs/community/discussions/160687), opened
2024-10-21 with a 2026-07-20 follow-up and reviewed 2026-08-29, records a
six-hour provisioning stall and a dependent job queued for 24 hours. Gludd
therefore allows at most one local producer and one hosted run for the active
SHA, namespaces their evidence and temporary resources, and publishes only
after both exact-SHA terminal attestations pass. Rollback cancels the untagged
candidate and leaves the last deployed version serving traffic; no speculative
ETA or partial green prefix changes live state.

### Local-inference ownership incident (2026-08-29)

Release preparation found a Qwen llama.cpp server running directly as
`python -m llama_cpp.server` on port 9999 with parent PID 1. It had been started
as test support, was then reused by later tests as though it were operator-owned,
and was repeatedly described as “external.” No Gludd daemon existed in its
ancestor chain. This was a test-orchestration ownership defect and an evidence
labeling defect, not a daemon shutdown attempt that failed.

The process was terminated through Gludd's namespace- and identity-checked
cleanup path. Beta4 readiness now inventories llama.cpp processes and fails
closed when one has no Gludd-daemon ancestor, reporting its PID, parent PID, and
command. Normal tests use the hermetic endpoint; tests requiring a real GGUF use
managed mode, where `LocalInferenceManager` owns start, readiness, failure, and
`stop_all()` teardown. External mode is valid only when the operator explicitly
supplies a loopback URL; the harness must never create a server and then relabel
it external to avoid cleanup.

This extends the long-lived port-collision and cleanup evidence recorded in
`LOCAL_MODEL_TESTING.md`, including the
[llama-cpp-python port ownership report #1359](https://github.com/abetlen/llama-cpp-python/issues/1359),
reviewed again 2026-08-29. ZDD is preserved because readiness observation and
cleanup do not restart the daemon or modify a serving deployment. Rollback
removes the readiness rule together with its regression; it never revives an
orphaned model process. Resource use remains bounded to one process-table
snapshot and ancestor walk per release preflight.
