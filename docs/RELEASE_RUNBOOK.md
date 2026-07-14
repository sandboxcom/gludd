# Release runbook

The one-page answer to "how do I cut a release, and how do I know it actually
shipped?" Written after v0.1.0-beta.1 was published **incomplete** (1 of 12
required assets, against a RED commit, mis-flagged as a stable release) because
every safety net in this repo was either bypassed or not wired in.

## The rule

**A tag is not a release. A release with assets is not a *complete* release.**
The only machine-checkable definition of "shipped" is:

```
make verify-release-completeness TAG=v0.1.0-beta.2
...
COMPLETENESS CHECK: PASS — all 16 checks passed.
```

Anything short of that literal `PASS` line is not a release, no matter what the
GitHub UI shows.

## Cutting a release

```
make release-cut TAG=v0.1.0-beta.2 MSG='release notes'
```

That is the **only** sanctioned path. It is fail-closed at every step:

1. `require-ci-green` — aborts unless CI is GREEN for the exact SHA being tagged.
2. `check-readme-status` — the README status table must be current for this tag.
3. push, then annotated tag + push (this is what triggers the CI release job).
4. `release-view` — confirm the GitHub Release exists.
5. poll, then **`verify-release-completeness`** — its exit code is the verdict.

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

```
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
