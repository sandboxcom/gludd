# Release runbook

This is the fail-closed procedure for cutting Gludd `v0.1.1`.

## The rule

A tag is not a release. A release is complete only when the exact tagged commit
has green CI, every required artifact has passed the pre-publish functional
matrix, and the published release passes the remote completeness check.

## What "complete" means

The only acceptable remote verdict is:

```text
make verify-release-completeness TAG=v0.1.1
...
COMPLETENESS CHECK: PASS
```

The v0.1.1 verifier requires all 28 artifact categories to pass, at least 30
assets to be present, the exact tag version in release assets, a non-draft
release, and no zero-byte asset. Before publication, CI also verifies artifact
contents, aggregate checksums, digest-pinned image references, canonical
Ansible runtime metadata, and all smoke attestations.

## Preconditions

Run these commands from the main checkout. Stop on every non-zero exit.

```text
make check-version-consistency
make check-readme-status TAG=v0.1.1
make validate-ansible-runtime-boundary
make check-collection-interop
make gate-all
make ci-verdict-safe BRANCH=development
```

Required evidence:

- `pyproject.toml`, `src/general_ludd/__init__.py`, and the README carry the same
  committed version bump;
- the project version is `0.1.1` everywhere;
- the core/controller/managed-host Python boundary is valid and locked;
- every cross-collection role edge resolves and its dependency is declared;
- aggregate coverage is at least 85%, every measured file is at least 75%, and
  the full gate has no warnings, errors, collection errors, xfails, or
  unexpected skips;
- CI is successful for the exact `development` tip. Missing, stale, pending,
  or cancelled CI is not green.

## Verify CI

Run `make ci-verdict-safe BRANCH=development` only after the full gate passes.
The verdict must belong to the exact development SHA that will be merged. A
missing, pending, stale, skipped, timed-out, or cancelled run is not a green
verdict and cannot authorize a release.

## Merge development

Only the main checkout may merge development to master.

```text
make development-merge-to-master
make verify-remote BRANCH=master SHA=<master-full-sha>
```

Do not create a release from a feature worktree. Do not rebase either shared
branch.

## Cut v0.1.1 with release-cut

```text
make release-cut TAG=v0.1.1 MSG='v0.1.1: S83.166 release documentation and version bump'
```

The tag-triggered workflow first proves that the identical development SHA has a
terminal green canonical run. It then completes the tag gate plus every platform,
container, execution-environment, provenance, and artifact job before its release
job can publish. `release-cut` snapshots any older matching tag run, polls the new
exact tag/SHA/workflow/event every 10 seconds for at most 90 minutes, and finally
runs artifact and full-matrix verification. A local timeout remains non-success.

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
`make check-provenance-attestation TAG=<tag>` uses `gh release verify`; a source
manifest alone is not signed provenance.

Long-lived practitioner reports shaped the guardrails. In
[actions/attest-build-provenance #156](https://github.com/actions/attest-build-provenance/issues/156),
self-hosted users reported OIDC-token failures despite apparently correct
settings, so release attestation remains on a GitHub-hosted runner with explicit
permissions and is blocking. In
[actions/attest-build-provenance #454](https://github.com/actions/attest-build-provenance/issues/454),
multi-subject input remained a recurring usability issue; Gludd avoids a
hand-maintained subject list by feeding the already-verified aggregate checksum
file to the consolidated action. GitHub's current
[artifact-attestation guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
confirms the OIDC/attestations permissions and post-build placement.

Historical exception: v0.1.1 was published before this signed-attestation step
existed. Its immutable source manifest and SHA-256 coverage remain valid, but
`gh release verify v0.1.1` correctly reports no attestation. Do not rewrite the
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
beta tag.

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

```text
make verify-release-completeness TAG=v0.1.1
make release-view TAG=v0.1.1
```

Expected release state:

- `isDraft: false`;
- `isPrerelease: false`;
- all 28 required artifact categories report `PASS`;
- at least 30 assets are present;
- no zero-sized asset;
- signed release provenance verifies with `gh release verify` (the immutable
  v0.1.1 historical exception is recorded above and in its audit receipt);
- release URL identifies `v0.1.1`.

Record the release URL, exact tag SHA, CI run URL, gate evidence, coverage
evidence, and completeness PASS in the task ledger.

## Rollback

The v0.1.1 rollback target is `v0.1.0-beta.4`. Rollback is a routing change to
that already-published version; it never moves, deletes, or overwrites the
v0.1.1 tag or its release assets.

- **Target:** route new work to `v0.1.0-beta.4`, then drain work already bound
  to v0.1.1.
- **Container:** resolve the immutable image digest recorded by the
  `v0.1.0-beta.4` release metadata and pin that digest. Never roll back to a
  mutable image tag.
- **Binary:** obtain the platform artifact from
  `https://github.com/sandboxcom/gludd/releases/tag/v0.1.0-beta.4` and verify it
  with that release's checksum set before installation.
- **Config compatibility:** restore a configuration snapshot validated by
  v0.1.0-beta.4 and run its migration/health checks before shifting traffic.
  Do not downgrade a database or configuration in place without that proof.

After traffic is restored, independently verify the prior digest, binary
version, health endpoint, and absence of v0.1.1-bound new work. Preserve the
failed deployment and its content-free evidence until the incident record is
complete.

The published receipt proves that the release lane can activate the exact
candidate and restore its immutable prior route without changing already-active
work. It does not replace this live verification: if live health/version or
work continuity differs from the receipt, refuse completion and keep the prior
route active.

Never upload a locally built replacement to a published release. Only artifacts
built by CI from the exact tagged SHA have valid provenance.

If publication was transiently interrupted, `release-recut` snapshots the prior
run before re-pushing the tag, waits only for a newer exact-identity run, and then
repeats both artifact checks:

```text
make release-recut TAG=v0.1.1
make verify-release-completeness TAG=v0.1.1
```

If the tagged SHA is red or an artifact is functionally invalid, do not paper
over it. Fix forward on development, repeat the full gate and exact-SHA CI
check, then cut the next prerelease tag.

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
