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

## Zero-downtime development

Candidate validation does not mutate the running Gludd service or the external
local-model endpoint. Mutable test resources live under the project namespace
returned by `scripts/resource_arbiter.py`; each shard gets an additional unique
batch namespace. The runner removes only the workspaces and coverage fragments it
owns. It does not stop externally owned services, including a user-started model
server.

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

## Upstream and practitioner evidence

Reviewed 2026-08-25:

- [GitHub documentation: rerunning workflows and jobs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs?tool=cli)
  confirms reruns use the original commit and ref.
- [GitHub Community discussion 27083](https://github.com/orgs/community/discussions/27083)
  documents the practical consequence that a rerun uses the workflow from the
  original SHA rather than a later fix.
- [GitHub Community discussion 17854](https://github.com/orgs/community/discussions/17854)
  reports artifact replacement and visibility surprises across reruns, supporting
  immutable, SHA-bound terminal evidence instead of artifact-name trust alone.
- [pytest-xdist issue 1323](https://github.com/pytest-dev/pytest-xdist/issues/1323)
  documents hangs and completed-work requeue behavior after worker restart.
- [pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313)
  documents controller hangs after a worker dies with retained pipes.
- [pytest-xdist issue 1278](https://github.com/pytest-dev/pytest-xdist/issues/1278)
  documents missed nonzero worker exits, supporting fail-closed controller parsing.
- [CPython issue 93852](https://github.com/python/cpython/issues/93852) records
  the long-lived practitioner failure where nested temporary paths exceed the
  107-byte Linux AF_UNIX limit.
- [CPython multiprocessing temp-directory implementation](https://github.com/python/cpython/blob/main/Lib/multiprocessing/util.py)
  documents the platform socket limits and the same short-system-temp fallback
  Gludd applies explicitly for supported Python versions.
- [GitHub Actions runner issue 3760](https://github.com/actions/runner/issues/3760)
  reports cross-runner state and ownership failures when runtime directories are
  shared, supporting Gludd's per-project, per-batch namespaces.
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
- [OpenCode plugin documentation](https://opencode.ai/docs/plugins/) documents
  the supported plugin-loading contract used by the singular manifest entry and
  the runtime hook acceptance test.
