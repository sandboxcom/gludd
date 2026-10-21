# Release runbook

Release target: `v0.1.2`

Last updated: 2026-10-20

This is the fail-closed procedure for the committed v0.1.2 candidate. The
[archived v0.1.1 procedure](RELEASE_RUNBOOK_V011.md) retains the earlier
branch-reconciliation workflow, published-release exceptions, and historical
commands. Those commands do not authorize this release.

## The rule

A tag is not a release. Completion requires the exact tagged SHA to have its
own green canonical local gate, required exact-SHA hosted CI, reviewed-head
integration receipt, complete functional artifact matrix, signed publication,
and remote completeness PASS. Source approval, focused acceptance, collection,
scheduling admission, a dry-run, and a plan establish different facts.

Keep one proof owner and one explicit missing-proof record for each release
task. Retain every failed gate, timeout, CI run, receipt, and immutable candidate
ref; a later source change cannot relabel an earlier failure. Follow
[Release Proof Throughput](features/RELEASE_PROOF_THROUGHPUT.md).

## What "complete" means

The release must pass all 28 artifact categories with at least 30 assets, the
exact tag version, no zero-byte assets, a non-draft release, and the stable
release flag `isPrerelease: false`. The pre-publish matrix also proves artifact
contents, all 15 required smoke checks, aggregate checksums, digest-pinned
container/EE references, canonical Ansible metadata, rollback receipt, and
signed provenance. The published bytes must pass their separate replay.

The only acceptable terminal remote completeness verdict is:

```console
make verify-release-completeness TAG=v0.1.2
```

Require its actual `COMPLETENESS CHECK: PASS` output; this document is not a
receipt. Missing, pending, timed-out, cancelled, stale, or wrong-SHA evidence
blocks completion and task closure.

## Preconditions

Perform release promotion from the canonical main checkout, at the exact clean
`development` tip. Feature work lands on development first. Never rebase either
shared branch, push feature/fix-forward work directly to master, or merge to
master from a worktree. The promotion owner captures the verified development
SHA and fast-forwards canonical master only after its gates pass.

