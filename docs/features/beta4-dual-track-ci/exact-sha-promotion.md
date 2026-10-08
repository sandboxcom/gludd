# Exact-SHA promotion and release operations

This shard preserves the detailed promotion, publication, and hosted-CI operating record routed from [`BETA4_DUAL_TRACK_CI.md`](../BETA4_DUAL_TRACK_CI.md).

## Exact-SHA promotion contract (2026-08-31)

The beta4 release audit found that `AGENTS.md` and the behavioral specifications
named `make release-promote`, but the executable target was absent. The legacy
fallback checked out `master` inside the invoking worktree and used
`git merge --no-ff`; its structural test explicitly accepted a missing target.
The corrected target runs at the exact `development` tip, requires clean current
and canonical main worktrees, validates paired local/hosted evidence and release
readiness for that immutable SHA, proves that `master` is its ancestor, and moves
the canonical main checkout only with `git merge --ff-only development`.
Publication remains single-sourced through `release-cut`.

The validation-only path executes the same topology and policy checks with
network/ref mutation disabled. Tests pin missing/invalid input failure, exact
evidence-before-mutation ordering, canonical-main confinement, ff-only refusal,
side-effect-free validation, `release-cut` delegation, public help, and the
variable-aware Make contract. A second RED regression found that the static
contract parser dropped the final target at EOF; it now flushes that stanza
explicitly, so adding a real target at the end of the Makefile cannot be reported
as missing.

Upstream and practitioner evidence reviewed 2026-08-31:

