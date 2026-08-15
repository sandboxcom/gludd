# Release runbook

The one-page answer to "how do I cut a release, and how do I know it actually
shipped?" Written after v0.1.0-beta.1 was published **incomplete** (1 of 12
required assets, against a RED commit, mis-flagged as a stable release) because
every safety net in this repo was either bypassed or not wired in.

## The rule

**A tag is not a release. A release with assets is not a *complete* release.**
The only machine-checkable definition of "shipped" is:

```text
make verify-release-completeness TAG=v0.1.0-beta.2
...
COMPLETENESS CHECK: PASS — all 16 checks passed.
```

Anything short of that literal `PASS` line is not a release, no matter what the
GitHub UI shows.

## Beta.2 release — exact steps once CI is green

A linear, copy-pasteable walk-through for `v0.1.0-beta.2`. Run each step in
order; **stop and fix** on any non-zero exit. Do not skip ahead.

### 0. Preconditions

- Working tree clean: `make git-status` shows no uncommitted changes.
- Version already bumped to `0.1.0-beta.2` in `pyproject.toml`,
  `src/general_ludd/__init__.py`, `CHANGELOG.md`, and the README status table
  (`**Status as of v0.1.0-beta.2 — <date>**`).
- `make check-readme-status TAG=v0.1.0-beta.2` exits 0.

### 1. Verify CI is GREEN on `development`

```text
make ci-verdict-safe BRANCH=development
```

