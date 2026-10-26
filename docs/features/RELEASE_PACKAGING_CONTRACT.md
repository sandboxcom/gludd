# Release Packaging Contract

## Scope

Gludd release inputs are repository-owned build artifacts. The Debian control
file, RPM spec, NSIS installer, portable installer, and PyInstaller spec must
remain tracked on development so a release is reproducible from its exact
commit without borrowing files from another branch or a local scratch path.

## Required invariants

- `dist/debian/control`, `dist/rpm/gludd.spec`,
  `dist/windows/gludd.nsi`, `dist/install.sh`, and `gludd.spec` are
  committed release inputs.
- Version placeholders are resolved by the release job; no package template
  embeds a prior release number.
- PyInstaller collects runtime data and dynamically imported Ansible, Uvicorn,
  Gunicorn, and Gludd modules. Platform-only modules are excluded explicitly so
  analysis is warning-free on Windows, macOS, and Linux.
- Structural verification parses the `Analysis` call as Python syntax. Helper
  variables, keyword arguments, or prose comments cannot masquerade as the
  canonical hidden-import and exclusion lists.
- A wheel or source distribution filename is not identity evidence. The release
  verifier parses the wheel's `METADATA` and the sdist's `PKG-INFO` with the
  standard-library email parser and requires `general-ludd-agent` plus the
  PEP 440-normalized candidate version in both payloads. Renamed or stale
  packages remain red even when their aggregate checksum and release-manifest
  entries were regenerated consistently.
- Build trees and temporary paths remain checkout-namespaced. Parallel projects
  must not share RPM, NSIS, PyInstaller, or checksum state.
- The release gate builds the native macOS executable from the candidate commit
  immediately before `binary_smoke_macos`. The smoke must not borrow a prior
  GitHub release asset, and a Linux artifact cannot satisfy the macOS input.
- The OCI image runs one foreground Gunicorn tree under Tini, binds container
  port 8000, and routes access, error, and captured application output to
  container stdio. CI checks container state on every health attempt and prints
  inspect state plus owned logs before cleanup on failure.

## Upstream and practitioner evidence