- The official [Git merge documentation](https://git-scm.com/docs/git-merge)
  defines `--ff-only` as refusing a merge when the current tip is not an
  ancestor, which is the required branch-discipline primitive.
- GitHub Community discussion
  [#13226](https://github.com/orgs/community/discussions/13226) records that tags
  point to commits rather than branches.
- GitHub Community discussion
  [#45144](https://github.com/orgs/community/discussions/45144) documents the
  common tag-push release trigger, while
  [#196018](https://github.com/orgs/community/discussions/196018) records that
  branch and tag pushes can trigger distinct workflow runs. Gludd therefore
  binds the exact SHA before the fast-forward and leaves tag publication to one
  owner.

ZDD means the untagged candidate is rejected before `master), a remote ref, or a
release asset changes. The only long-lived resources consulted are the two Git
worktrees and immutable evidence files; no daemon, model server, or cleanup task
is started. Rollback before the fast-forward is the isolated promotion commit.
After a successful fast-forward, publication is idempotently resumed through
`release-cut`; history is never rewritten and the target never performs a
compensating reverse merge.

### Push-hook collection ownership (2026-08-31)

The first post-promotion push attempt passed formatting, JSON, secrets, workflow,
Ruff, and mypy, then terminated at the collection hook without moving the remote
SHA. The hook had bypassed Gludd's owned collection path by launching a second raw
`pytest tests/ --co` process. It therefore lacked the shared collection lock,
bounded diagnostics, and the same observable terminal contract already provided
by `make collect-check`.

The hook now delegates exactly to `make collect-check`. A RED structural
regression first pinned that owner relationship; the full pre-commit/config
surface then passed with warnings as errors. The pre-commit implementation
[prints a hook start marker before executing it](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/commands/run.py)
but buffers the child output until failure or verbose completion, so the delegated
target must own its own bounded result. Practitioner issue
[#2933](https://github.com/pre-commit/pre-commit/issues/2933), reviewed
2026-08-31, asks for durable per-hook timing/structured visibility, while issue
[#2069](https://github.com/pre-commit/pre-commit/issues/2069) records a long-lived
serial hook that appears stalled until interruption. The project's existing
`require_serial: true` remains, consistent with pre-commit's official changelog
guidance for non-parallel-safe hooks.

This path is ZDD: a failed hook leaves the remote and hosted pipeline unchanged.
The collection lock, one foreground pytest collector, and one bounded log file are
owned by `collect-check`; no daemon or cleanup task is introduced. Rollback is
the isolated hook/test/documentation commit, restoring the prior entry without
rewriting Git history or touching a release ref.

#### Push failure propagation and mutable secret evidence (2026-09-29)

The v0.1.1 development push exposed a distinct ownership edge after every earlier
pre-push phase had passed. `detect-secrets` refreshed line metadata in
`.secrets.baseline` and returned nonzero, so Git correctly rejected the push. The
`batch-push` shell recipe nevertheless continued, emitted a success message, and
persisted a successful verdict because the direct `git push` result was separated
from later commands only by semicolons.

`batch-push` now captures and returns the push failure before any success-only
side effect. Its regression requires the failure edge to occur before both the
operator message and `_record-push-verdict`. Baseline refresh remains an explicit,
reviewable commit followed by the read-only `secrets-scan` and live-secret
verification paths; a hook rewrite is never silently published.

This behavior is not Gludd-specific. detect-secrets issues
[#149](https://github.com/Yelp/detect-secrets/issues/149) and
[#212](https://github.com/Yelp/detect-secrets/issues/212) document baseline
metadata updates causing hook failure and requiring the baseline to be staged.
pre-commit issue
[#1489](https://github.com/pre-commit/pre-commit/issues/1489) and the upstream
runner implementation establish that a hook-modified worktree is a failure even
when the hook exits zero. Gludd therefore treats Git's exit status, not subsequent
recipe output, as the publication boundary.

The path remains ZDD: a failed push cannot advance the remote SHA, trigger hosted
CI, or write a success verdict. Rollback is the isolated Makefile, regression,
ledger, and documentation commit; it does not rewrite history or touch a tag,
artifact, deployment, or running service.

### Cleanup validation and apply parity (2026-08-31)

Stopping an invalidated local dual-track producer exposed a second operational
contract gap. `terminate-project-process-tree` validation accepted only the
shape of a positive PID and absolute namespace, while apply also inspected the
live process command. A supervisor whose command retained an unexpanded resource
root therefore passed validation and failed at apply time. The owned child was
stopped safely with its exact expanded namespace, but the two modes did not prove
the same precondition.

Validation now takes the same bounded process-table snapshot as apply, requires
the root PID to remain live, and requires the exact namespace to occur in that
root command before it reports success. It performs no signal operation. Tests
pin matching identity success, mismatched identity rejection, cross-project
exclusion, children-first apply, missing-process behavior, and idempotent signal
errors. Branch-aware coverage measures the cleanup owner directly rather than
relying on a broad gate to encounter it.

This is ZDD by construction: validation cannot terminate a process, and apply
continues to signal only the verified project tree. Failure leaves the candidate
invalidated and the remote, tag, release, and serving deployment unchanged.
Rollback is the isolated cleanup/test/coverage-config/documentation commit; no
compensating cleanup task or background service is introduced.

### Host disk-pressure determinism (2026-08-31)

The exact local dual-track producer for beta4 exposed two database admission tests
that used `tmp_path` while still reading the host filesystem's live capacity. The
volume was legitimately 99% full, so production correctly returned `CRITICAL`;
the tests incorrectly assumed the result could only be `OK` or `WARNING`. Hosted
CI passed on a roomier runner, creating the precise local/hosted divergence that
dual-track validation is intended to reveal.

The tests now replace `shutil.disk_usage` with explicit total, used, and free byte
snapshots for every status assertion. They pin `OK`, configured blocking,
nonexistent-path, environment fallback, reported byte counts, and real 99.5%
critical-pressure behavior. Production remains fail closed and no threshold,
retry, skip, or warning suppression changed.

Evidence reviewed 2026-08-31:

- [pytest temporary-path documentation](https://docs.pytest.org/en/8.2.x/how-to/tmp_path.html)
  guarantees a unique test directory, not virtualized disk capacity.
- [CPython issue 35458](https://bugs.python.org/issue35458) records a long-lived
  practitioner report that live `shutil.disk_usage` assertions fail randomly when
  tests run concurrently.
- [GitHub Actions runner-images issue 9344](https://github.com/actions/runner-images/issues/9344)
  records hosted-runner regressions caused by unexpectedly exhausted disk.

This repair is ZDD: synthetic capacity exists only inside each test, while the
application continues to observe and reject actual critical disk pressure. The
only owned resources are pytest's monkeypatch rollback and its namespaced
temporary directory. Rollback is the isolated test/documentation commit; it does
not mutate the release tag, remote, database, or host filesystem policy.

### Stable release-tag readiness (2026-09-16)

The readiness checker now supports `v0.1.1` as the first stable release tag while
retaining `v0.1.0-beta.4`. Syntax and release support remain separate fail-closed
boundaries. The syntax boundary accepts only the complete forms
`vMAJOR.MINOR.PATCH` and `vMAJOR.MINOR.PATCH-beta.N`; each numeric identifier is
either `0` or begins with `1` through `9`. The support boundary then requires the
exact tag to have a release-task mapping. A canonical but unmapped future tag is
therefore still rejected rather than silently borrowing another release's
evidence.

The stable release is bound to the exact ledger declarations S83.157 through
S83.168. The checker rejects a missing or partial task set, even if every beta4
task is complete, and reports every member that is not effectively complete.
A checked marker cannot close a task whose explicit status remains `pending` or
`in_progress`, while an unchecked task always remains open. S83.166 is the
terminal publish/promotion action, so it must be declared but is excluded
from the pre-publication readiness cycle just as beta4's terminal release action
is. Post-publication verification remains responsible for completing it.

Those tasks treat self-improvement as the primary acceptance workload rather
than the platform boundary. Accelerator inventory, model sizing, provider
routing, compute lifecycle, execution, evidence, and generic evaluation belong
to reusable Gludd capabilities; self-improvement supplies only its proposal,
comparison, acceptance, promotion, and learning policy.

Alpha, release-candidate, build-metadata, abbreviated, leading-zero, whitespace,
path-shaped, and command-shaped values remain invalid. This preserves beta
validation instead of broadening the accepted prerelease family while making the
stable form available to the same exact-SHA, ledger, worktree, version, resource,
and CI checks.

Practitioner evidence reviewed 2026-09-16:

- [GitHub Community discussion #26603](https://github.com/orgs/community/discussions/26603),
  opened in 2021 with a 2024 follow-up, records that release events do not apply
  tag filters and require an explicit validation step. Gludd therefore validates
  the whole tag before collecting or acting on release evidence.
- [SemVer issue #583](https://github.com/semver/semver/issues/583), opened in
  2020, records the persistent ambiguity around numeric prerelease identifiers:
  zero is valid, while multi-digit identifiers beginning with zero are not. The
  checker encodes that distinction directly for core numbers and `beta.N`.
- The authoritative [Semantic Versioning 2.0.0 specification](https://semver.org/)
  supplies the same no-leading-zero rule. Gludd deliberately supports only its
  stable form and the already-owned beta form, not every SemVer prerelease.

This change is ZDD by construction: validation performs no deployment, tag, or
branch mutation, and the stable tag must pass the existing immutable evidence
path before publication. It starts no process, service, or cleanup task. Rollback
is the isolated checker, test, and documentation commit; the previously supported
beta tag and its task mapping remain unchanged.

### Exact-SHA tag validation reuse (2026-09-27)

The tag workflow previously repeated five heavyweight validation families after
the identical development commit had already completed them: the two-version
FreeLLMAPI upstream build, eight canonical coverage shards, coverage aggregation,
four Molecule shards, and game-building validation. GitHub treats a branch push
and a tag push as separate events, so this was a second execution rather than a
continuation of the already-green run.

The tag workflow now has a bounded `release_source_proof` job. It checks out the
immutable event SHA and invokes the existing fail-closed CI verdict helper with
both the full `GITHUB_SHA` and the explicit `development` branch. The helper now
queries the exact commit and `Build and Release` workflow without GitHub's
server-side branch filter, requests `headBranch` and `event`, then locally requires
the full SHA, bare `development` ref name, workflow name, and `push` event before
selecting the newest run. Only terminal `success` prints the source run ID.
Missing identity fields, pending, cancelled, skipped, failed, differently named,
or differently addressed runs cannot authorize reuse.

Only validations already covered by that complete source workflow are skipped in
the tag run. The two-version gate still executes on the tag's exact checkout.
Linux, macOS, Windows, Termux, container, and Ansible execution-environment jobs
still rebuild and smoke the release-versioned artifacts. The release fan-in still
generates the SBOM, wheel and sdist, checksums, source-SHA manifest, complete asset
matrix, published-release verification, downloadable binary smoke, and rollback
evidence. Its condition enumerates every direct dependency result: source proof,
gate, and artifact producers must be `success`, while only the five reused job
families may be `skipped`. A cancellation, new dependency state, or condition
drift remains fail closed.

This changes the configured tag critical-path ceiling before publication from the
120-minute test-shard lane to the 45-minute tag-specific artifact lane, a bounded
75-minute reduction. It also avoids up to 16 duplicate hosted jobs per release
(two upstream legs, eight test shards, one coverage aggregation, four Molecule
shards, and one game job). These are configuration-bound ceilings, not an
assertion that every run consumes its entire timeout; hosted timing remains
observable in the source and tag workflow records.

Practitioner and platform evidence reviewed 2026-09-27:

- [GitHub Community discussion #27031](https://github.com/orgs/community/discussions/27031),
  opened 2021-07-02, records the long-lived branch-then-tag duplicate-run problem
  for one commit and recommends an exact-commit pre-job before skipping duplicate
  work. Gludd uses its owned verdict helper rather than adding an unpinned generic
  skip action.
- [GitHub Community discussion #44396](https://github.com/orgs/community/discussions/44396),
  opened 2023-01-15, reports duplicate compile, deploy, and release work for the
  same branch/tag SHA and explains why concurrency cancellation is not a safe
  substitute. Gludd preserves separate SHA-scoped runs and reuses only an already
  terminal source result.
- GitHub's [workflow trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
  states that multiple triggering events create multiple workflow runs. Its
  [job dependency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)
  documents skipped dependency propagation, and the
  [contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context)
  defines the `success`, `failure`, `cancelled`, and `skipped` results used by the
  explicit release fan-in.
- The official [`gh run list` reference](https://cli.github.com/manual/gh_run_list)
  documents the commit and workflow filters plus the `headBranch`, `headSha`,
  `event`, and `workflowName` JSON fields. The long-lived empty-result failures in
  [GitHub CLI issue #5474](https://github.com/cli/cli/issues/5474) and
  [GitHub Community discussion #24626](https://github.com/orgs/community/discussions/24626)
  make server-side branch filtering unsuitable as release evidence; Gludd reduces
  the query by immutable commit/workflow and repeats every authorization check
  locally.

The rollout is ZDD: no running Gludd service, database, tag, or published artifact
is changed by the proof job. Old and new application workers are unaffected, and
an invalid proof stops before publication. Rollback is one workflow/test/doc
revert; the next tag returns to rerunning every heavy validation while the prior
release and its immutable artifacts remain available.

### Exact-identity release wait (2026-09-27)

The publication commands previously waited for the release artifact with fixed
retry loops. `release-recut` stopped after about five minutes and `release-cut`
stopped after about ten, even though the workflow itself documents a 30–60 minute
cold build. This created a false operator failure while a healthy release was
still queued or building. The loops also asked only whether an artifact existed;
they did not first prove that the terminal workflow belonged to the new tag, its
full commit SHA, the `Build and Release` workflow, and the `push` event.

Both publication paths now snapshot the newest matching workflow run before tag
mutation, then delegate to the existing `ci-await` owner with that baseline and
four-part identity. Re-cuts therefore cannot mistake the prior failed or green
run for the new tag push. Exact-SHA lookup deliberately omits GitHub's server-side
branch filter, requests up to 50 commit matches, and locally requires the tag ref,
SHA, workflow, event, and a run ID newer than the snapshot before choosing the
newest run. GitHub reports `headBranch` using the bare tag name (`v0.1.1`), not
the full Git ref (`refs/tags/v0.1.1`), so the selector and its regression fixture
use that API representation. Every lookup emits a heartbeat. Success requires
`completed/success`; cancelled, failed, skipped,
neutral, stale, action-required, startup-failure, timed-out, or an unknown
completed conclusion fails closed. Lookup errors and an absent run retry within
one bounded 90-minute window, after which timeout remains non-success.

The poll interval falls from 60 seconds to 10 seconds. This cuts average discovery latency
from at most 60 seconds to at most 10 seconds (and, under a uniform event arrival
assumption, from 30 seconds to 5 seconds). The timeout ceiling rises from
5–10 minutes to 90 minutes so it covers the existing 60-minute cold-build budget
plus queue margin. This does not weaken the final checks: only after the exact run
is green do `verify-release-artifact` and `verify-release-completeness` inspect
the immutable published matrix.

Practitioner and platform evidence reviewed 2026-09-27:

- [GitHub CLI issue #5474](https://github.com/cli/cli/issues/5474), opened
  2022-04-17, records `gh run list --branch` returning empty JSON and identifies
  local `headBranch` filtering as the working remedy. Gludd uses commit filtering
  for query reduction and repeats all identity checks locally.
- [GitHub Community discussion #24626](https://github.com/orgs/community/discussions/24626),
  opened 2021-03-01 with years of follow-up, reports the Actions workflow-run API
  returning no results for branch-filtered queries even when runs are visible.
  An empty query result is therefore pending evidence, never release permission.
- [GitHub Community discussion #5673](https://github.com/orgs/community/discussions/5673),
  opened 2021-09-16 and active through 2026, documents persistent demand for
  bounded, configurable deployment waits as queued approvals and runner capacity
  can outlast short client timeouts. Gludd owns an explicit bounded timeout rather
  than treating one short retry loop as platform truth.
- [GitHub Community discussion #158805](https://github.com/orgs/community/discussions/158805)
  records an observed tag-triggered workflow payload in which `head_branch`
  contains the triggering job's `ref_name`: a bare tag name for a tag and a bare
  branch name for a branch, with no full ref or ref type. Exact wait therefore
  compares the bare tag name and rejects an assumed `refs/tags/...` value.
- The official [`gh run list` manual](https://cli.github.com/manual/gh_run_list)
  defines the commit, branch, workflow, event, limit, and JSON fields used here.
  No new polling dependency or custom API client was introduced.

This is ZDD by construction: waiting and verification mutate no application,
database, service, or release asset. A failed or timed-out exact run stops before
any success claim; previously published releases keep serving. Rollback is one
script/Makefile/test/doc commit and restores the shorter loops without changing a
tag, artifact, or deployment.

### Complete gate preflights and ephemeral Molecule dependencies (2026-09-29)

The first real exact-SHA `binary_smoke_linux` replay completed successfully: it
built the reviewed aarch64 ELF, exercised the packaged CLI and daemon, submitted a
job, verified failure paths, and tore the service down. It also exposed an
ownership defect. Galaxy had installed 929 third-party Ansible and Community
files beneath Gludd's project `collections/` directory. The following gate then
rejected those paths during task registration, as it should.

The dependency path listed the writable project collection root before the
Molecule run's ephemeral collection root. Ansible Galaxy deliberately installs
into the first configured collection path; a lookup order therefore became a
write destination. The order is now ephemeral dependencies first and Gludd's
owned collection second. The scenario's existing trap removes the ephemeral root
on success or failure, while project code remains resolvable without accepting
generated vendor content into source ownership.

That gate exposed a second process defect: its independent checks were ordinary
Make prerequisites. The first failure prevented the gate recipe, later
preflights, and every subsequent phase from running. `--keep-going` can continue
independent siblings but still cannot execute a target whose prerequisite failed;
`--ignore-errors` would continue by discarding the failure truth. Neither option
implements a trustworthy complete verdict.

The gate now owns an explicit preflight ledger. It runs all named checks in a
stable order, streams every start and result, retains one PASS/FAIL row per check,
makes any failure sticky in the final status, and continues through all remaining
gate phases. An executable pass/fail/pass fixture proves that a middle failure is
retained while the later check still runs. This converts a build attempt from a
one-error-at-a-time loop into a complete repair set without weakening the final
nonzero result.

Practitioner and implementation evidence reviewed 2026-09-29:

- Ansible's [`galaxy.py`](https://github.com/ansible/ansible/blob/devel/lib/ansible/cli/galaxy.py)
  selects the first collection path as the installation destination.
- Ansible issues [#68621](https://github.com/ansible/ansible/issues/68621) and
  [#72628](https://github.com/ansible/ansible/issues/72628) document long-lived
  confusion and unusable collection installs caused by path selection.
- Molecule issue [#3999](https://github.com/ansible/molecule/issues/3999)
  recommends explicitly owning `ANSIBLE_COLLECTIONS_PATH` when generated
  configuration does not provide the required project and dependency paths.
- The Stack Overflow discussions on
  [`--ignore-errors` versus `--keep-going`](https://stackoverflow.com/questions/53039550/makefile-ignore-errors-vs-keep-going)
  and [target-local continue-on-error policy](https://stackoverflow.com/questions/53760185/define-continue-if-error-policy-directly-in-target-dependencies)
  show why global flags cannot both retain failures and guarantee the parent
  recipe executes.

Both repairs preserve ZDD. Molecule dependencies live only in a run-scoped path
and are removed by the owner without touching an installed Gludd service. Gate
aggregation changes validation control flow only: it performs read-only checks,
retains their complete evidence, and still fails before publication whenever any
check is red.

### Diagnostic artifacts are not release assets (2026-09-29)

The first complete exact-SHA gate exposed structural tests that treated every
`actions/upload-artifact` step as a publishable binary. That assumption would
force failure evidence to disappear: the Linux PyInstaller warning graph is
intentionally uploaded with `if: always()` before binary and daemon smoke tests,
while the distributable archive is uploaded only with `if: success()` after all
smoke and packaging checks pass.

Artifact admission is now explicit. Names beginning with `gludd-*` are release
assets and must remain success-gated behind smoke tests; other names are
diagnostics and must survive failures without entering the release job's
`gludd-*` download fan-in. Gate-wiring tests likewise inspect the canonical
`GATE_PREFLIGHT_TARGETS` list and the non-short-circuit runner instead of the old
direct-prerequisite header. The Molecule action allowlist also includes the
pinned Node 24 `upload-artifact` action used for retained scenario evidence.

Practitioner and implementation evidence reviewed 2026-09-29:

- The official [upload-artifact metadata](https://github.com/actions/upload-artifact/blob/main/action.yml)
  declares its Node 24 runtime, while GitHub's
  [always() documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
  explicitly describes retaining logs after failure.
- Long-running reports [actions/upload-artifact#585](https://github.com/actions/upload-artifact/issues/585)
  and [#328](https://github.com/actions/upload-artifact/issues/328) show why
  failure diagnostics need explicit unconditional upload behavior.
- GitHub Community discussion
  [#206753](https://github.com/orgs/community/discussions/206753) records the
  important boundary between workflow artifacts and release assets: release jobs
  must explicitly download and republish the artifacts they admit.
- Runner issue [#4295](https://github.com/actions/runner/issues/4295) and the
  runner's [Node action guidance](https://github.com/actions/runner/blob/main/docs/checks/nodejs.md)
  document the long-lived runtime-version ambiguity avoided by pinning actions
  whose metadata natively selects Node 24.

This is ZDD by construction. Diagnostic retention and test classification do not
modify a running service or published release. A rollback removes one test/doc
commit; the success-gated release uploads and previously published artifacts stay
unchanged throughout.

### Durable plugin state is explicit test input (2026-09-29)

The next complete candidate gate reduced the repair surface to one failure. The
delegate streak E2E gave its streak counter and disengage signal function-scoped
paths, but left the newly durable dispatch ownership ledger at the repository
default. A synthetic `task` with prompt `do work` therefore found a legitimate
owner from an earlier process and was denied before the streak-reset assertion
could execute.

The test now binds `GLUDD_DISPATCH_DEDUP_STATE` to the same function-scoped
temporary root as its other state. The three synthetic tool variants still share
one ledger within that test, but no run reads or mutates live project ownership.
This is isolation, not a deduplication bypass: the separate runtime contract still
proves exact-prompt and tracked-task collisions, lock ownership, retry after a
failed dispatch, and permanent denial after completion.

Practitioner and implementation evidence reviewed 2026-09-29:

- Pytest issue [#11790](https://github.com/pytest-dev/pytest/issues/11790)
  documents collisions when a supposedly unique temporary boundary is reused by
  concurrent invocations.
- The pytest-xdist discussion
  [#1213](https://github.com/pytest-dev/pytest-xdist/discussions/1213) recommends
  temporary files with file locking when cross-worker state is deliberately
  shared.
- Pytest's
  [temporary-path documentation](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.rst)
  defines `tmp_path` as function-scoped; that isolation applies only to resources
  actually rooted there.

The full gate ledger contained exactly one `SHARD-FAIL`; the repaired node passes
1/1 and the combined delegate/dedup replay passes 21/21. No running service,
published artifact, or live dispatch owner changes, so the repair is ZDD and its
rollback is the single test-state binding.
