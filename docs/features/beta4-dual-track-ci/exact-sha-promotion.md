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

#### Current remote evidence before a v0.1.2 push (2026-10-12)

A real guarded development push exposed an unset `PUSH_REMOTE`. The old guard
ignored fetch errors, treated a non-resolving ref as zero divergence, and printed
PASS. `PUSH_REMOTE` now defaults to `sandboxcom`; the guard uses the same configured
SSH key as the normal push and rejects detached HEAD or an unconfigured remote.

The existing Git CLI provides the admission evidence. A successful exact-branch
lookup precedes an explicit branch-to-tracking-ref fetch. Lookup or fetch errors
cannot reuse cached refs. A confirmed absent remote branch may be created through
a normal push; every existing remote branch must have zero commits missing from
the local candidate. `FORCE=1` cannot bypass divergence. Reconciliation remains
merge-forward, never a shared-branch rebase. The actual push still provides the
final non-fast-forward race guard; this check neither merges nor publishes.

Local bare-remote regressions exercise default and explicit remotes, local-only
work, remote advance, divergence, unavailable remotes, fetch failure after a
successful lookup, new branches, detached HEAD, and a non-mutating Make dry run.
No validation-only variable is added to the live push path. Preview the recipe:

```text
make _pull-before-push-guard -n PUSH_REMOTE=sandboxcom SSH_KEY=/tmp/gludd-contract-absent-key
```

The preview is not live remote admission. The live guard refreshes only the
selected tracking ref; it changes no serving process, release tag, artifact,
remote ref, or local HEAD, so the path remains ZDD. Rollback is an isolated
Make/contract/test/documentation commit without rewriting shared history.