Reviewed 2026-08-29. The official
[PyPA Core Metadata specification](https://packaging.python.org/en/latest/specifications/core-metadata/)
defines `Name` and `Version` as required distribution identity fields. Beta4
therefore validates those payload fields instead of inferring identity from an
outer archive name.

PyInstaller issue
[#5360](https://github.com/pyinstaller/pyinstaller/issues/5360) records a 2020
frozen-application failure where a dynamically imported optional dependency was
absent even though the application worked before packaging. Maintainer guidance
was to declare the hidden import explicitly. Issue
[#3997](https://github.com/pyinstaller/pyinstaller/issues/3997) records the
complementary long-lived problem: statically discovered optional GUI modules can
pull unwanted platform code into an artifact, and the supported remedy is an
explicit spec-file exclusion. Together they justify checking both sides of the
bundle boundary rather than treating a successful PyInstaller process as proof
of a runnable artifact.

Reviewed 2026-09-28. Astral's practitioner issue
[#6729](https://github.com/astral-sh/uv/issues/6729) documents the easily missed
distinction between resolving optional dependencies into `uv.lock` and actually
installing an extra into the synchronized environment. A later workspace report,
[#14645](https://github.com/astral-sh/uv/issues/14645), records the same operational
surprise: the expected optional runtime was absent until the caller supplied the
extra explicitly. PyInstaller hidden imports do not install distributions; they
only tell analysis to collect an import that is already available. Gludd therefore
uses `uv sync --frozen --extra azure` and `uv run --frozen --extra azure` on every
frozen-binary path. This keeps ordinary source installs modular while ensuring the
universal release executable really contains the Azure SDK required by its Azure
roles, model lifecycle, and self-improvement backend.

GitHub Actions upload-artifact
[#290](https://github.com/actions/upload-artifact/issues/290), opened in 2022
and reviewed 2026-08-29, records long-lived practitioner trouble with artifact
retention and storage behavior. Gludd treats that transport as untrusted input:
post-download validation checks package-internal identity as well as the release
manifest and digest, so a coherent rename cannot silently become a different
release payload.

Reviewed 2026-08-31. The official
[GitHub CLI release-download manual](https://cli.github.com/manual/gh_release_download)
documents that `--pattern` is a glob and that matching assets are written to
the selected directory; it does not designate one match as the executable.
GitHub CLI issue
[#13961](https://github.com/cli/cli/issues/13961) records the practitioner need
to download an archive and its checksum together and verify them explicitly.
The beta4 publication incident matched that boundary: the broad
`*linux-x86_64*` pattern correctly downloaded both the tarball and checksum,
but the post-deploy script then excluded `*.tar.gz` while searching for its
payload. The smoke now binds the exact versioned tarball, fails if it is absent,
extracts that path directly, and fails if the expected `gludd` entry is absent.
The structural test prohibits the contradictory archive exclusion so a
successful upload cannot mask a broken post-deploy execution path.

The 2026-08-30 full-gate replay exposed the same identity problem one stage
earlier: `binary_smoke_linux` correctly built `dist/linux/gludd`, but the next
macOS scenario looked only for `dist/gludd` and then offered to download the
latest published release. That could not test an unpublished beta4 candidate
and failed closed when no native input existed. The release gate now invokes
the canonical native build target before the macOS scenario, and a regression
pins build-before-smoke ordering. This reuses the candidate checkout and keeps
the transport concerns documented in upload-artifact #290 outside local smoke
identity.

Gunicorn issue
[#1184](https://github.com/benoitc/gunicorn/issues/1184), opened in January
2016 and still a useful practitioner record, documents that access logging is
disabled unless explicitly configured and discusses stdout/stderr behavior.
The 2026-08-21 container-health incident combined a CLI wrapper process with
discarded child stdio, so 30 refused probes exposed no startup cause. Running
Gunicorn directly in the foreground with explicit `-` log destinations removes
that blind spot and gives Tini exact process ownership.

Reviewed 2026-08-31. The official
[GNU make recipe syntax](https://www.gnu.org/software/make/manual/html_node/Recipe-Syntax.html)
documents that Makefiles contain distinct make and shell syntaxes, that recipe
lines are tab-prefixed, and that continued recipe lines form one logical shell
command. A long-lived Stack Overflow practitioner discussion about
[enumerating Make targets](https://stackoverflow.com/questions/4219255/how-do-you-get-the-list-of-targets-in-a-makefile/45843594)
explicitly calls out false positives from simplistic text matching. The beta4
promotion incident was the same class: a whole-file grep matched the guard's own
comments and quoted diagnostics. The release guard now scans only executable
recipe tokens, joins continuations, evaluates shell segments independently, and
fails closed on a real unprefixed push. Its behavioral regression executes the
guard before any artifact build; fixture tests prove that prose cannot satisfy
or trip the policy.

## Zero-downtime delivery

Packaging runs beside the serving release and writes versioned artifacts.
Promotion is allowed only after every platform artifact, installer, checksum,
and smoke result is present. A failed build leaves the serving version and its
artifacts unchanged; rollback selects the preceding immutable release rather
than rebuilding it. This keeps packaging repair out of the live request path.
The image has no migration or additional resident process: the same single
worker is promoted beside the active digest, and rollback repoints new starts to
the preceding digest while existing requests drain.

The metadata verifier is read-only and runs before publication. On an identity
mismatch it changes no staged file, service, database, or model process. Recovery
rebuilds the wheel and sdist from the immutable candidate, regenerates the
manifest and checksums, and reruns the complete verifier; operators never rename
the stale payload or relax the check. The previously serving digest remains the
rollback target throughout.

## Verification

Focused tests assert file presence, executable permissions, package metadata,
portable build paths, dynamic imports, platform exclusions, artifact names, and
cross-platform workflow coverage. The adversarial matrix test changes both
internal identities and regenerates valid checksums, proving that outer naming
cannot bypass the payload check. Release-wide gate and platform CI remain the
authority for promotion.

## Platform-local filelock ctypes analysis, 2026-10-26 UTC

Native Darwin arm64 compilation at source
`c7c01337e8482927eb74c87fee34059a3d5d45f3` uses Python3.12.14 and
PyInstaller6.22.3. Its completed executable/DMG build and outside-checkout
smokes remain evidence for those original bytes. The retained build log emits
`Library ntdll required via ctypes not found`. Installed filelock4.0.12 loads
`ntdll` at `filelock/_windows.py:58`, inside its `sys.platform == "win32"`
branch. Its package initializer still imports `WindowsFileLock` on POSIX, so
the Windows module and the existing spec comment must remain.

PyInstaller's supported `excludedimports` is local to the hooked importer.
The narrow `hook-filelock._windows.py` omits only that dead POSIX ctypes edge;
on Windows it omits nothing. The relative `scripts/pyinstaller_hooks`
registration preserves existing spec path conventions, all other ctypes
consumers, Windows native locking, observer loading and PID ownership.
The exact TDD registration names this required hook basename and its actual
runpy tests; it grants no typing, coverage, frozen-runtime or release exemption.

The unchanged private minimal consumer emits one actual ntdll analysis warning
before the hook and zero with it. Both native frozen binaries preserve public
Windows symbols, real POSIX contention/acquire-release and dynamic ctypes
reachability, with no hidden imports or application modules. Native receipts:
`.gate-logs/observed/ntdll-minimal-baseline/ntdll-minimal-baseline-nativeuv-c7c0133-20261026.json`
(exit0/6.479s) and
`.gate-logs/observed/ntdll-minimal-comparison/ntdll-minimal-comparison-c7c0133-20261026.json`
(exit0/6.242s). Initial UV identity validation failed before building
(exit2/0.561s). Both valid private logs separately retain two UV option warnings
because `--no-sync` is redundant with `--no-project`; they are fixture setup
diagnostics, and neither comparison is claimed globally warning-free.

The module docstring and `excludedimports: list[str]` annotation add
metadata to the tested private hook; its platform predicate and list values are
unchanged. New source acceptance, exact-file 85/75 line/branch coverage on both
installed runtimes, fresh full Gludd compilation/smoke and genuine Windows
artifact acceptance remain separate. Existing Linux915 graph evidence remains
Linux-only. No serving digest, process or published asset changes during this
build-only repair; rollback selects the preceding build inputs and immutable
serving release.

Related firsthand reports
[PyInstaller #1801](https://github.com/pyinstaller/pyinstaller/issues/1801)
(2016, build success followed by a pythonnet runtime failure) and
[PyInstaller #9381](https://github.com/pyinstaller/pyinstaller/issues/9381)
(2026, Windows DLL search precedence) support those proof boundaries. Neither
establishes the cause of this filelock4.0.12 bare-ntdll warning; the retained
installed source and native baseline/comparison establish that attribution.

The three new platform cases first fail only on the original empty hookspath:
3FAIL0.56s/native1.722s at v012-filelock-hook-red/
v012-filelock-hook-red311-8d2c97e-20261026.json. The initial watcher ownership
refusal starts no test and creates no receipt; native work-item support then
declares S98.5's direct S98.4 dependency. No observation JSON is hand-edited.

The complete established owner passes8, including all five original bodies.
Direct strict checks of the actual required hook filename and test file pass
on EACH installed runtime. Initial Ruff/environment/resource/task checks pass
native11.611s; resource inventory remains265. Staged secret/clone/TDD/docstring/
line guards pass7.753s with zero new clones. The exact TDD entry explains the
required non-importable basename; native TDD reports no remaining checkable
staged source. Actual coverage and frozen acceptance retain their own authority.

All16 complete packaging/TDD owners pass211 with6 original skips on EACH:
Python3.11:9.44s/native10.742s and Python3.12:11.63s/native15.869s. Receipts are
v012-filelock-hook-receiving/v012-filelock-hook-receiving{311,312}-8d2c97e-20261026.json.
Each trace has437events/217complete pairs and zero issues, unfinished or failure
reports. The six unchanged TestSystemdUnit cases require the generated
dist/general-ludd.service, absent in this receiving fixture; no new skip lands.

Native exact-file coverage passes8 EACH and measures only the real hook:
2/2 statements,100percent lines, zero excluded lines and zero branch arcs.
The three platform cases exercise both predicate values. Existing85aggregate/
75each line AND branch floors remain unchanged. Receipts are coverage-files/
20261026T231340Z-1915-0d3ca1af.json (3.106s) and
20261026T231341Z-1916-0a6d9ce1.json (4.477s); JSON reports are
.gate-logs/coverage-filelock-hook-{311,312}-8d2c97e-20261026.json.
The native audits analyze exactly one file and retain all thresholds.

Independent source review approves only the installed-API-local hook,
registration and original-test retention. Normal guarded collection/commit,
fresh clean-source full Gludd Mac compilation and outside-checkout executable/
DMG smoke remain next. No build-warning, Windows, Linux, full-gate, hosted,
deployment or stable-release success is inferred from these focused results.
