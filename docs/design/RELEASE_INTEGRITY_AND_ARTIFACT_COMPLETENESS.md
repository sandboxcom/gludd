# Release Integrity & Artifact Completeness — spec + incident record

Status: ACTIVE (2026-07-14) — audit complete; several requirements implemented
in the working tree this session (marked ✅/🔧 below), remainder specced for
the next release.
Owner: release engineering
Motivating incident: v0.1.0-beta.1 (published 2026-07-14T18:40:54Z).

## 1. Problem statement (verified evidence)

The v0.1.0-beta.1 GitHub release shipped **incomplete and against a RED
pipeline**, despite the repo having both a `require-ci-green` gate and a
release-completeness verifier:

- `make verify-release-completeness TAG=v0.1.0-beta.1` →
  `COMPLETENESS CHECK: FAIL — 13 check(s) failed.` The release carries **1
  asset** (`gludd`, 54,882,160 bytes) of a required **≥ 12**.
- The tag resolves to commit `260923839e58a022d4c95ccbeba96ca600403d11`;
  `make ci-verdict SHA=26092383…` → `CI RED` (run 29351112456,
  conclusion=failure). No "Build and Release" run in recent history is green.
- The release was marked `prerelease=False` despite the `-beta` tag
  (🔧 FIXED live 2026-07-14: `make release-set-prerelease TAG=v0.1.0-beta.1`
  → `prerelease=True`, output pasted in session log).
- Local `dist/` contained build **inputs** but not outputs; the tarball inputs
  `dist/install.sh`, `dist/README.md`, `dist/general-ludd.service` had been
  deleted from the tree entirely (🔧 RESTORED from history via new
  `git-restore-from` target; `make dist` now completes, exit 0).

Audit-confirmed root causes (verifier/CI wiring review, file:line verified):

1. **The verifier ran nowhere.** Neither `verify_release_completeness.py` nor
   `make verify-release-completeness` appeared anywhere in the 824-line
   `.github/workflows/build.yml`; `release-cut`/`release-recut` polled only
   `verify-release-artifact` (non-draft + ≥1 asset).
2. **`release-create` was fully ungated**: no CI gate, single binary, swallowed
   `gh release create` failures via `|| echo "release-create-failed"`, no
   `--prerelease`. This is the path beta.1 shipped through.
3. **Threshold drift**: CI's inline check demanded ≥6 assets (names uncounted —
   six `.sha256` files would pass) while the verifier demanded ≥12.
4. **`release-recut` had no `require-ci-green` at all** despite re-pushing tags.
5. **Non-blocking artifact jobs**: `windows` (build.yml:551) and `termux` (:605)
   are `continue-on-error: true`, so a release could publish without their
   artifacts and still pass the old inline check.
6. `bundle-ripgrep` sha pin was an all-zeros placeholder → ripgrep never
   bundled (fail-closed) since introduction.
7. Verifier `_resolve_repo` used `rstrip(".git")` (char-set strip, latent
   mangling bug) and never checked `isPrerelease`, version-match, or sizes.

## 2. Requirements and status

**R-1 — No ungated publish path.** ✅ IMPLEMENTED (this session):
`release-create` now runs `require-ci-green`, publishes **draft-only** with
`--prerelease` auto-set for hyphen tags, and no longer swallows errors;
`release-recut` now runs `require-ci-green SHA=$(git rev-parse TAG^{commit})`;
both `release-cut` and `release-recut` now finish by running
`verify-release-completeness` (exit code propagated) instead of stopping at
the ≥1-asset check. REMAINING (next release): a test harness for these targets
(dry-run mode or script extraction) so gate regressions are caught by CI.

**R-2 — CI builds the full artifact matrix on tag push.** PARTIAL: the
uncommitted build.yml adds .deb/.rpm/.dmg/.exe jobs with upload paths that
match the Makefile outputs and verifier categories. REMAINING: decide policy
for `continue-on-error` on `windows`/`termux` — either make them blocking for
tag builds, or accept that the completeness verifier fails the release job
loudly when their artifacts are missing (current behavior after R-3).