[Git's fetch reference](https://git-scm.com/docs/git-fetch.html) documents explicit
source-to-destination refspecs, and its
[remote lookup reference](https://git-scm.com/docs/git-ls-remote) distinguishes
confirmed absence (exit 2) from transport failure. A
[2015 practitioner report](https://stackoverflow.com/questions/29527630/why-doesnt-git-update-my-remote-tracking-branch),
reviewed 2026-10-12, records remote-tracking state remaining stale after a fetch.
That configuration-dependent case is not this unset-variable defect; it supports
checking the actual remote and explicit ref mapping instead of trusting cached
ahead/behind output.

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

The 2026-10-12 piped reproduction exposes a separate observability gap: the
default progress callback prints heartbeats but leaves them buffered until
process exit or interruption. The callback now uses the standard-library
`functools.partial(print, flush=True)`, so startup is visible before the first
remote lookup, each heartbeat is visible before sleeping, and terminal output
is visible before returning. Two failing-first pipe regressions prove that
ordering, with all original selectors, deadlines, exit codes, and injectable
callbacks unchanged. The 39-case exact/codification cohort passes; the full
33-case producer suite passes at 97% targeted coverage.

This follows Python's documented
[noninteractive stdout buffering](https://docs.python.org/3/library/sys.html#sys.stdout)
and the long-lived practitioner discussion
[Disable output buffering](https://stackoverflow.com/questions/107705/disable-output-buffering).
The change adds no runner, service, process, environment, listener, or schema;
rollback restores only callback construction after an active waiter exits.

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

### Success-only historical failure resolution (2026-10-12)

The CI failure ledger now distinguishes a job that supplies repair proof from a
job that supplies no new failure. Only an actual `success` for the same branch,
workflow, and job closes a historical failure family. A `skipped` or `neutral`
job remains a non-failure, adds no new family, and leaves existing `open` or
`repaired` ownership intact. A successful job may close its own family even when
another job in that workflow failed; the sibling failures remain independently
owned. Aggregate workflow status and merge eligibility cannot substitute for
the missing job proof.

Observation also reconciles legacy families that the previous shared conclusion
set incorrectly marked resolved. Correction requires a stored terminal run with
the matching run ID, SHA, branch, workflow, and job, a valid unchanged payload
fingerprint, and a `skipped` or `neutral` job conclusion. Attempt-specific
receipts select that attempt; historical receipts use their original observation
timestamp when it identifies the attempt. Without that identity, every matching
attempt must prove non-success. Missing, conflicting, or ambiguous evidence
preserves the existing projection, including a genuine successful resolution.
New resolutions from later attempts retain their attempt number explicitly.

A proven false resolution returns to `repaired` when its repair receipt is still
retained, or to `open` otherwise. Only the false resolution markers are removed:
immutable run records and fingerprints, failure occurrences, repair receipts,
and successful sibling resolutions remain unchanged. Re-observing identical
terminal input reports `reconciled` once, persists that correction through the
existing atomic ledger writer, and then reports `unchanged`. Conflicting input
for a recorded run attempt fails before any reconciliation mutation.

Repair input also requires its full lowercase 40-character SHA before starting
the Make evidence command. Three failing-first regressions cover abbreviated,
uppercase and overlong hashes: each fails without launching evidence or changing
ledger bytes. Invalid caller input cannot consume a test/gate run before refusal.

Practitioner and platform evidence reviewed 2026-10-12:

- [GitHub Community discussion #52082](https://github.com/orgs/community/discussions/52082),
  opened 2023-04-05, reports the exact failure-then-skip sequence: a required
  check fails, a later conditional run skips it, and the merge UI reports that
  checks passed although the job never succeeded. The ledger requires actual
  job success before closing the recorded failure.
- [GitHub Community discussion #28864](https://github.com/orgs/community/discussions/28864),
  opened 2022-08-09 with follow-ups through 2026, documents automerge admitting
  skipped required jobs after dependency failures. Its practitioners require
  explicit dependency-result checks; Gludd retains each missing proof family.
- [GitHub Community discussion #24754](https://github.com/orgs/community/discussions/24754),
  opened 2019-01-15 with later follow-ups, records the long-lived ambiguity
  between neutral check conclusions and successful validation.
- GitHub's [status-check reference](https://docs.github.com/en/pull-requests/reference/status-checks)
  explicitly distinguishes `success`, `skipped`, and `neutral`, while treating
  the latter two as successful for dependent checks. That platform dependency
  policy does not establish that a previously failing Gludd job ran and passed.

The change preserves ZDD: observation and atomic operational-state correction
start no service, environment, runner, listener, or additional proof owner, and
modify no application, database, deployment, or published release. Rollback is
the isolated script/test/documentation change; retained immutable CI observations
remain available and all release, coverage, and promotion gates still apply.

### Quoted repair-evidence variables (2026-10-12)

The normal repair path exposed a transport failure while assembling native
evidence for hosted unit-2. A quoted two-file `TESTFILES` value passed through
GNU Make's `foreach` as separate words. The recipe inserted `--evidence-var`
inside that value; pytest rejected the resulting command and no receipt was
recorded. This was a failed proof producer, not successful validation.

`ci-failure-repair` now exports its existing `CI_REPAIR_EVIDENCE_VARS` input and
selects the CLI's explicit `--evidence-vars-from-env` mode. The existing Python
`shlex.split` parses only that fixed environment key; evidence execution remains
an argument list passed to `subprocess.run` without a shell. Quoted multi-file
values and nested pytest option quotes therefore survive as complete Make
assignments, without recipe-shell interpolation or a custom parser. Subsequent
Make commands retain their normal variable semantics; this is not a change to
Make's filename grammar.

Full lowercase repair-SHA and plain-target validation still precede evidence.
Malformed quoting or a non-`UPPER_CASE=value` assignment returns an error before
subprocess execution or ledger writes. Direct callers retain repeated
`--evidence-var` arguments, and ambient environment input is ignored unless
explicitly selected. Only successful native execution can write a repaired
receipt; hosted resolution still requires an executed successful replacement
job. Neither path claims the complete release gate is green.

Eight new cases preserve quote/metacharacter values as data, exact multi-file
and pytest arguments, legacy opt-in isolation, SHA priority, and failed-input
atomicity. A harmless real-Make probe exercises an explicitly exported value
defined in a second Makefile and cannot run evidence or create a ledger. Six
cases fail first while 61 pass; after repair all 67 ledger cases pass at 92.48%
combined coverage (93.89% line, 89.10% branch), above unchanged 85/75 floors.
Three additional mapping/roster/wiring cases fail first. The new
`ci-repair-evidence-variable-forwarding` family routes that probe and the complete
ledger cohort through the existing warning-fatal one-worker early owner, with
unchanged 90/60-second bounds, 21 phases, a 2850-second ceiling and mandatory
canonical full-gate replay.

The complete related acceptance slice passes 100 cases. The actual early owner
executes 431 cases in 30.84 seconds (31.6 observed), with all 32 promoted families
validated. The public safe behavioral example, all 215 contracts, strict
source/test typing, Ruff, Make lint and the same 250 ownership entries pass.
Actual collection selects 122322 of 122323 tests with one intentional deselection
and zero errors.

Evidence reviewed 2026-10-12:

- GNU Make's [foreach documentation](https://www.gnu.org/software/make/manual/make.html#Foreach-Function)
  defines iteration over whitespace-separated words, not shell-quoted arguments.
- Python's [shlex documentation](https://docs.python.org/3/library/shlex.html#shlex.split)
  supplies the maintained quoted-string parser and recommends argument-list
  subprocess calls when avoiding shell interpretation.
- The February 2017 [practitioner report](https://stackoverflow.com/questions/42407052/foreach-python-file-in-subdirectory)
  reproduces repeated `foreach` expansions for a quoted path containing spaces.
  The same word-versus-argument distinction explains the repair transport fault;
  the fix reuses standard facilities rather than adding another tokenizer.

This is a control-plane repair. It starts no serving process, environment,
container, package installation or deployment and preserves ZDD. Rollback is the
isolated Make/CLI/test/documentation change; immutable observations and the
existing atomic ledger remain intact. Current source review, exact-SHA hosted
success and canonical gate/coverage remain required, and disk admission is not
bypassed.

### Molecule groups preserve push SHA identity (2026-10-23)

The standalone Molecule workflow grouped runs by workflow, full ref and event.
Different push SHAs on one branch therefore shared a pending group. GitHub's
default pending-run replacement still applies when `cancel-in-progress` is false.
Molecule now uses Build's existing conditional SHA selection while retaining
its full-ref/event namespace:

```yaml
group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.sha }}-${{ github.ref }}-${{ github.event_name }}
cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

Distinct development/candidate push SHAs and dispatch SHAs resolve separately;
updates to the same PR still coalesce. Different PRs, refs and event types stay
separate, while repeated identical source retains one group. The six shards,
`max-parallel: 3`, `fail-fast: false`, 45-minute job timeout, read permission, triggers
and native command remain intact. No queue setting or capacity bound increases.

The [primary concurrency manual](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
and [Community discussion #5435](https://github.com/orgs/community/discussions/5435),
opened 2021-09-02, document the long-lived pending-run surprise.
The [retained research record](../../RELEASE_READINESS.md#long-lived-github-actions-artifact-failures)
explains why disabling active cancellation alone cannot preserve queued commits.
This packet uses that record and Root's fixed scope of 14 source bodies; it adds no
public retrieval or source expansion. These sources establish the generic
failure mode, without attributing a particular hosted cancellation or speedup.

Patched isolated acceptance is based on
`14bca32a9a4524472df9d09739163450a0514524` in
`/private/tmp/gludd-worktrees/agent-v012-gha-build-phase`.
The three new source-contract methods in
`tests/unit/test_release_pipeline_structure.py` consume actual YAML, retain all
predecessor assertions, render only the pinned literal grammar, and cover eight
context pairs plus exact expression/cancellation and native-policy checks.
They supply no GitHub server scheduling evidence. Receipts below are relative
to that worktree; all exact sessions are terminal and drained.

| Scope | Pytest result | Native elapsed/status | Receipt |
| --- | --- | --- | --- |
| RED 3.11 | 4 FAIL / 6 PASS, 0.81s | 1.895s / exit2 | `.gate-logs/observed/v012-molecule-concurrency-red/20261023T201328Z-64625-86828f41.json` |
| Narrow GREEN 3.11 | 10 PASS, 0.52s | 1.361s / exit0 | `.gate-logs/observed/v012-molecule-concurrency-green311/20261023T201350Z-65039-b62df421.json` |
| Complete 3.11 | 257 PASS, 10.76s | 11.601s / exit0 | `.gate-logs/observed/v012-molecule-concurrency-union311/20261023T201405Z-65863-14ed4e18.json` |
| Complete 3.12 | 257 PASS, 12.85s | 14.104s / exit0 | `.gate-logs/observed/v012-molecule-concurrency-union312/20261023T201433Z-68113-e7fd0332.json` |

The RED failures are the literal source pin and distinct development push,
candidate push and dispatch SHAs. Six prior-behavior controls already pass.
The complete eight-owner union retains:

- `tests/unit/test_release_pipeline_structure.py`
- `tests/unit/test_concurrency_group_formula.py`
- `tests/unit/test_ci_gate_matrix_full.py`
- `tests/unit/test_github_actions_deep.py`
- `tests/unit/test_workflow_edge_cases_deep.py`
- `tests/unit/test_ci_hosted_environment_contract.py`
- `tests/unit/test_pytest_param_fixture_collisions.py`
- `tests/integration/test_molecule_ci_e2e.py`

All watched acceptance uses one pytest process (`-n 0`), fatal warnings, literal `ID=`, installed
3.11/3.12 selection, `RUN_WATCHED_PYTEST_TRACE=1`, project-private observations and the original
90s total / 60s quiet bounds.
Ruff, strict changed-owner typing, test-environment guard and native line limits
pass. These are configuration/source checks; no new aggregate coverage
measurement or hosted timing result follows.

This control-plane repair preserves ZDD and 85% aggregate / 75% per-file line AND
branch floors. Root's packet inspection supplies source acceptance only.
Normal commit collection, independent candidate review, receiving acceptance,
canonical full gate, exact-SHA push CI and immutable hosted timing remain pending.
A failed receipt remains history; neither this source packet nor a later
handoff closes S98.4 or authorizes promotion.

### Diagnostic reruns with changed logging (2026-10-23)

Diagnostic CI can run before release-candidate readiness. The existing guarded
`ci-rerun` owner now accepts literal `CI_RERUN_DEBUG=0|1` and
`CI_RERUN_FAILED_ONLY=0|1`, both defaulting to 0. Ordinary calls retain their
exact native argv. Opting in appends `--debug` and/or `--failed` after the
existing repository/run arguments. Installed `make ci-rerun-help` confirms that
`--failed` includes dependencies; it is not an exact one-job selection.

The native ledger observation still precedes `guard-rerun`, unchanged-run
permission and a nonempty auditable reason. Direct-goal raw flags are captured
before startup exports and prerequisites, including command-line, environment
and `-e` carriers. Invalid flags refuse before observation or evaluation.
The [mandatory guide](../../../.agents/policy/key-make-targets.md#diagnostic-actions-reruns)
and target contract retain an offline plan: no remote observation, ledger write
or rerun request occurs. Success guidance identifies the exact run/reason and
one accepted request only. Failure preserves native status and marks the write
unknown until native observation reconciles a possibly applied request.

The existing ledger's [guarded rerun record](../CI_FAILURE_LEDGER.md) retains
the failure-family policy and prior research. This packet adds no HTTP research,
runner, polling owner, dispatcher, cache, Git mutation or Python production code.
The actual API response decides rerun authority; missing workflow-file push
scope is not evidence that rerunning an existing workflow is forbidden.

Acceptance starts at isolated source `008f879dbea79fdead27994a5948df4bc07b7d52`.
New `tests/unit/test_ci_rerun_diagnostics.py` executes the real Make recipes
and native ledger CLI against a harmless `gh` recorder. Controls retain actual
metadata/jobs/guard ordering, default argv, all flag combinations, offline plans,
native refusal/status, unrelated goals, private-carrier spoof resistance and
owned unguarded evaluation witnesses before guarded no-evaluation assertions.
Every predecessor test remains intact.

| Scope | Pytest result | Native elapsed/status | Worktree-relative receipt |
| --- | --- | --- | --- |
| RED 3.11 | 47 FAIL, 19.81s | 20.9s / exit2 | `.gate-logs/observed/v012-ci-rerun-debug-red/20261023T233615Z-40469-199e2c07.json` |
| New owner GREEN 3.11 | 77 PASS, 12.59s | 13.5s / exit0 | `.gate-logs/observed/v012-ci-rerun-debug-green311/20261023T233750Z-50847-e3eb3f80.json` |
| Complete 3.11 | 575 PASS, 30.94s | 32.054s / exit0 | `.gate-logs/observed/v012-ci-rerun-debug-union311/20261023T234154Z-63943-a2e1a9cf.json` |
| Complete 3.12 | 575 PASS, 33.71s | 34.702s / exit0 | `.gate-logs/observed/v012-ci-rerun-debug-union312/20261023T234243Z-66236-70a18cab.json` |

All exact test sessions are terminal and drained; native observed-status confirms
both complete unions inactive and passed. Acceptance uses one pytest process,
fatal warnings, literal `ID=`, installed runtimes and project-private native traces.
Narrow tests retain 90/60 seconds; the complete eight-owner union uses 300/60
diagnostic acceptance bounds without changing publication/runtime owners.
The union retains all eight owners: `test_ci_rerun_diagnostics.py`,
`test_ci_failure_ledger.py`, `test_ci_failure_ledger_concurrency.py`,
`test_workflow_failure_class_guards.py`, `test_make_target_contract.py`,
`test_ci_cancel_contract.py`, `test_make_help_audit.py` and
`test_pytest_param_fixture_collisions.py`, all under `tests/unit/`.
The installed help, offline example, Make lint, 290-target contract, strict new-test
typing, Ruff, test-environment guard and native tracked-file line limits pass.
Markdown lint remains unproved here: its native owner refuses because the
peer lacks the hardcoded locked `.venv/bin/rumdl`; no install or sync was attempted.
No new Python production coverage measurement follows from this Make-only repair;
85% aggregate / 75% per-file line AND branch floors and ZDD remain unchanged.

A debug request against published run `39826135809` supplies diagnostic logs
for that run's original source, not proof of local source repairs. Request
acceptance, queued work, terminal CI, canonical exact-SHA push proof and release
publication remain separate. No actual API request is included in these source
receipts. Normal collection, independent review, receiving, canonical full gate,
exact-SHA hosted success and immutable timing remain required as applicable.
Neither diagnostic readiness nor this source packet closes S98.4 or relaxes the
original 90/60 publication/runtime bounds, capacity, coverage or promotion rules.

### Locked Node advisory research and compatibility boundary (2026-10-23)

The authorized debug request produced a real new failure in Build run
`39826135809`, attempt 2, original source
`eb772a658a01db4485087813e4478eb6c59cc328`. The failed gate 3.12 step is
`Audit locked Node build dependencies`. Native ledger status retains one OPEN
family `4893be02c7dce927162ef033a51fd2674ae3d7d423492fb63e4f3c108d4ce062`.
The older runtime family remains REPAIRED by its local receipts; this new family
does not rewrite that history or supply hosted repair proof.

The unchanged native `node-deps-audit` at low severity reproduces the failure.
Its actual npm report contains three TOML advisories and one Zod advisory; registry
transport succeeded. Native npm's proposed forced plugin downgrade is not used.

| Advisory | Affected installed copy | Primary patched floor |
| --- | --- | --- |
| [GHSA-4qx8-w7c3-pj3p](https://github.com/advisories/GHSA-4qx8-w7c3-pj3p) | `toml` 4.3.0, affected <=5.0.0 | 5.0.1 |
| [GHSA-9j65-hcg3-gc64](https://github.com/advisories/GHSA-9j65-hcg3-gc64) | `toml` 4.3.0, affected <=5.0.0 | 5.0.1 |
| [GHSA-9qmh-m7c6-f92p](https://github.com/advisories/GHSA-9qmh-m7c6-f92p) | `toml` 4.3.0, affected <=5.0.0 | 5.0.1 |
| [GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5) | `zod` 4.1.8, affected <4.6.6 | 4.6.6 |

Structured advisory bodies and npm registry metadata independently establish
published TOML 5.0.1 and Zod 4.6.7, retaining their tarball URLs and integrity.
The latest observed plugin 1.19.4 still pins Effect 4.0.0-beta.83 and Zod 4.1.8;
upgrading that plugin alone does not clear these findings. These are retrieval-time
facts, retained in raw native logs, not an ongoing latest-version assertion.
The [TOML maintainer repair](https://github.com/BinaryMuse/toml-node/commit/609c873fffea0a27ebe5b4dd098eed2b3daf3606)
and release reference are present in the primary advisory bodies.

The separately retrieved firsthand [Zod issue 1872](https://github.com/colinhacks/zod/issues/1872)
was opened 2023-01-12 and closed 2026-10-09. Its reporter describes excessive
error accumulation and memory exhaustion while validating large arrays with
Zod 3.20.2. It is relevant long-lived context for the same availability mechanism;
it is not a Gludd execution or performance measurement. The current advisory's
reviewed range, actual lock and native audit establish this candidate's scope.
No additional issue comments or broad source search were retrieved.

The original-source a403 review separately found an unsafe test-recorder
boundary: tests/unit/test_node_audit_source_inspection.py saved the entire
inherited environment in JSONL. The recorder now retains all environment key
names and only its three owned public fields, LC_ALL, MODE and
GLUDD_NODE_SOURCE_UNRELATED. The existing forbidden-carrier assertion checks
the complete name set, preserving sanitation without retaining credential
values. All other original function bodies/assertions remain. Two additive
ordinary/-e witnesses use only a bounded owned environment and require a
private canary value to be absent from recorded output.

Actual tests-only RED is2FAIL/1.35s/native2/2.722s:
v012-review-node-privacy-red/v012-review-node-privacy-red311-a403-20261026.json.
The complete19-owner browser/privacy receiving passes554 EACH,3.11 in106.76s/
native108.332s and3.12 in115.86s/native117.729s:
v012-review-root-receiving/v012-review-root-receiving{311,312}-corrected-a403-20261027.json.
These paths are under .gate-logs/observed in the isolated
feature/v012-original-review-repairs-20261026 checkout at base
a403ed858bb561f7bbc8353c54ad057be4020f89. Both exact sessions are drained and
native observed-status verifies inactive/passed0; each native trace retains
1111events/554complete protocols with zero issues/failures/unfinished nodes.
Static checks and the six-path content guard pass. Independent review approves
source only. TASKS adds only this exact test path to existing S98.4 scope,
retaining all328 IDs/states/order and2499 lines. No real transport/auth/npm,
audit, canonical gate, exact-SHA CI, deployment or release result is supplied
by these fixture controls. Final prose and normal collection/commit remain
separate from the original installed audit and all final release obligations.

The Make-only public companion is `inspect-node-audit-source`. Its literal
`NODE_AUDIT_SOURCE_VALIDATE_ONLY=1` default is a pure offline plan; explicit 0
retrieves a fixed list with four advisories, four npm metadata endpoints and
the one referenced issue. There is no caller URL, token, output path or source
selector. Direct-goal capture precedes startup/export evaluation; an indirect
target refuses transport. Each public request uses connect 10s / total 20s / 2MiB,
zero retries and zero redirects, and stops on native failure while preserving
earlier raw bodies. Curl receives only its public tool path and locale, rather
than ambient credentials or proxy configuration. The existing observer owns
the project-private raw log, 10-second heartbeat and terminal receipt.

The new native recorder owner retains ordinary and `-e` argv, offline mode,
all fixed source/count pins, native 22/28/47 failures, ninth-request refusal,
unrelated carriers and actual same-origin evaluation witnesses. GNU Make
normalizes leading command-line assignment spaces before capture; independent
native value witnesses retain that rule while environment-leading spaces refuse.
A failed initial fixture replay remains historical evidence of that corrected
new-test assumption. No predecessor test assertion was removed.

All following receipts are relative to
`/private/tmp/gludd-worktrees/agent-v012-gha-build-phase`, based on
`d9cacf2f2cd9b1b7469027d0ef9ab18b52c9a2d0`:

| Scope | Actual result | Native receipt |
| --- | --- | --- |
| Original locked audit | Four high-severity findings, exit2/2.768s | `.gate-logs/observed/v012-node-audit-native-red/20261023T235405Z-84132-d7302fa1.json` |
| New helper RED | 39 FAIL / 2 PASS, 2.47s, native exit2/3.3s | `.gate-logs/observed/v012-node-audit-source-red/20261023T235630Z-87813-9f921e91.json` |
| Direct-context RED | 2 FAIL, 0.74s, native exit2/1.7s | `.gate-logs/observed/v012-node-audit-source-context-red/20261023T235715Z-88270-9b027a9c.json` |
| Eight-source helper GREEN | 43 PASS, 3.87s, native exit0/4.6s | `.gate-logs/observed/v012-node-audit-source-native-green311/20261023T235858Z-91605-82517dfa.json` |
| Primary eight bodies | HTTP200 each, exit0/2.086s | `.gate-logs/observed/v012-node-audit-primary-sources/20261023T235910Z-91898-b5944ba0.json` |
| Ninth-source catalog RED | 5 FAIL, 2.14s, native exit2/3.0s | `.gate-logs/observed/v012-node-source-issue-catalog-red/20261024T000613Z-95556-24e9ffc2.json` |
| Nine-source helper GREEN | 44 PASS, 4.45s, native exit0/5.3s | `.gate-logs/observed/v012-node-source-nine-green311/20261024T000707Z-97383-11930857.json` |
| Referenced issue followup | Nine HTTP200 bodies, exit0/2.4s | `.gate-logs/observed/v012-node-source-issue-followup/20261024T000737Z-97716-b3e501d1.json` |

The ninth-source followup changes only the catalog's new issue input; the prior
eight accepted primary bodies remain evidence for version claims. Raw HTTP 0
grants no issue relevance, package compatibility, audit acceptance or release proof.
Complete helper-only acceptance passes on both installed runtimes:

| Runtime | Complete result | Native receipt |
| --- | --- | --- |
| 3.11 | 331 PASS, 36.74s, native exit0/37.835s | `.gate-logs/observed/v012-node-source-helper-union311/20261024T001304Z-6083-ba293c15.json` |
| 3.12 | 331 PASS, 39.35s, native exit0/40.458s | `.gate-logs/observed/v012-node-source-helper-union312/20261024T001416Z-8240-fca5ece2.json` |

Both exact sessions are terminal and drained; native observed-status confirms
inactive/passed and the same source identity
`d9fa41bee52061d1b98816aaf98418c1ff2fcb1b0db6bd8ac9443b66b951f91b`.
The eight complete owners, under `tests/unit/`, are:

- `test_node_audit_source_inspection.py`
- `test_presentation_browser_source_inspection.py`
- `test_node_deps_update_notifier.py`
- `test_make_target_contract.py`
- `test_make_help_audit.py`
- `test_pytest_param_fixture_collisions.py`
- `test_ci_rerun_diagnostics.py`
- `test_ci_cancel_contract.py`

These source/transport contracts use one pytest process, fatal warnings, literal
`ID=`, explicit installed interpreters, project-private trace/observer paths,
300/60 diagnostic acceptance bounds and 10-second heartbeats. The original
90/60 publication/runtime owners are unchanged. The four separate Node repair
paths, including the failed compatibility case, are outside this helper union.

Root's exact five-path staged preflight also passed all three native hooks in
7.7s with zero new clones. Its retained receipt is
`node-source-staged-preflight/node-source-staged-preflight311-d9cacf2.json`.
The native hooks stashed and restored separate tracked Node changes on each
pass; those changes and the untracked compatibility owner remain separate and
unaccepted. Final feature receipt notes were added after the frozen code proof;
normal commit collection, final documentation lint and independent receiving
remain Root-owned.
The 291-target contract, pure offline behavior, Make lint, Ruff, strict helper
typing, environment-write guard and tracked-file line bounds pass. Markdown
lint still requires the existing locked Rumdl executable absent from this peer.

The dependency candidate is separate. Parent-scoped leaves retain plugin 1.18.5,
SDK 1.18.5 and Effect 4.0.0-beta.83. Native `node-deps-relock` also refreshed the
resolver's two Effect patch dependencies, msgpackr 2.1.1 to 2.1.2 and multipasta 0.2.8
to 0.2.9. No lock bytes were edited manually and no audit floor was raised.

Before that change, the original locked tree passed seven real installed Node 26
compatibility controls. These execute actual Effect `Primitive.fileParse` and
`fileSchema` with an owned filesystem service, plus the plugin's tool/schema/
execute API. Number, float, max-safe integer, wide integer, Date/local-time,
arrays/tables, typed schema and invalid-input behavior retain literal assertions.
The added all-copy floor controls failed on the two affected versions and missing
scoped overrides: 3 FAIL / 12 PASS, 4.23s, native exit2/5.4s, receipt
`.gate-logs/observed/v012-node-leaf-baseline-red/20261024T000355Z-94279-f4a14c27.json`.

After native relock and sync, the same controls produced 14 PASS / 1 FAIL,
2.61s, native exit2/3.6s:
`.gate-logs/observed/v012-node-leaf-compatibility311/20261024T000505Z-95086-f4618eb7.json`.
TOML 5.0.1 rejects `9007199254740993` instead of returning the old rounded
Number 9007199254740992. Its supported `bigint` option would change all integer
types; Effect's consumer calls `Toml.parse(content)` without options. The failed
wide-integer assertion and successful baseline remain unchanged. No adapter,
version substitution or expectation relaxation follows from passing audit floors.
The explicit operator preference is pending, so dependent leaf behavior and
the first post-fix audit are held.

This work restarts no serving process and preserves ZDD, fatal warnings and
85% aggregate / 75% per-file line AND branch floors. Helper acceptance is source
evidence only. Root still owns normal commit collection, independent review and
the exact clean Node candidate's first post-fix `ci-failure-repair` audit against
the existing native ledger. No post-fix npm audit was run in this packet.
The family stays OPEN; receiving, canonical full gate, exact-SHA feature/push CI,
hosted timing and release/deployment proof remain separate and pending.

Root then executed one actual diagnostic request at 2026-10-23T23:43:12Z,
using this peer's direct ci-rerun target, explicit installed3.11 and the existing
absolute Main CI ledger. Native ci-view and guard-rerun preceded
`gh run rerun -R sandboxcom/gludd 39826135809 --debug --failed`.
The explicit unchanged-source reason requested debug evidence for the published
eb772a6 runtime timeout, with no source-fix or release-proof claim. The existing
watcher receipt is `.gate-logs/observed/v012-diagnostic-build-rerun/v012-debug-build39826135809-request-008f879.json`;
it passed in2.585s with write_outcome=accepted. Root drained exact exec27607
and read native observed-status; request success alone proves no test result.
No credential consent, workflow-file push or new candidate was needed for this
actual existing-run Actions operation.

A subsequent native `ci-run-summary RUN=39826135809` reports the same published
full SHA eb772a658a01db4485087813e4478eb6c59cc328, workflow in_progress and
gate3.12 in_progress, with four completed successes and two skipped jobs visible.
Its nonzero status remains: work is unfinished. All eight shard timing rows are
pending/censored; no speed or gate improvement follows. The current printed
summary does not identify the attempt number, which must be reconciled through
the existing native observation before attributing later artifacts. This live
observation is separate from both575-case source receipts and all newer local
source. Later failure or completion still needs the actual native result.
The selected flag proof covers direct CLI/environment ordinary/-e inputs;
prerequisite invocation and later file/target-specific settings are unproved.


### Operator-approved unsafe TOML integer rejection (2026-10-27 UTC)

The operator explicitly approved rejection: "i agree, these values should not silently change".
This supersedes the pending preference in the retained October24 history above.
The original Effect control parsed9007199254740993 and expected the rounded
Number9007199254740992. That literal control and its failed receipts remain
history; only this case now requires clear lossless-number rejection through
the same real Effect one-argument parser. PREFIX and all six other cases remain
byte-identical, preserving safe numbers, dates, arrays, schema and plugin APIs.
The CSS predicate permits only the exact nested parent object; all tool pins stay.
The security owner remains byte-identical458da84d1876422ce24f74fbfd9d8a3d49461111659ff620d6c397513dfe8c11.

Root's unchanged security-floor owner at fixed f1aa82455ca1f78fc62ca7d09140af712dc37d70
fails3/passes5, pytest0.66s/native2.235s, at
v012-node-security-red/v012-node-security-red311-f1aa824-20261026.json:
TOML4.3.0, Zod4.1.8 and absent scoped overrides. The exact private proposal is
/tmp/gludd-v012-node-approved-repair-exact-proposal-f1aa824-20261026.json,
SHA256 f4a22584f56d0ab6af514881937f10f5608df0c61e7e3f6ff243d5e8616bcd6a.
Root applies only its three blocks. Native relock passes5.357s, sync4.416s at
v012-node-relock/v012-node-relock-scoped-f1aa824-20261026.json and
v012-node-sync/v012-node-sync-scoped-f1aa824-20261026.json. Both use the owned
public cache, /dev/null user config, update-notifier=false and --no-audit.
The complete generated diff changes only TOML/Zod version/URL/integrity fields;
all184 lock entries, plugin/SDK1.18.5, Effect4.0.0-beta.83, upstream dependency
declarations, msgpackr2.1.2, multipasta0.2.9 and all direct tool pins remain.
No generated lock bytes were edited manually and no older checkpoint was applied.

Ruff, three separate strict scopes, environment-write guard, all10895 file
bounds,303 target contracts and logical-Make AST pass27.545s at
v012-node-static/v012-node-static311-f1aa824-20261026.json. Complete23-owner
receiving passes490 on311 in72.14s/native73.944s and489/1 original skip on312
in73.45s/native75.307s, at v012-node-receiving/
v012-node-receiving{311,312}-scoped-f1aa824-20261026.json. The existing312 skip
is test_local_dual_track_make_example_is_canonical_and_observable; EACH retains
one default hook_live deselection from test_hook_runtime_verification.py.
Each trace has983 events/490 completed protocols/no unfinished or failed reports.
The literal complete owner union is:

- tests/unit/test_node_dependency_compatibility.py
- tests/unit/test_node_quality_security_floor.py
- tests/unit/test_css_lint_target.py
- tests/unit/test_ci_stylelint_workflow.py
- tests/unit/test_markdown_lint_target.py
- tests/unit/test_precommit_quality_guards.py
- tests/unit/test_root_cleanup.py
- tests/unit/test_presentation_make_targets.py
- tests/unit/test_presentation_javascript_runner.py
- tests/unit/test_node_deps_update_notifier.py
- tests/unit/test_security_audit_observability.py
- tests/unit/test_local_dual_track_producer.py
- tests/unit/test_pages_presentation_resilience.py
- tests/unit/test_duplicate_code_native.py
- tests/unit/test_enforcement_unbypassable.py
- tests/unit/test_opencode_node_v26_compat.py
- tests/unit/test_opencode_plugin_ports.py
- tests/unit/test_plugin_config_runtime.py
- tests/unit/test_hook_runtime_verification.py
- tests/unit/test_check_plugin_liveness.py
- tests/unit/test_precommit_hooks_deep.py
- tests/unit/test_integration_admission_target.py
- tests/unit/test_file_line_limits.py

The actual native test-opencode-e2e owner additionally passes44 EACH, fatal
warnings, original selectors, separate owned scratch and complete44-protocol
traces:3116.79s/native8.075s and3126.89s/native8.241s, at
v012-node-opencode-e2e/v012-node-opencode-e2e{311,312}-f1aa824-20261026.json.
Root drains all exact sessions and verifies terminal status/command scope.
Independent review approves source only. Normal fresh collection/source commit
and the FIRST actual post-fix audit through ci-failure-repair remain next;
all four native Node families stay OPEN at this fixed receiving snapshot.
Canonical85/75 LINE AND BRANCH BOTH, ZDD, full gate, exact-SHA hosted/promotion,
deployment and stable release proof remain separate and unproved.
