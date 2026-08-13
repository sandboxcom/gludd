# Beta.3 fail-closed release pipeline

## Release invariant

`v0.1.0-beta.3` may be published only after every release prerequisite has a
successful GitHub Actions conclusion. The `release` job is the single fan-in
and explicitly needs:

- the lint, type, collection, smoke, feature-evidence, secrets, and enforcement
  gate;
- every test-shard matrix leg;
- merged coverage with at least 85% aggregate coverage and at least 75% in
  every production Python file;
- every Molecule shard;
- Linux x86_64, Linux aarch64, macOS arm64, and Windows x86_64 builds;
- the GHCR container build/push and game-building E2E job.

The platform jobs remain parallel with the test and Molecule jobs. Parallel
execution reduces elapsed time without weakening the release fan-in.

## Failure semantics

- Required jobs do not use `continue-on-error`.
- An empty shard selection is an error, including pytest's no-tests-collected
  case.
- Every shard must produce a non-empty coverage database. The aggregation job
  requires all seven databases; missing or partial input is not a reportable
  success.
- Coverage reports explicitly enforce 85% aggregate and run
  `scripts/audit_coverage.py` at 75% per file.
- Platform binaries are smoke-tested before packaging/upload. Linux also
  starts the daemon and requires a successful `/health` response.
- Release artifacts upload only after their build and smoke steps succeed.
  Coverage and Molecule evidence still use `if: always()` for red-run
  diagnostics, with missing evidence itself treated as an error.
- Every artifact upload sets `if-no-files-found: error`.
- Pre-publication checks require the exact versioned filenames, non-zero file
  sizes, and valid SHA-256 sidecars. The release action also sets
  `fail_on_unmatched_files: true`.
- Reruns update the release associated with the existing tag. They do not
  silently delete a release through a swallowed CLI failure.

## Beta.4 restoration evidence

A clean development gate replay exposed workflow drift before beta.4: the shard,
Molecule, Linux, macOS, Termux, and container jobs could report a successful
workflow after failure; missing coverage and diagnostic artifacts were warnings;
and the release fan-in omitted coverage and several artifact producers. The
restored workflow keeps producers parallel, but the tagged release waits for all
of them. It also requires all seven coverage databases, validates aggregate and
per-file thresholds, rejects zero-size assets, checks producer SHA-256 sidecars,
and updates an existing tagged release without deleting published state.

The focused workflow regression set covers the current fan-in, job failure
semantics, artifact conditions, coverage dependencies, and stale structural
contracts. Beta.4 gate evidence is recorded in `TASKS.md`; remote publication
still requires the exact tagged commit to pass GitHub Actions.

## Long-lived user reports considered

These are not isolated theoretical cases:

- [GitHub Community discussion #26733](https://github.com/orgs/community/discussions/26733)
  began in 2020 and documents how skipped dependency/fan-in checks can appear
  successful to merge automation. The beta.3 publisher directly enumerates
  every release prerequisite instead of inferring success from a loose
  workflow conclusion.
- [GitHub Community discussion #26618](https://github.com/orgs/community/discussions/26618)
  began in 2020 and traces missing downstream artifacts to a consumer job that
  did not declare the producer in `needs`. Beta.3's release job names every
  artifact producer, while the coverage job names the shard producer.
- [GitHub Community discussion #50004](https://github.com/orgs/community/discussions/50004)
  began in 2023 and continued receiving reports after artifact-action version
  changes. It shows that apparently uploaded multi-runner artifacts can still
  be unavailable when download semantics are assumed rather than verified.
  Beta.3 downloads a named `gludd-*` set and then verifies exact files.
- [GitHub Community discussion #101831](https://github.com/orgs/community/discussions/101831)
  began in January 2024 and still had users asking for updates in May 2025
  about artifact-storage quota failures. Upload failures therefore remain
  blocking; the release cannot reinterpret an unavailable artifact as an
  optional platform.
- The [upload-artifact project documentation](https://github.com/actions/upload-artifact)
  states that its default for no matching files is warning-success. Every
  release-bound upload overrides that default with `if-no-files-found: error`.
- [upload-artifact issue #447](https://github.com/actions/upload-artifact/issues/447)
  documents a long-lived case where hidden coverage databases appeared to
  upload but downloaded as empty artifacts. Beta.4 counts all seven expected
  shard databases after download and rejects any absent or empty producer
  output before combining coverage.
- The [softprops/action-gh-release documentation](https://github.com/softprops/action-gh-release)
  documents that an existing tag release is updated and that unmatched globs
  can be made fatal. Beta.3 uses those idempotent update semantics and enables
  the fatal unmatched-file option instead of deleting releases with
  `|| true`.

## Local verification

The focused structural contract is
`tests/unit/test_beta3_fail_closed_pipeline.py`. Related workflow, packaging,
checksum, NSIS, smoke, coverage, Molecule, and release tests must also pass.
The repository's workflow-YAML hook validates both parsing and the job graph.
Remote publication still requires a green tag run and the repository release
completeness verifier.