**R-3 — Completeness verification is a blocking CI job.** ✅ IMPLEMENTED
(this session): the release job's inline `assets|length >= 6` step is replaced
by `uv run python scripts/verify_release_completeness.py <tag> <repo>`
(GH_TOKEN provided). REMAINING (next release): mark the release **draft**
before verification and un-draft only on PASS, so an incomplete release is
never publicly visible even transiently.

**R-4 — Asset repair path.** ✅ IMPLEMENTED: `make release-upload-assets
TAG=… FILES=…` (idempotent via `--clobber`) + `make release-set-prerelease
TAG=…`. Provenance rule: only CI-built artifacts from the tagged SHA may be
uploaded to a tagged release — never locally-built binaries from a different
tree (this is why beta.1 was NOT back-filled from the local dist this session).

**R-5 — Checksums, SBOM, licenses as first-class dist outputs.** PARTIAL:
`make dist` emits per-tarball `.sha256`, `dist/sbom.json`, and packs
LICENSE/THIRD_PARTY_LICENSES into the tarball; CI aggregates `SHA256SUMS`.
REMAINING: `make dist` should also emit a top-level `SHA256SUMS` covering all
artifacts so local and CI layouts match the verifier identically.

**R-6 — Prerelease flag correctness.** ✅ IMPLEMENTED: verifier now fails when
`isPrerelease` mismatches the tag shape (`-alpha|-beta|-rc`); `release-create`
sets `--prerelease` for hyphen tags; CI already did
(`prerelease: contains(ref_name, '-')`). beta.1's flag corrected live.

**R-7 — Verifier hardening.** PARTIAL (this session): added prerelease-flag
check, version-stamped-asset check, zero-size-asset check; fixed
`removesuffix(".git")`; queries `isPrerelease`. Tests now exercise the real
`check_completeness()` via mocked gh JSON: 34 passed
(`make test-iso TESTFILE=tests/unit/test_verify_release_completeness.py`).
REMAINING (next release):
  - download + validate checksum-file digests against actual assets;
  - per-category uniqueness (one asset must not satisfy two categories, e.g.
    a `.deb` name also matching "linux-x86_64 binary");
  - container-image (GHCR) parity check with the workflow's `container` job;
  - signature/provenance verification once artifacts are signed.

**R-8 — Local packaging targets (CORRECTED 2026-07-14 — the original claim was
WRONG; do not act on it).**

~~`$(VERSION)` is undefined in the Makefile~~ — **REFUTED.** `Makefile:2771`
defines it: `VERSION := $(shell $(UV) run python -c "from general_ludd import
__version__; print(__version__)")`. It resolves correctly from
`src/general_ludd/__init__.py`, and `.deb`/`.rpm` substitute it into their
`VERSION_PLACEHOLDER` templates (`Makefile:2938`, `:2952`). The earlier audit
claim that a local `make deb-package` yields an empty `Version:` was false.
Left here as a record, because a spec that sends someone to "fix" working code
is worse than no spec.

**Real, remaining issues in the same area:**
- **`dist/windows/gludd.nsi` silently defaults to version `0.0.0`.** It has
  `!ifndef VERSION` / `!define VERSION "0.0.0"`. The Makefile *does* pass
  `-DVERSION=$(VERSION)` (`:2992`) and CI does too (build.yml:632), so it is not
  broken today — but if either ever stops passing it, makensis emits
  `gludd-0.0.0-setup-x86_64.exe` **without erroring**. Given that beta.1 shipped
  1-of-12 assets unnoticed, a silently mislabelled installer is exactly the
  failure class to eliminate. Fix: `!ifndef VERSION` → `!error "VERSION must be
  passed via -DVERSION"`. Effort: S.
- `macos-dmg` hardcodes `-macos-arm64` (`DMG_NAME`, `:2964`) regardless of host
  arch — an x86_64 mac would produce a mislabelled dmg. Effort: S.
- ~~`rpm-package` uses a fixed shared `/tmp/gludd-rpmbuild`~~ — **RESOLVED for
  beta.3.** It now uses the checkout-local absolute `dist/rpmbuild` path, so
  parallel projects and release worktrees cannot delete each other's RPM tree.
- `dist/rpm/gludd.spec:30` has a hardcoded changelog date. Cosmetic.

**R-15 — beta.3 Linux/Windows packaging incident (RESOLVED 2026-07-28).**

