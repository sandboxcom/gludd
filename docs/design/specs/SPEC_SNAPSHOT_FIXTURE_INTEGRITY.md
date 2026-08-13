# Snapshot Fixture Integrity Specification

Status: implemented  
Contract version: `snapshot-fixture/v1`  
Scope: `tests/unit/test_snapshot_deep.py` and `tests/snapshots/*.json`

## Problem and practitioner evidence

The deep snapshot suite previously treated a missing reference file as success:
an ordinary test run created `tests/snapshots/`, wrote the current value as the
expected value, then compared the value to itself. A clean checkout therefore
could not detect a missing fixture and became dirty merely by running tests.

This is a long-lived ecosystem problem rather than a Gludd-only preference.
The Pants practitioner issue
[`pantsbuild/pants#11622`](https://github.com/pantsbuild/pants/issues/11622),
opened in 2021, describes the conflict between snapshot writers and hermetic
temporary test execution and explicitly separates validation (the normal CI
default) from the exceptional write/update workflow. That operational lesson is
the basis of this contract: validation is always read-only; fixture creation is
an explicit reviewed action.

## Audited ownership and inventory

- The initial explicit update produced 31 JSON files. The stale
  `behavior_render_minimal` case failed before reaching its assertion because
  production security now requires at least one guardrail layer. Updating that
  input to the supported minimum exposed the 32nd and final fixture.
- `tests/snapshots/` now contains exactly the 32 IDs declared by the suite. An
  exact manifest check fails for both missing and unexpected JSON files; a
  minimum-count assertion is not sufficient.
- `SearchResult` imports `history.git_indexer`, whose `GitHistoryIndexer` owns
  `.gludd/git_history.db`, but the snapshot constructs only the dataclass and
  must not instantiate the indexer or create its database.
- Git coordination locks are owned by `git_automation.locking`; snapshot tests
  do not invoke that control plane. The worktree's ignored `.ansible/.lock` is
  tool-owned coordination state and is excluded from the checkout leak scan,
  as are cache/virtualenv directories and Git metadata.
- An autouse before/after guard attributes any newly created checkout-local
  `*.db`, SQLite WAL/SHM, `*.sqlite*`, or `*.lock` file to the test that leaked
  it. Temporary pytest data remains under the make target's namespaced
  `--basetemp` path.

## Required behavior

### Validation mode

With `GLUDD_UPDATE_SNAPSHOTS` absent or not equal to `1`:

1. The helper must not create a directory, fixture, received-value file,
   database, or lock in the checkout.
2. A missing fixture raises an assertion naming its expected path and the
   explicit regeneration command.
3. Actual values are normalized through JSON before comparison. This preserves
   the serialized contract for tuples, sets represented by producers as sorted
   lists, datetimes represented by `default=str`, and JSON mappings.
4. Invalid JSON, an output mismatch, a missing manifest entry, an unexpected
   fixture, or a temporary `*.tmp`/editor backup fails the suite.

### Explicit update mode

With `GLUDD_UPDATE_SNAPSHOTS=1`:

1. The helper may create only `tests/snapshots/` and its named JSON fixtures.
2. Each fixture is canonical JSON: sorted keys, two-space indentation, UTF-8,
   and one trailing newline.
3. Publication is atomic: write the same-directory `.json.tmp`, replace the
   destination, and remove the temporary file in a `finally` block.
4. The update run still compares the published file to the normalized actual
   value and must finish green. The operator reviews and commits all fixture
   changes together with the behavior change that required them.

## Acceptance criteria

- A regression redirects `SNAPSHOT_DIR` to an absent temporary path, calls the
  helper in validation mode, observes `Missing snapshot fixture`, and proves
  that even the directory was not created.
- A normal run from the tracked fixture set passes all deep snapshot tests and
  leaves the Git-visible file set unchanged.
- The manifest is exact at 32 fixtures and every fixture parses as JSON.
- A DB/lock artifact introduced during any snapshot test causes that test's
  teardown to fail with the leaked relative paths.
- Explicit regeneration works under two pytest-xdist workers without partial
  JSON or leftover temporary files.
- Focused tests maintain at least 85% aggregate coverage and at least 75% for
  every touched production source. This change touches no production source;
  the snapshot test helper itself is exercised in read, missing, update, and
  cleanup paths.

## Observability and security

Failures name the missing/mismatched fixture or list manifest and artifact
deltas. No received output is written implicitly, preventing secrets in a
renderer result from being persisted merely because CI or an editor ran tests.
Canonical JSON makes review diffs stable, while exact manifests prevent stale
or injected reference files from silently becoming trusted baselines.

The writer is intentionally gated only by the exact value `1`. CI additionally
asserts that this flag is not set. Fixture updates remain a code-review event;
they are never a recovery side effect of validation.

## ZDD rollout and rollback

This is a test-only, zero-downtime change: no daemon, database migration, API,
or runtime process is restarted. Roll out by landing the helper, complete
fixture set, regression, and this specification atomically. Development and CI
immediately switch from implicit creation to read-only validation.

Rollback is a single commit revert and requires no service action or data
restore. Do not roll back by deleting fixtures or re-enabling implicit writes.
If an emergency blocks fixture regeneration, skip only the deep snapshot test
at the job-selection layer with an explicit incident reference, then restore
`snapshot-fixture/v1`; production runtime remains unaffected throughout.

## Compatibility and versioning

`snapshot-fixture/v1` is compatible with the current Python 3.14 test matrix and
the existing `GLUDD_UPDATE_SNAPSHOTS=1` operator interface. Consumers compare
JSON values, not Python container identity, so the formerly accidental
tuple-versus-list mismatch is resolved at the serialization boundary.

A future encoding, directory layout, or normalization change is
`snapshot-fixture/v2`: update this document, regenerate every affected fixture
explicitly, and land a compatibility test that rejects a mixed-version set.