Requires `conclusion: success` **and** `headSha == development tip`. If PENDING
→ wait and resume other work; re-check at the next natural break (the cooldown
is 10 min — do NOT poll tighter). If RED → fix-forward on `development`, do not
proceed. A cancelled run is **not** a verdict (see "A cancelled CI run is NOT a
verdict" below); treat cancelled/no-run as red.

### 2. Merge `development` → `master` (main checkout only)

```text
make development-merge-to-master
```

This performs a `--no-ff` merge on the **main checkout** (`/Users/shawnwilson/gludd`),
never inside a worktree. It requires CI green on `development`. After it
completes, verify `master` tip matches the merged commit:

```text
make verify-remote BRANCH=master SHA=$(make git-rev-parse REF=HEAD)
```

Expect `VERIFIED master@<sha>`. A `REMOTE MISMATCH` means the push did not land
— re-run the push, do not proceed.

### 3. Cut the release

```text
make release-cut TAG=v0.1.0-beta.2 MSG='v0.1.0-beta.2: <one-line summary>'
```

This is the **only** sanctioned release path. It is fail-closed at every step:

1. `require-ci-green` — aborts unless CI is GREEN for the exact SHA being tagged.
2. `check-readme-status` — the README status table must be current for this tag.
3. push, then annotated tag + push (this is what triggers the CI release job).
4. `release-view` — confirm the GitHub Release exists.
5. poll, then **`verify-release-completeness`** — its exit code is the verdict.

`release-cut`'s local poll gives up after ~10 min. A cold, tag-triggered
full-matrix build runs **30–60 min**, so a poll timeout means **"still
building"**, not failure. Do not conclude the release is broken on a poll
exhaustion — proceed to step 4.

### 4. Verify the 12 assets

```text
make verify-release-completeness TAG=v0.1.0-beta.2
```

This is the **real gate** (not `verify-release-artifact`). It requires all 12
artifact categories (see "What 'complete' means" below), the prerelease flag
matching the `-beta` tag, version-stamped asset names, and no zero-size assets.
Expect:

```text
COMPLETENESS CHECK: PASS — all 16 checks passed.
```

If CI is still building, the check will report missing assets — wait and re-run
rather than declaring failure. If CI is **complete** and assets are still
missing, the release is broken; see "If CI is red for the tag" below.

### 5. Publish / confirm

Once `verify-release-completeness` passes, the GitHub Release created by
`release-cut` step 4 is already public and non-draft. Confirm:

```text
make release-view TAG=v0.1.0-beta.2
```

Expect `isDraft: false`, `isPrerelease: true`, and ≥12 assets listed. Paste the
release URL and the `COMPLETENESS CHECK: PASS` line as the completion evidence
in `TASKS.md` — without both, the release task is **not** done.

### Rollback / repair

If the tagged SHA turned red after tagging, or assets are incomplete on a
**completed** CI run: do **not** back-fill locally-built binaries. Either
`make release-recut TAG=v0.1.0-beta.2` (requires CI-green on the tag) or cut
`v0.1.0-beta.3` from a green SHA and mark beta.2 superseded in its notes.

For an already-published release missing only CI-built artifacts:
`make release-upload-assets TAG=v0.1.0-beta.2 FILES='...'` (CI-built, tagged-SHA
artifacts only), then `make release-set-prerelease TAG=v0.1.0-beta.2`, then
re-run `verify-release-completeness`.

Preconditions: the working tree must be clean, and the version must already be
bumped in `pyproject.toml`, `__init__.py`, `CHANGELOG.md`, and `README.md`.

## What "complete" means

`scripts/verify_release_completeness.py` requires **12 artifact categories** and
at least 12 assets:

| | |
|---|---|
| Platform binaries | linux-x86_64, linux-aarch64, macos-arm64, windows-x86_64 |
| Packages | `.deb` (amd64), `.rpm` (x86_64), `.dmg` (macOS), `.exe` installer |
| Metadata | checksums, SBOM, `LICENSE`, `THIRD_PARTY_LICENSES` |

Plus: the release must be **non-draft**; its **prerelease flag must match the tag
shape** (any `-alpha`/`-beta`/`-rc` tag must be marked prerelease, and a stable
tag must not be); asset names must carry the **tag's version**; and **no asset may
be zero-size**.

CI runs this script as a **blocking step** in the release job — an incomplete
release fails the workflow.

## Local signature validation

`make release-validate` signs `MANIFEST.json` with `GLUDD_SIGNING_KEY` (or the
default SSH key) and verifies it with `ssh-keygen -Y verify`. The command creates
an artifact-scoped `dist/release.allowed_signers` from the matching public key;
it does not fall back to an unprovisioned user-home trust file. Set
`GLUDD_ALLOWED_SIGNERS` when validation must use an externally managed trust
store instead. Keep the public key paired with the release signing key.

## Container artifact is fail-closed

When release validation is run with container building enabled, the validator
requires `dist/container-image-tags.json` and checks that it references the
release version. A failed container build or missing tag metadata therefore
fails validation; it cannot be silently treated as a pip-only release. The
pip-only path remains available when container building is intentionally
disabled.

## Traps

**`make verify-release-artifact` is not the gate.** It only proves "non-draft and
at least one asset exists." A release with one binary and no SBOM, no checksums,
and no Linux build passes it. Use `verify-release-completeness`.

**A poll timeout means "still building", not "failed."** A cold, tag-triggered
full-matrix build runs **30–60 minutes**, while `release-cut`'s local poll gives
up after ~10. If the poll exhausts, do **not** conclude the release is broken —
re-check later with `make verify-release-completeness TAG=...`.

**`make release-create` cannot publish.** It is a CI-green-gated, **draft-only**
single-binary fallback that prints an `INCOMPLETE RELEASE` banner. It exists for
bootstrap situations only. (Before it was gated, it was exactly how beta.1
escaped: no CI check, one binary, errors swallowed by `|| echo`, no prerelease
flag.) Finish a draft by uploading the real CI artifacts, passing the
completeness check, then un-drafting.

## Repairing a published release

```text
make release-upload-assets TAG=v0.1.0-beta.2 FILES='dist/a.tar.gz dist/b.deb'
make release-set-prerelease TAG=v0.1.0-beta.2
make verify-release-completeness TAG=v0.1.0-beta.2
```

`release-upload-assets` is idempotent (`--clobber`), so re-running is safe.

**Provenance rule — this is not negotiable.** Only ever upload **CI-built
artifacts from the tagged SHA**. Never upload locally-built binaries from a
development tree: they would carry a different build than the tag claims, which
is a lie about what users are running. If the tagged SHA is red and cannot
produce artifacts, the honest move is to **cut a new tag from a green SHA**, not
to back-fill the old one.

## A cancelled CI run is NOT a verdict

`.github/workflows/build.yml` queues push-triggered runs in a concurrency group
keyed on the **branch**, and GitHub keeps only **one pending run per group**. So
**every new push to a branch silently cancels the queued run for the previous
commit**. A cancelled run executes zero jobs — it is neither pass nor fail, it is
the *absence* of a gate.

Practical consequences:

- **`make ci-await BRANCH=...` cannot give a stable verdict on a moving branch.**
  If pushes keep landing, it will wait forever while each run is evicted.
- **Always check a SHA, not a branch:** `make ci-verdict SHA=<full-sha>`.
- Before tagging, confirm the tag's exact commit has a **completed, successful**
  run. "No run" and "cancelled" both mean *not validated* — treat them as red.

This is not hypothetical: the commit `0b6237c4` had its run cancelled after
2m42s having run **zero jobs**, and that class of eviction is what let a
never-validated SHA get tagged as v0.1.0-beta.1.

## If CI is red for the tag

You cannot honestly complete that release. Either make the exact tagged SHA green
and `make release-recut TAG=...` (which now also requires CI-green), or cut the
next patch/beta from a green SHA and note the superseded tag in the release
notes. Do not paper over it.

## Local artifact builds

`make dist` builds the executable, bundles binaries, generates the SBOM, and
assembles the tarball. Two things to know:

- **`dist/` is half-tracked.** Build *inputs* (`install.sh`, `README.md`,
  `general-ludd.service`, `debian/control`, `rpm/gludd.spec`, `windows/gludd.nsi`)
  are committed; build *outputs* are gitignored. Deleting `dist/` breaks
  `make dist`. Restore inputs with
  `make git-restore-from REF=<sha> FILES='dist/install.sh ...'`.
- The Linux and Windows artifacts come from the **CI matrix**, not a macOS
  laptop. A local `make dist` cannot produce the full 12-asset set — that is
  expected, and it is why the release must come from CI.