Build-and-Release run `30331174104` exposed two distinct failure classes:

- **Linux/RPM was repository-controlled.** `rpm-package` asked
  `mkdir -p /tmp/gludd-rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}` to create the
  RPM tree. GNU make runs recipes with `/bin/sh` unless configured otherwise;
  Ubuntu's shell does not implement Bash brace expansion. It therefore created
  one literal brace-named directory and the next `cp` failed because
  `SOURCES/` did not exist. This is the exact long-lived failure demonstrated
  in the 2018 user report
  [“cannot create multiple directories with makefile”][make-brace-report].
  The [GNU make shell documentation][gnu-make-shell] confirms the `/bin/sh`
  contract. The target now creates every directory explicitly (portable POSIX
  shell), uses the namespaced checkout-local tree, and has regression guards
  against both brace expansion and a shared `/tmp` root.
- **Windows was repository-controlled.** The hosted runner stopped during
  “Getting action download info” with `Unable to resolve action
  actions/checkout@...`; no checkout, dependency install, build, packaging, or
  project code ran. The configured `d632...a2e4` hash does not exist: the
  [official checkout v4.2.0 release][checkout-v420] resolves to
  `d632683dd7b4114ad314bca15554477dd762a938`. The open `actions/checkout` issue
  [#1562][checkout-resolution-report] documents the same error signature over
  multiple years, which is why the raw message alone was not enough to call
  this a bad pin; checking the official release identified the repository
  typo. Windows also floated `setup-uv@v5` while maintained jobs used immutable
  Node 24 pins. It now uses the repository's canonical checkout v5.0.1 and
  setup-uv v8.2.0 hashes. The failed hosted runner was `2.336.0`, newer than the
  [checkout v5 minimum of `2.327.1`][checkout-node24], so the selected Node 24
  runtime is supported.
- **Windows packaging now fails closed and is reproducible.** The job pins the
  Windows 2022 image and Python 3.12, makes binary smoke tests blocking before
  packaging, pins NSIS 3.12.0, promotes NSIS warnings to errors, creates
  portable `Get-FileHash` sidecars, and makes missing uploads fatal. A required
  Windows artifact failure can no longer be hidden by `continue-on-error` or
  `if-no-files-found: warn`.

Regression coverage lives in
`tests/unit/test_packaging_templates_committed.py` (portable, namespaced RPM
tree) and `tests/unit/test_ci_regression_guards.py` (canonical immutable
Windows bootstrap actions).

[make-brace-report]: https://stackoverflow.com/questions/49099682/cannot-create-multiple-directories-with-makefile/49100159
[gnu-make-shell]: https://www.gnu.org/software/make/manual/html_node/Choosing-the-Shell.html
[checkout-v420]: https://github.com/actions/checkout/releases/tag/v4.2.0
[checkout-resolution-report]: https://github.com/actions/checkout/issues/1562
[checkout-node24]: https://github.com/actions/checkout#checkout-v5

**R-16 — Molecule workflow Node 20 fallback (RESOLVED 2026-08-20).**

GHE run `32437385366` reported that the floating `checkout@v4`,
`setup-python@v5`, and `setup-uv@v5` actions declared Node 20 and were being
forced onto Node 24 by the runner. A [2026 practitioner discussion][node24-noise]
documents that opting into the newer runtime does not remove this warning:
the action's declared runtime must be upgraded. The official
[setup-python v6 release][setup-python-v6] also requires runner `2.327.1` or
newer; this repository's observed GHE runner `2.336.0` satisfies that bound.

The Molecule workflow now uses the same immutable, Node 24 checkout v5.0.1 and
setup-uv v8.2.0 pins as the maintained build jobs, plus immutable setup-python
v6.2.0. Structural tests require those exact hashes and reject any floating
third-party action in every workflow. This is a zero-downtime CI-only rollout:
there is no service or artifact migration, all action inputs remain compatible,
and the six bounded Molecule shards are unchanged. Rollback restores the three
workflow refs together; no persistent state is written beyond ordinary runner
caches, and the existing 45-minute job timeout and concurrency group retain
their resource bounds.

[node24-noise]: https://github.com/orgs/community/discussions/190988
[setup-python-v6]: https://github.com/actions/setup-python/releases/tag/v6.2.0

**R-13 — The `/Users/` leak guard covers only the tarball (NEW, and it already
cost us).** The only developer-path guard is
`grep -rIl -e '/Users/' -e 'Mac.localdomain' $(TARBALL_DIR)` — implemented twice
(inline in `dist` at `Makefile:2920-2923`, and as `dist-path-check` at
`:2323-2328`) — and **both only check `dist/general-ludd-agent-*` after a build**.
`dist-path-check` has **zero references in `.github/`**: it is not wired into
`make gate`, `make lint`, or any workflow. So `molecule/`, `playbooks/`,
`roles/`, `collections/`, `src/`, `scripts/` and the Makefile itself are
**entirely unguarded** — which is precisely why four molecule scenarios shipped
with hardcoded `/Users/shawnwilson/...` paths that cannot exist on a CI runner,
and why a **shipped** collection role
(`collections/.../roles/log_analyzer/playbook.yml`) carried a personal
pytest-tmp path. Note `make ansible-syntax` only covers `playbooks/*.yml` and
`make lint` is ruff over `src`+`tests`, so **neither is structurally capable of
catching a YAML path bug**. Fix: a repo-wide tracked-file lint (excluding
`docs/` and the guard's own lines) wired into `gate` + CI. Effort: S.

**R-12 — Installer FORMATS are wrong for a daemon, and nothing is signed (NEW,
operator-raised 2026-07-14).**

*Windows — `.exe` should be `.msi`.* `make windows-installer` builds an NSIS
`.exe` (`dist/windows/gludd.nsi`), and the verifier's category is literally
`.exe installer (Windows)`. An NSIS `.exe` is fine for a consumer GUI app, but
gludd is a **service**, and an `.exe` is effectively opaque to managed
deployment. **MSI is the correct format**: it is what `msiexec`, Group Policy,
Intune and SCCM consume, and it provides real silent-install semantics
(`/qn`), proper Add/Remove Programs registration, per-machine installs,
upgrade/downgrade logic via ProductCode/UpgradeCode, and transactional
rollback. Recommend **WiX Toolset** to produce a signed `.msi`, and update
`EXPECTED_CATEGORIES` in `verify_release_completeness.py` accordingly (accept
`.msi`; keep `.exe` only if we deliberately ship both).

*macOS — `.dmg` is present but is not an installer.* We DO already ship a
`.dmg` (`make macos-dmg`), so that category is satisfied. But a `.dmg` is a
**disk image**, not an installer: it cannot install a launchd plist, create
`/usr/local/var` state dirs, or run post-install steps. Today's dmg just wraps
the binary plus a shell `install.sh`, which is why `dist/install.sh` is copied
into the staging dir at all. For a **daemon**, the correct macOS artifact is a
**`.pkg`** (productbuild/pkgbuild), which can run preinstall/postinstall
scripts and register the launchd job. Recommend shipping a signed+notarized
`.pkg` as the primary macOS installer and keeping the `.dmg` (or a plain
tarball) as the manual/portable option.

*Signing/notarization is the bigger gap than either format.* Nothing we ship is
signed. An unsigned `.dmg`/`.pkg` is blocked by **Gatekeeper** ("cannot be
opened because the developer cannot be verified") unless the user right-click-
opens or runs `xattr -d com.apple.quarantine`; an unsigned `.exe`/`.msi` trips
**SmartScreen**. A signed-but-unnotarized macOS artifact still fails on modern
macOS — notarization (`notarytool` + stapling) is required, not optional.
Needs: an Apple Developer ID Application + Installer cert and an Authenticode
(ideally EV/Azure Trusted Signing) cert, stored as CI secrets. **Until signing
exists, document the manual bypass in the release notes** rather than letting
users hit an opaque OS block. Add signature verification to the completeness
verifier once signing lands (R-7 already reserves this).

**R-10 — CI concurrency must not evict a commit's only verdict (NEW, CRITICAL —
this is the mechanism that made beta.1 possible).**
`.github/workflows/build.yml:46-48`:
```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```
On `push`, `cancel-in-progress` is **false**, so runs queue — and GitHub keeps
only **one pending run per group**. Every new push to `development` therefore
**silently cancels the queued run for the previous commit**. Observed directly:
run 29362980590 for `0b6237c4` reports `"jobs":[]` — it went pending at 19:44:34,
was cancelled at 19:47:16 (one second after the next push), and **never executed
a single job**. Runs 29364608983 / 29364610894 / 29364661863 died the same way.

**Consequence:** a commit can reach a tag having **never been tested**, and
`make ci-await BRANCH=<branch>` can never return a stable verdict against a
moving branch. A cancelled run is not a failure and not a success — it is the
*absence* of a gate. This is precisely how v0.1.0-beta.1's SHA went out
unvalidated.

**Fix:** the release gate must bind to a **SHA**, not a branch. Concretely:
(a) `require-ci-green` must fail-closed on `cancelled`/missing runs, never treat
"no completed run" as anything but a hard stop (verify it does); (b) drop the
push-time concurrency group, or scope the group to the SHA
(`${{ github.workflow }}-${{ github.sha }}`) so a new push cannot evict an older
commit's verdict; (c) prefer `make ci-verdict SHA=<sha>` over branch-based
awaits everywhere in the release path.

**R-11 — The release job can never fire while `test-shard` is chronically red
(NEW).** `build.yml:743-747` gates `release` on
`needs: [version, gate, test-shard, linux, macos, windows, termux, container]`.
`test-shard` has a **long-standing red baseline** (10/12 shards failing on run
29363154848, and failing *harder* on parent commits — 404 failures at `ad09cc0a`
vs 376 at `079b7f5a`). So the sanctioned `release-cut` path is **mechanically
unreachable**, which is exactly the pressure that pushed the last release through
the ungated `release-create` fallback. **Fixing the red baseline is therefore a
release-integrity requirement, not just hygiene.** The failures are a small,
tractable set (see beta.2 Wave 0): a stale `.opencode/plugin/shared.ts` test path
(the file moved to `.opencode/lib/`), one broken autouse fixture
(`test_bill1_slurm_billing_wiring.py:23-29` assigns into `_daemon_state` while it
is still `None`), a default `allowed_cidr` that rejects TestClient's `testclient`
pseudo-host, and the known xdist `/tmp` race. Molecule is **not** in `needs`, so
it does not block releases.

**R-9 — Bundled-binary pins stay real (NEW).** 🔧 ripgrep sha fixed this
session (official `4cf9f274…4d8e`, verified: `shasum -c` OK, bundled).
FIXED: osquery macOS asset name 404
(`osquery-5.10.2.macos_arm64.tar.gz` did not exist upstream —
`bootstrap.py::_osquery_download_url` now builds the real 5.10.2 asset
names, `osquery-5.10.2_1.macos_x86_64.tar.gz` for darwin any-arch and
`osquery-5.10.2_1.linux_{x86_64,aarch64}.tar.gz` for linux; verified via a
live `make bundle-binaries` run — osquery downloaded a real ~24MB tarball,
HTTP 200, no 404). REMAINING: add a CI check that every pinned URL+sha in
`scripts/download_bundled_binaries.py` and the Makefile actually resolves
(HEAD request) so dead pins are caught before release week.

## 3. Acceptance criteria (next release)

- `make verify-release-completeness TAG=<next-tag>` prints literal
  `COMPLETENESS CHECK: PASS` against the published release.
- CI run for the tag SHA is green before the release is visible non-draft.
- A deliberate `release-create` on a RED SHA aborts at `require-ci-green`.
- Beta tags show `Pre-release` on GitHub.
- `make dist` from a clean checkout completes with every input present in-tree.

## 4. v0.1.0-beta.1 disposition

The published beta.1 (1 asset, RED tagged SHA) cannot be honestly completed:
the missing 11 artifacts must come from a green CI build of the exact tagged
SHA, and that SHA is RED. Back-filling with locally-built dev-tree binaries
would misrepresent provenance. Disposition options (operator decision,
outward-facing): (a) leave beta.1 as a marked prerelease with a
release-notes caveat and cut **v0.1.0-beta.2** from the next green SHA through
the now-gated pipeline (RECOMMENDED); (b) `make release-recut TAG=v0.1.0-beta.1`
only if its exact SHA is first made green. Pipeline fixes in this tree make
either path fail-closed.
