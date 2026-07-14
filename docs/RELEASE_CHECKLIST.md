# Release Checklist

A step-by-step checklist for cutting a release. Every step is gated — do not
skip ahead. A tag is not a release, and "has assets" is not a *complete* release:
it is not done until **`make verify-release-completeness TAG=...` passes**.

Full procedure and rationale: **`docs/RELEASE_RUNBOOK.md`**.

> **`make verify-release-artifact` is NOT the gate.** It only proves "non-draft and
> at least one asset exists" — a release with one binary and no SBOM, no checksums
> and no Linux build passes it. That is precisely how v0.1.0-beta.1 shipped with 1
> of 12 required assets. Use `verify-release-completeness`.

## 1. Pre-flight

- [ ] Version bumped **everywhere**: `pyproject.toml`, `src/general_ludd/__init__.py`, `CHANGELOG.md`.
- [ ] README status current: the **Feature & Task Completion Status** table and the
      `**Status as of <version>**` line reflect the version being cut.
      Verify with `make check-readme-status TAG='<tag>'`.
- [ ] `CHANGELOG.md` updated with the new version's entries.
- [ ] Working tree clean and committed (`make git-status`).

## 2. CI must be GREEN

- [ ] `make ci-verdict BRANCH=master` reports `conclusion: success`.
- [ ] The run's `headSha` **equals** the local HEAD of `master` (no `STALE RUN WARNING`).
- [ ] A stale run (headSha != branch tip) does NOT count — wait for a fresh green run.

## 3. Cut the release

- [ ] `make release-cut TAG='<tag>' MSG='<message>'`

  This is the **only sanctioned path**. It runs, aborting on the first failure:
  1. `require-ci-green` — CI not green for the exact SHA = ABORT
  2. `check-readme-status` — README stale = ABORT
  3. `git-push-sandboxcom` — push master
  4. `git-tag-push` — annotated tag + push (triggers the CI release job)
  5. `release-view` — confirm the GitHub Release exists
  6. poll, then `verify-release-completeness` — its exit code is the verdict

  **`make release-create` is not an alternative.** It is a CI-green-gated,
  **draft-only** single-binary fallback for bootstrap situations and cannot
  publish a public release.

## 4. Verify completeness (the real gate)

- [ ] `make verify-release-completeness TAG='<tag>'` exits 0 and prints `PASS`.
- [ ] Record the artifact download URL(s) and the CI run id.

`verify-release-completeness` requires **12 artifact categories**:

| | |
|---|---|
| Platform binaries | linux-x86_64, linux-aarch64, macos-arm64, windows-x86_64 |
| Packages | `.deb`, `.rpm`, `.dmg`, `.exe` installer |
| Metadata | checksums, SBOM, `LICENSE`, `THIRD_PARTY_LICENSES` |

Plus: the release must be **non-draft**; the **prerelease flag must match the tag
shape** (`-alpha`/`-beta`/`-rc` ⇒ prerelease, stable tag ⇒ not); asset names must
carry the **tag's version**; and **no asset may be zero-size**. CI runs this same
script as a **blocking** step on tag builds.

> **A poll timeout means "still building", not "failed."** A cold tag-triggered
> matrix build takes **30–60 minutes**, while `release-cut`'s local poll gives up
> after ~10. Do not conclude the release is broken — re-check later with
> `make verify-release-completeness TAG='<tag>'`.

A tag with an incomplete asset set is **NOT shipped** — fix CI, cut a new release,
do not bump the version.

### If a published release needs repair

- [ ] `make release-upload-assets TAG='<tag>' FILES='...'` (idempotent, `--clobber`)
- [ ] `make release-set-prerelease TAG='<tag>'` if the prerelease flag is wrong
- [ ] Re-run `make verify-release-completeness TAG='<tag>'`

**Provenance rule — not negotiable.** Only ever upload **CI-built artifacts from the
tagged SHA**. Never upload locally-built binaries from a development tree — that
falsifies what users are running. If the tagged SHA is red, cut a new tag from a
green SHA instead of back-filling the old one.

## 5. Post-release

- [ ] Tick the release row in `TASKS.md` with evidence: the `verify-release-completeness` PASS line, artifact URL, CI run id + `conclusion: success`, commit hash.
- [ ] Update `SESSION.md`: last commit hash, completed objective, next steps.
- [ ] Push any docs-only changes (`make batch-push`).
- [ ] Do NOT open the next-version epic until this version's completeness check passes.

## 6. Local artifact builds

`make dist` builds the executable, bundles binaries, generates the SBOM, and
assembles the tarball. Two things to know:

- **`dist/` is half-tracked.** Build *inputs* (`install.sh`, `README.md`,
  `general-ludd.service`, `debian/control`, `rpm/gludd.spec`, `windows/gludd.nsi`)
  are hand-authored; build *outputs* are gitignored. **Deleting `dist/` breaks
  `make dist`.** Use `make dist-clean` to remove only the outputs.
- A local `make dist` **cannot** produce the full 12-asset set — the Linux and
  Windows artifacts come from the **CI matrix**, not a developer laptop. That is
  expected, and it is why the release must come from CI.
