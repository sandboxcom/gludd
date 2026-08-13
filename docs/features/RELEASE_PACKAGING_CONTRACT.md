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
- Build trees and temporary paths remain checkout-namespaced. Parallel projects
  must not share RPM, NSIS, PyInstaller, or checksum state.

## Practitioner evidence

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

## Zero-downtime delivery

Packaging runs beside the serving release and writes versioned artifacts.
Promotion is allowed only after every platform artifact, installer, checksum,
and smoke result is present. A failed build leaves the serving version and its
artifacts unchanged; rollback selects the preceding immutable release rather
than rebuilding it. This keeps packaging repair out of the live request path.

## Verification

Focused tests assert file presence, executable permissions, package metadata,
portable build paths, dynamic imports, platform exclusions, artifact names, and
cross-platform workflow coverage. Release-wide gate and platform CI remain the
authority for promotion.
