# Release runbook

This is the fail-closed procedure for cutting Gludd `v0.1.0-beta.4`.

## The rule

A tag is not a release. A release is complete only when the exact tagged commit
has green CI, every required artifact has passed the pre-publish functional
matrix, and the published release passes the remote completeness check.

## What "complete" means

The only acceptable remote verdict is:

```text
make verify-release-completeness TAG=v0.1.0-beta.4
...
COMPLETENESS CHECK: PASS
```

The beta4 verifier requires 28 artifact categories, at least 30 non-empty
assets, a non-draft prerelease, the exact tag version in release artifacts, and
no zero-byte asset. Before publication, CI also verifies artifact contents,
aggregate checksums, digest-pinned image references, canonical Ansible runtime
metadata, and all smoke attestations.

The legacy 12 artifact categories remain a compatibility floor within beta4's
28-category matrix; satisfying those 12 categories alone is not a complete
beta4 release.

## Preconditions

Run these commands from the main checkout. Stop on every non-zero exit.

```text
make check-version-consistency
make check-readme-status TAG=v0.1.0-beta.4
make validate-ansible-runtime-boundary
make check-collection-interop
make gate-all
make ci-verdict-safe BRANCH=development
```

Required evidence:

- `pyproject.toml`, `src/general_ludd/__init__.py`, and the README carry the same
  committed version bump;
- the project version is `0.1.0-beta.4` everywhere;
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

## Cut beta4 with release-cut

```text
make release-cut TAG=v0.1.0-beta.4 MSG='v0.1.0-beta.4: sandbox, local-model, runtime-boundary, and artifact-matrix hardening'
```

The tag-triggered workflow must complete all gate, test, coverage, Molecule,
platform, container, and execution-environment jobs before its release job can
publish. A local poll timeout only means the bounded poll ended; inspect CI at
the next natural break and never treat timeout as success.

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
| Metadata | CycloneDX SBOM, install script, licenses, provenance, checksums | validate schemas, execute installer from the Linux archive, and verify exact SHA-256 coverage |

Every platform job writes a versioned smoke attestation only after its checks
pass. `scripts/verify_release_asset_matrix.py` unions those attestations and
requires all 15 smoke checks before the publishing action runs. Every artifact
upload sets `if-no-files-found: error`.

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

Installer verification happens entirely in the additive staging directory.
Failure blocks publication while the active digest and any previously
published assets remain untouched; rollback is therefore deletion of the
failed candidate staging set, not mutation of the live release. This preserves
zero-downtime service for existing users while a corrected candidate is built.

## Verify with verify-release-completeness

```text
make verify-release-completeness TAG=v0.1.0-beta.4
make release-view TAG=v0.1.0-beta.4
```

Expected release state:

- `isDraft: false`;
- `isPrerelease: true`;
- at least 30 assets;
- all 28 categories report `PASS`;
- no zero-sized asset;
- release URL identifies `v0.1.0-beta.4`.

Record the release URL, exact tag SHA, CI run URL, gate evidence, coverage
evidence, and completeness PASS in the task ledger.

## Repair and rollback

Never upload a locally built replacement to a published release. Only artifacts
built by CI from the exact tagged SHA have valid provenance.

If the tag workflow is green but publication was transiently interrupted:

```text
make release-recut TAG=v0.1.0-beta.4
make verify-release-completeness TAG=v0.1.0-beta.4
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

## Long-lived practitioner failure history

Reviewed on 2026-08-30, two long-running practitioner reports explain why
beta4 fails closed instead of trusting a successful build command or a
well-named transfer artifact:

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
