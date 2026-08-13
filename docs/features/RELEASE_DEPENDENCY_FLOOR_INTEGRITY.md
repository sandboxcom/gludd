# Release Dependency Floor Integrity

## Failure boundary

A broad text replacement during the beta release sequence changed unrelated
third-party minimum versions to Gludd's own prerelease number. The last known
good manifest was beta.1; beta.2 was the first contaminated manifest. This made
the project appear version-consistent while silently destroying dependency
compatibility and allowed a duplicated version floor in the
`sync-llama-cpp` target.

The release version bumper now owns exactly two fields:

- `[project].version` in `pyproject.toml`
- `general_ludd.__version__`

Dependency specifications, schema versions, documentation, and configuration
values are outside that mutation boundary.

## Manifest and lock contract

All PEP 621 runtime dependencies, optional extras, and uv dependency groups are
audited for accidental Gludd prerelease floors. Marker-split requirements remain
valid when their package name is shared but their Python markers differ. Package
identity comparisons use PEP 503 canonical names, and `uv.lock` is parsed with
the standard-library TOML parser.

The `sync-llama-cpp` target consumes the locked `local-inference` extra
instead of maintaining a second package constraint. Its
`SYNC_LLAMA_CPP_VALIDATE_ONLY=1` behavioral example resolves the lock without
installing or compiling the model runtime.

The repaired manifest also:

- keeps the Starlette `httpx2>=2.7.0` backend development-only in both dev
  groups;
- removes duplicate SciPy requirements;
- removes the GPL-only `ansible-dev-tools` metapackage while retaining the
  individually declared Ansible lint, Molecule, and pytest tools;
- preserves the two security-patched, Python-marker-specific Ansible Core
  ranges; and
- regenerates registry hashes and source provenance for every locked package.

## Practitioner evidence

Long-lived packaging reports show why release automation must fail closed:

- [Poetry issue #1388](https://github.com/python-poetry/poetry/issues/1388)
  reports a version command claiming success while layout-sensitive handling
  leaves the intended project version unchanged.
- [Poetry issue #9975](https://github.com/python-poetry/poetry/issues/9975)
  reports a dependency operation unexpectedly removing optional extras,
  demonstrating why every dependency group and the resulting environment must
  be checked after a manifest change.
- [Poetry issue #8194](https://github.com/python-poetry/poetry/issues/8194)
  records unrelated updates selecting prereleases under marker-dependent
  resolution, reinforcing the need to review the complete lock rather than only
  the named package.

These reports concern Poetry, while Gludd uses uv. The shared engineering
lesson is the inference adopted here: structured ownership and complete
post-mutation lock validation are safer than text replacement or partial
dependency checks.

## ZDD and operational behavior

This is a zero-downtime packaging change: runtime APIs, database schemas, and
deployed service configuration do not change. Existing workers continue using
their installed environment while a candidate artifact is built and verified.
Promotion occurs only after the manifest, lock, license, vulnerability,
coverage, and release gates pass. Rollback selects the previous immutable
artifact and lock; it never edits a live environment in place.

Validation is observable through tracked make targets. Tests fail on warnings,
dependency checks report exact locked package counts, the local-inference
behavioral example is dry-run capable, and namespaced resource status confirms
that no extra worker processes are left running.