Commit all release metadata before sealing the candidate: `pyproject.toml`,
`src/general_ludd/__init__.py`, README, release notes, changelog, workflow
inputs, locks, and required task/evidence metadata must describe the same
version and candidate. Select the installed registered Python3.11 release
toolchain before every outer Make invocation, including Git/helper targets.
Use the five runtime assignments in the [mandatory Make guide](../.agents/policy/key-make-targets.md#run-focused-checks-with-explicit-inputs)
and append them to the commands below. The current SESSION identifies the
installed paths. Main's existing .venv may be Python3.14 and is not this release's
canonical311 runtime. Reuse the selected environment without synchronization or
downloads; do not let a documentation example acquire an implicit runtime. Run:

```console
make check-version-consistency
make check-readme-status TAG=v0.1.2
make check-runbook-currency TAG=v0.1.2
make validate-ansible-runtime-boundary
make test-files TESTFILES=tests/unit/test_collection_interop.py \
  PYTEST_ARGS='-q -W error -n 0' \
  UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
make release-page-notes TAG=v0.1.2 \
  RELEASE_PAGE_NOTES_LEDGER=config/v012_completed_backlog_reconciliation.json \
  RELEASE_PAGE_NOTES_OUTPUT=docs/releases/v0.1.2.md \
  RELEASE_PAGE_NOTES_VALIDATE_ONLY=1
```

Require version `0.1.2` everywhere, the locked core/controller/managed-host
Python boundary, and declared/resolved cross-collection dependencies. The full
collection-interop test owner invokes the existing native graph audit on this
repository through its original `test_repository_collection_graph_is_valid`
assertion, alongside missing/undeclared dependency, role/module, cycle, CLI,
and ZDD refusal controls. Its real repository report must contain zero issues;
fixture-only success or a Python-boundary check cannot replace that result.
The release page must preserve both ledger-backed source inventory and historical
integration/proof/default-disabled boundaries; implementation is not release
completion. A non-zero exit stops the sequence at its actual failing owner.

Each uppercase value below denotes an actual native value, not a fact to
invent: `ACTIVE_RELEASE_TASK_ID` is the selected canonical open task,
`FULL_DEVELOPMENT_SHA` is the captured clean development SHA, and
`CANONICAL_NATIVE_GATE_ATTESTATION` is the resource owner's canonical
`ci-shards/attestation.json` path. Resolve them from the existing native
work-item, worktree, and gate/resource outputs before execution. Keep the
review intent and producer-owned evidence under the named external
`/tmp/gludd-v012-*` paths so evidence creation does not dirty the candidate.

### 1. Admit actual proof work before sealing

```console
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0 \
  WORK_ITEM_ID=ACTIVE_RELEASE_TASK_ID \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=20
```

Run real admission before candidate sealing, final combined replay, or an
expensive full gate. It checks the existing capacity, cleanup, configuration,
ownership, source-contract, and runtime prerequisites. Repair the actual
failing input before retrying. A changed HEAD, elapsed time, observer ID, or a
larger resource budget does not clear an unchanged prerequisite failure.

A successful admission permits proof to start; it is not gate or release
evidence. Any `VALIDATE_ONLY=1` or other plan output remains non-authoritative.

### 2. Freeze one candidate and bind independent source review

After admission, pin the original source/base refs and one final candidate.
Obtain independent review of the actual original sources. Never replace that
approval with a passing suite, a Markdown verdict, a task checkbox, or synthetic
review JSON. Use the trusted reviewer's explicit intent and the existing
[native reviewed-head producer](features/NATIVE_REVIEWED_HEAD_INPUTS.md):

```console
make reviewed-head-inputs REVIEWED_HEAD_INPUTS_ACTION=record \
  REVIEWED_HEAD_INPUTS_INTENT=/tmp/gludd-v012-release-review-intent.json \
  REVIEWED_HEAD_INPUTS_OUTPUT=/tmp/gludd-v012-release-reviewed-inputs \
  REVIEWED_HEAD_INPUTS_REPO_ROOT=. \
  REVIEWED_HEAD_INPUTS_GATE_ATTESTATION=CANONICAL_NATIVE_GATE_ATTESTATION \
  REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=0 WORK_ITEM_ID=ACTIVE_RELEASE_TASK_ID
make reviewed-head-inputs REVIEWED_HEAD_INPUTS_ACTION=focus \
  REVIEWED_HEAD_INPUTS_INTENT=/tmp/gludd-v012-release-review-intent.json \
  REVIEWED_HEAD_INPUTS_OUTPUT=/tmp/gludd-v012-release-reviewed-inputs \
  REVIEWED_HEAD_INPUTS_REPO_ROOT=. \
  REVIEWED_HEAD_INPUTS_GATE_ATTESTATION=CANONICAL_NATIVE_GATE_ATTESTATION \
  REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=0 WORK_ITEM_ID=ACTIVE_RELEASE_TASK_ID
```

The producer derives native source/ref, patch/topology, application-order, and
review bindings. Its single combined focused acceptance uses the original
reviewed test owners, fatal warnings, one worker, native JUnit/trace, exact
candidate/runtime identity, and the existing authentication owner. A focus
failure remains a failure; changing the bundle or observer cannot authorize
repeating unchanged failed inputs. Keep the candidate unchanged during proof.

### 3. Run the complete canonical release gate once

The canonical gate policy is `make gate` with
`GATE_TEST_PROFILE=release`, `GATE_EXACT_SHA_RESUME=0`,
`GATE_MAX_FILES_PER_BATCH=16`, and `GATE_BATCH_WORKERS=1`.
Launch that same gate once through its existing observed lifecycle owner:

```console
make gate-background-observed GATE_TIMEOUT=7200 GATE_POLL_INTERVAL=60 \
  GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY=0 \
  GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 \
  GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 \
  WORK_ITEM_ID=ACTIVE_RELEASE_TASK_ID
```

This owner keeps the operation visible and records the real outer gate
lifecycle needed by receipt finalization. Do not launch a second foreground
gate to manufacture another receipt. Require the authenticated terminal full
gate PASS, all eight native shard receipts with the canonical release
execution policy, every required integration/E2E/Molecule/artifact phase, and
all original assertions. A background PID or shard-only result is not the
completed outer gate.

Treat warnings as errors. Coverage must be at least **85% aggregate** and
**75% per file** on both lines and branches, retaining stricter feature
thresholds. Require zero lint errors, type errors beyond the permitted baseline,
collection errors, failing tests, xfails, and unexpected skips. Do not reduce
selectors, change workers/profile/resume policy, omit files/branches, waive
cleanup, or relax assertions to obtain proof.

### 4. Finalize the native reviewed-head integration receipt

Only after the exact candidate's combined focus and complete gate pass:

```console
make reviewed-head-inputs REVIEWED_HEAD_INPUTS_ACTION=finalize \
  REVIEWED_HEAD_INPUTS_INTENT=/tmp/gludd-v012-release-review-intent.json \
  REVIEWED_HEAD_INPUTS_OUTPUT=/tmp/gludd-v012-release-reviewed-inputs \
  REVIEWED_HEAD_INPUTS_REPO_ROOT=. \
  REVIEWED_HEAD_INPUTS_GATE_ATTESTATION=CANONICAL_NATIVE_GATE_ATTESTATION \
  REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=0 WORK_ITEM_ID=ACTIVE_RELEASE_TASK_ID
```

The producer authenticates the actual finished outer lifecycle, current clean
SHA/content, native local shard attestation, focused runtime, and chronology.
It publishes `reviewed-head-receipt.json`, then `manifest.json`, outside the
candidate. Do not hand-author, copy from fixtures, rewrite, or relocate current
observations to manufacture acceptance. Its canonical receipt is the artifact
consumed below; a second manual receipt builder is unnecessary.

## Verify CI

Use the existing policy-guarded development push owner after native admission;
verify the selected branch before pushing. Keep the frozen candidate unchanged
while hosted proof runs. Observe the exact captured SHA and workflow identity:

```console
make ci-await BRANCH=development TIMEOUT=5400 SHA=FULL_DEVELOPMENT_SHA \
  CI_AWAIT_WORKFLOW='Build and Release' CI_AWAIT_EVENT=push \
  CI_AWAIT_INTERVAL=10 CI_AWAIT_AFTER_RUN_ID=0 \
  CI_AWAIT_VALIDATE_ONLY=0 CI_AWAIT_SNAPSHOT_ONLY=0 \
  CI_AWAIT_OBSERVATION_OUTPUT=/tmp/gludd-v012-release-ci.json
make ci-verdict-safe SHA=FULL_DEVELOPMENT_SHA CI_BRANCH=development \
  BRANCH=development CI_CHECK_COOLDOWN_SEC=600 CI_VERDICT_VALIDATE_ONLY=0
```

The exact-SHA verdict must include every required workflow, with `headSha`
equal to the candidate/development tip. One successful workflow cannot replace
the complete verdict. Missing, pending, skipped, stale, failed, cancelled, or
timed-out runs are non-success. Use the existing producers for current CI JSON;
pending/lookup-error/timeout must invalidate an earlier current success.
Preserve immutable run URLs, all eight shard identities, original failures,
and timing/censored intervals; diagnostics and plans never become authority.

## Merge development and release-cut through promotion

Revalidate real readiness on the same clean development candidate:

```console
make release-readiness TAG=v0.1.2 RELEASE_READINESS_VALIDATE_ONLY=0 \
  RELEASE_COMPLETED_STAGES='' RELEASE_OBSERVATIONS='' \
  REVIEWED_HEAD_INTEGRATION_RECEIPT=/tmp/gludd-v012-release-reviewed-inputs/reviewed-head-receipt.json \
  RELEASE_CANDIDATE_SHA=FULL_DEVELOPMENT_SHA \
  RELEASE_READINESS_CI_BRANCH=development
```

Empty forecast fields do not waive task, receipt, gate, CI, completeness, or
deployment prerequisites. Then, from canonical main at the verified
development tip, use the sole promotion owner:

```console
make release-promote TAG=v0.1.2 MSG='v0.1.2 release' \
  RELEASE_PROMOTE_VALIDATE_ONLY=0 RELEASE_PROMOTE_PUBLISH=1 \
  REVIEWED_HEAD_INTEGRATION_RECEIPT=/tmp/gludd-v012-release-reviewed-inputs/reviewed-head-receipt.json
```

The owner revalidates clean topology, exact-SHA local/hosted evidence and
readiness, fast-forwards canonical master to the captured development SHA,
and calls `release-cut` with that same SHA, local attestation, CI source branch,
and reviewed-head receipt. Do not substitute a manual merge, direct tag push,
or worktree promotion. Validation/planning mode and `PUBLISH=0` do not publish
or complete a release.

The internal `release-cut` path verifies a signed annotated tag before push,
snapshots any prior matching workflow run, and waits for a newer exact
tag/SHA/workflow/event identity. Its tag-triggered lane rechecks the unchanged
development proof and runs every platform, container, EE, provenance, and
artifact prerequisite before publication. A timeout remains non-success.

### Metadata changes require a new candidate proof

Any edit after sealing, including version, changelog, runbook, generated notes,
workflow, lock, TASKS evidence, or other metadata, creates a different
candidate. Preserve the original candidate ref and its failed/passing receipts;
re-enter actual admission, original-source review/binding, combined acceptance,
full canonical gate, and exact-SHA CI for the new final SHA before promotion.
Bookkeeping, a new commit label, or a previously green parent is not an
exception. Keep native evidence outside the checkout and never modify a frozen
candidate during final proof.

## Functional artifact matrix

The release workflow builds, validates, and stages these immutable outputs:

| Lane | Required output | Pre-publish functional proof |
|---|---|---|
| Linux x86_64 | tar, deb, rpm | extract each package and execute `gludd version`; tar also runs `--help` |
| Linux aarch64 | tar | extract and execute on the native arm64 runner |
| macOS arm64 | tar, dmg | extract tar; mount the read-only DMG; execute both binaries |
| Windows x86_64 | zip, NSIS | expand ZIP; silently install NSIS; execute; silently uninstall |
| Python | wheel, sdist | install each into its own empty, namespaced virtual environment and execute |
| Collections | agent, language, networking tarballs and index | build with `ansible-galaxy`; validate archive identities against locked EE requirements |
| Ansible EE | seven canonical boundary inputs plus image metadata | build with `ansible-builder`, run offline import smoke, push beside active image, record digest |
| Container | GHCR image metadata | run a namespaced container and wait a bounded 30 seconds for `/healthz` |
| Metadata | CycloneDX SBOM, install script, licenses, provenance, rollback receipt, checksums | validate schemas, execute installer from the Linux archive, rehearse rollback, and verify exact SHA-256 coverage |

The completeness verifier recognizes exactly 28 mandatory categories; none are
optional. The minimum is 30 assets because the runtime-collection category
requires three separately named collection tarballs:

| Group | Exact required categories |
|---|---|
| Platform binaries (4) | Linux x86_64, Linux aarch64, macOS arm64, Windows x86_64 |
| Native packages/installers (4) | `.deb`, `.rpm`, `.dmg`, Windows `.exe` installer |
| Base metadata (4) | checksums, SBOM, `LICENSE`, `THIRD_PARTY_LICENSES` |
| Python and collections (4) | wheel, sdist, three runtime collection tarballs, collection manifest |
| Ansible execution boundary (8) | EE definition, EE collection requirements, EE Python requirements, EE system requirements, EE runtime lock, managed-host Python lock, collection Python boundary inventory, EE image metadata |
| Runtime delivery (4) | container image metadata, install script, smoke attestations, release manifest |

This list mirrors `EXPECTED_CATEGORIES` in
`scripts/verify_release_completeness.py`. A category-count change must update
the verifier, its structural tests, and this runbook together.

Every platform job writes a versioned smoke attestation only after its checks
pass. `scripts/verify_release_asset_matrix.py` unions those attestations and
requires all 15 smoke checks before the publishing action runs. Every artifact
upload sets `if-no-files-found: error`.

The release job also writes
`gludd-rollback-receipt-<version>.json` before the release manifest,
`SHA256SUMS`, provenance attestation, or publication. The rehearsal is
hermetic: it uses only the staged Linux archive and a run-scoped route under
`/tmp/gludd-rollback-rehearsal-<run>-<attempt>`. It executes the candidate's
`version` and `--help` commands, activates that exact staged artifact in the
ephemeral route, restores the byte-identical prior-version route, and hashes a
fixed in-flight-work snapshot before and after the transition. It does not call
a cloud, registry, release, or deployment mutation API.

The receipt is accepted only when all of these bindings agree:

- candidate activation, health, observed version, artifact name, and staged
  artifact SHA-256;
- the prior version, its route SHA-256 before activation and after restoration,
  restored health/version, and an explicit immutable-restoration result;
- identical before/after SHA-256 values for active work;
- the exact 15-category smoke fan-in and the SHA-256 of every contributing
  smoke attestation; and
- the source commit plus a canonical evidence SHA-256 for the receipt body.

The verifier replays the existing release state machine through stage, canary,
and rollback. A missing category, false status, version drift, changed prior
route, changed work snapshot, rebound attestation, or altered evidence digest
refuses the receipt and blocks publication. `write-manifest` then inventories
the receipt, `SHA256SUMS` binds its published bytes, and the existing
`actions/attest` step signs the same aggregate checksum subject. This is a
control-plane rollback proof, not permission to mutate a live deployment; the
provider-specific live proof remains a separate release-readiness obligation.

Publication is a second trust boundary. Immediately after the GitHub Release
action returns, the release job downloads only the published rollback receipt,
release manifest, `SHA256SUMS`, Linux candidate archive, and complete versioned
smoke-attestation set into a run-scoped directory. The
`verify-published-rollback` command then replays the receipt schema and ZDD state
machine against those downloaded bytes, verifies every evidence file through
the published aggregate checksum, and requires the manifest to inventory the
receipt, candidate, and smoke inputs. It never substitutes the runner's
`release-assets` staging directory for hosted evidence. The following
post-deploy smoke executes the downloaded candidate's version and help commands.
Missing, rebound, oversized, linked, malformed, or inconsistent evidence blocks
the release with bounded diagnostics that do not echo hosted content; cleanup
preserves the primary failure. This read-only verification cannot shift live
traffic, so a failure leaves the previously active version and in-flight work
unchanged.

After the matrix writes and validates `SHA256SUMS`, the release job uses
`actions/attest` v4.2.2 pinned to commit
`1e69f48acb82d1966a394da916b4c1698aa569d6`. Its `subject-checksums` input binds
the signed SLSA statement to the same complete asset set that is published. The
job grants `id-token: write` and `attestations: write` explicitly, and
`make check-provenance-attestation TAG=v0.1.2` uses `gh release verify`; a source
manifest alone is not signed provenance.

Long-lived practitioner reports shaped the guardrails. In
[actions/attest-build-provenance #156](https://github.com/actions/attest-build-provenance/issues/156),
self-hosted users reported OIDC-token failures despite apparently correct
settings, so release attestation remains on a GitHub-hosted runner with explicit
permissions and is blocking. In
[actions/attest-build-provenance #454](https://github.com/actions/attest-build-provenance/issues/454),
multi-subject input remained a recurring usability issue; Gludd avoids a
hand-maintained subject list by feeding the already-verified aggregate checksum
file to the consolidated action. GitHub's cited
[artifact-attestation guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
confirms the OIDC/attestations permissions and post-build placement.

Historical exception: v0.1.1 was published before this signed-attestation step
existed. Its immutable source manifest and SHA-256 coverage remain valid, but
its immutable historical publication has no signed attestation. Do not rewrite the
public tag or assets; the exception and fix-forward are recorded in
`docs/releases/audit-0.1.1.json`.

The staged `install.sh` is treated as untrusted release input even after its
execution smoke. Static verification accepts at most 1 MiB of UTF-8, requires
the executable bit and the exact repository Bash shebang, and requires an
active `set -euo pipefail` line; a comment containing those words is not
evidence. Invalid encoding and oversize input produce bounded matrix failures
instead of an unhandled traceback. This check reads one bounded file and starts
no process, so parallel platform jobs retain their existing resource budgets.

## Canonical Ansible runtime artifacts

The release must contain byte-for-byte copies of:

- `config/ansible/execution-environment.yml`;
- `config/ansible/requirements.yml`;
- `config/ansible/requirements.txt`;
- `config/ansible/bindep.txt`;
- `config/ansible/runtime-lock.json`;
- `config/ansible/managed-host-python.lock.json`;
- `config/ansible/collection-python-boundary-inventory.json`.

The core Python distribution remains separate from the controller execution
environment and managed-host interpreter locks. Missing or stale copies fail
before publication.

## Zero-downtime deployment

Container and Ansible EE builds are additive:

1. build a new versioned image beside the active digest;
2. run its isolated smoke test with a namespaced process/container;
3. push the immutable image and record its registry digest;
4. publish metadata only after all other artifacts pass;
5. route only new work to the new digest, then drain in-flight work.

Rollback routes new work to the previously recorded digest and drains the bad
revision. Never mutate or retag the prior digest. Platform release assets are
also immutable: a repair must come from the same tagged CI SHA or from a new
version tag.

Before publication, inspect the rollback receipt in the staged matrix. Its
`candidate.sha256` must match the staged Linux archive, both prior-route
digests and both active-work digests must be identical, and
`platform_fan_in.categories` must contain the complete 15-check set. Do not
hand-edit or regenerate the receipt after `SHA256SUMS`; a changed receipt is a
new candidate and requires the complete release job again.

Installer verification happens entirely in the additive staging directory.
Failure blocks publication while the active digest and any previously
published assets remain untouched; rollback is therefore deletion of the
failed candidate staging set, not mutation of the live release. This preserves
zero-downtime service for existing users while a corrected candidate is built.

## Verify with verify-release-completeness

After publication and hosted-byte rollback replay, independently require:

```console
make verify-release-completeness TAG=v0.1.2
make check-provenance-attestation TAG=v0.1.2
make release-view TAG=v0.1.2
```

Require `isDraft: false`, `isPrerelease: false`, all 28 required artifact
categories PASS, at least 30 assets, zero zero-byte assets, exact v0.1.2 tag/SHA
and release URL, successful signed provenance verification, and the published
rollback receipt/checksum/manifest/smoke set replay. A source manifest or
successful upload does not replace the hosted-byte checks.

Record actual release/download URLs, signed tag and exact SHA, local gate,
line/branch coverage, required CI run URLs, reviewed-head receipt, and remote
completeness PASS in the owning evidence record. Tick tasks only with their
required completed proof. A later metadata commit follows the new-candidate
rule above; never move the published tag or rewrite immutable assets.

## Rollback

The v0.1.2 prior-version rollback target is the already-published `v0.1.1`.
Before live admission, pin and verify that release's exact immutable platform
artifact, checksum set, image digest, configuration snapshot, and compatibility
evidence. Its unsigned-tag and missing-attestation history remains explicitly
recorded in the [v0.1.1 audit](releases/audit-0.1.1.json) and
[archived procedure](RELEASE_RUNBOOK_V011.md). That exception never waives
v0.1.2 signed-tag/provenance or functional proof.

Route new work back to the verified prior digest first, block new candidate
intake, and drain work already bound to the failed generation. Keep minimum
healthy capacity; do not mutate or retag the prior digest, overwrite either
release's assets, or downgrade a database/configuration in place without
verified compatibility and migration/health evidence.

Independently verify the prior digest, binary version, health, work continuity,
and absence of candidate-bound new work after routing recovery. Preserve the
failed candidate, deployment receipts, logs, and content-free incident evidence.
The hermetic published receipt proves the release lane's candidate activation,
immutable prior-route restoration, and unchanged active work; it does not
replace provider-specific live canary, health, rollback, or cleanup proof.

Cleanup removes only resources owned by the exact candidate/lease after bounded
drain and provider-side absence checks. Unknown cleanup remains
`CLEANUP_PENDING`; preserve the primary error. An interrupted publication
requires the existing exact-identity recovery policy and the same complete
artifact checks. Never upload local replacements or recut a red/invalid
candidate to manufacture success. Fix forward through development with a new
reviewed, gated, exact-SHA candidate and new version tag when required.

## Traps

- **A cancelled CI run is NOT a verdict.** Cancellation, timeout, missing CI,
  and a successful run for a different SHA all block promotion.
- A successful build command does not prove a usable package. Execute each
  packaged form and require its smoke attestation before publication.
- Workflow-artifact retention is not release publication. Missing uploads fail
  immediately, and only the checksummed GitHub Release set is the durable
  release verdict.
- Cleanup traps are part of the gate. They preserve an existing primary failure
  and turn a detach, container-removal, or temporary-directory cleanup failure
  into a red step when the smoke itself succeeded.
- Never repair a release with locally rebuilt files. Re-run CI from the exact
  tagged SHA when safe, or fix forward and cut a new prerelease.
- If concurrent automation replaces uncommitted release work, stop writing to
  the canonical checkout, create an isolated worktree, and follow
  [Codex file-change recovery](features/CODEX_FILE_CHANGE_RECOVERY.md). Replay
  only an audited thread/ordinal range; never use a broad restore or reset.

## Long-lived practitioner failure history

The following reports retain their original review dates and version scope.
They define refusal shapes for this release; they provide no current upstream
status, successful Gludd proof, or exception to the v0.1.2 procedure.

Reviewed on 2026-08-30, two long-running practitioner reports explain why
v0.1.1 and earlier beta releases fail closed instead of trusting a successful
build command or a well-named transfer artifact:

- GitHub Actions upload-artifact
  [issue #290](https://github.com/actions/upload-artifact/issues/290), opened in
  2022, records practitioner trouble with artifact retention/storage behavior.
  Beta4 treats workflow artifacts as short-lived transfer objects, requires
  every upload to fail when files are absent, and publishes a checksummed
  release set rather than relying on retained workflow artifacts. The same
  trust boundary is why malformed, oversized, or comment-only installer
  content becomes an observable pre-publish failure.
- PyInstaller
  [issue #5360](https://github.com/pyinstaller/pyinstaller/issues/5360) records
  the long-lived class of frozen applications that build successfully but omit
  optional dependency data or templates. Beta4 therefore executes each native
  binary before packaging and executes the packaged form again before publish;
  `gludd.spec` remains the explicit data/submodule inventory.

These reports are design evidence, not exceptions. A matching upstream symptom
still fails the beta4 gate.

### Rollback receipt practitioner evidence

Reviewed on 2026-10-05, long-lived operator reports explain why a successful
rollback command is not accepted without immutable identity and continuity
evidence:

- Argo Rollouts
  [issue #501](https://github.com/argoproj/argo-rollouts/issues/501), opened in
  2020, records old blue replicas terminating almost immediately despite a
  configured scale-down delay. The receipt therefore hashes active work before
  and after the candidate/rollback transition; selecting the old route alone is
  insufficient.
- Kubernetes
  [issue #50021](https://github.com/kubernetes/kubernetes/issues/50021), opened
  in 2017, records `rollout undo` reporting success while a failed revision
  remained and rollout status could not reach a healthy terminal state. The
  receipt consequently requires restored prior version and health evidence in
  addition to a restoration status string.
- GitHub Community
  [discussion #161656](https://github.com/orgs/community/discussions/161656)
  demonstrates that deleting and re-uploading a release asset under the same
  name changes its digest. Gludd binds the prior route, candidate artifact,
  every smoke attestation, and the receipt itself by SHA-256; a matching name is
  never treated as immutable identity. The post-publication replay therefore
  downloads the hosted evidence and rechecks `SHA256SUMS` instead of trusting
  the successful upload step or the runner's original staging bytes.

These reports define refusal cases. A digest change, incomplete fan-in, missing
health/version proof, or active-work drift cannot be waived by a successful
command exit.

### Signed-tag automation evidence

Reviewed on 2026-10-05, long-lived GitHub operator reports show why tag signing
must be owned before publication instead of inferred from a successful release
job:

- GitHub Community discussion
  [#27016](https://github.com/orgs/community/discussions/27016) records that the
  REST tag-creation API does not create a user-signed tag; the supported pattern
  is to sign with Git locally and push the resulting tag object. Gludd therefore
  creates release tags with `git tag -s -a`, verifies them locally, and only then
  pushes the ref.
- `actions/checkout` issue
  [#649](https://github.com/actions/checkout/issues/649) documents signed tag
  objects being checked out as direct commit refs in shallow workflows. Hosted
  artifact jobs may prove the peeled commit, but they are not the owner of tag
  signature admission; the pre-push local guard verifies the actual tag object.
- `action-gh-release` issue
  [#722](https://github.com/softprops/action-gh-release/issues/722) records an
  orphaned draft release when tag creation is denied after release work starts.
  Gludd performs signing and tag-policy checks before remote mutation so a
  signing failure cannot create or replace hosted release state.

The v0.1.1 tag predates this repaired wiring and is unsigned. Its published tag
is immutable: do not delete or replace it to manufacture compliance after the
fact. The audit trail must record that exception, and every later release is
blocked unless the signed tag verifies before push.
