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
