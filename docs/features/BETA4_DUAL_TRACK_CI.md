# Beta 4 Dual-Track CI Evidence

Status: implemented for the `v0.1.0-beta.4` candidate pipeline on 2026-08-25.

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
runner's `DEFAULT_SHARDS` registry, bounded batch planner, single xdist worker,
heartbeats, cleanup, aggregate coverage, and terminal writer are the sole source
of execution truth. `DUAL_TRACK_LOCAL_VALIDATE_ONLY=1` is the read-only Make
contract and never creates a successful attestation.

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

## Upstream and practitioner evidence

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
