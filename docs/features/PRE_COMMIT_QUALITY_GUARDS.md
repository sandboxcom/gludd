# Pre-Commit Line and Duplicate-Code Guards

## Outcome

Oversized staged files and newly introduced production clones now fail before a
commit is created. The normal Git hook, the guarded Make commit targets, the
fast pre-commit target, and feature-branch integration admission all share the
same two checks. The line checker reads the staged index rather than a possibly
different working-tree file. Clone admission uses the exactly locked `jscpd`
5.4.0 executable; Gludd contains no custom clone algorithm.

The checks are control-plane only and preserve zero-downtime delivery. They do
not start an application process, change a database, publish an artifact, or
alter the staged index. Temporary source snapshots and the ephemeral clone
baseline live in a unique system temporary directory and are removed on every
return path.

## Staged-content boundary

`make check-file-line-limits FILE_LINE_LIMIT_POLICY=config/file_line_limits.json
FILE_LINE_LIMIT_STAGED=1` discovers added, copied, modified, renamed, or
type-changed staged paths and reads each stage-zero blob by object ID. An
unstaged small edit cannot conceal an oversized staged file, and an unstaged
large edit cannot make a compliant staged blob fail. The complete working-tree
inventory remains the default for lint and gate use, including exact non-text
policy drift checks.

The shared `scripts/staged_snapshot.py` boundary also materializes selected
production roots for clone admission. Both checks therefore use one path
validation, Git error, and snapshot implementation instead of duplicating
staged-content behavior.

## Mature duplicate detection

`config/duplicate_code.json` bounds `jscpd` to application code, operational
scripts, OpenCode plugins, and the maintained Ansible collections. A scan uses
at most two workers (passed as a supported CLI flag), ignores test, prose,
fixture, and vendor trees, rejects files above the explicit 500 KiB analysis
ceiling, and requires at least 12 lines and 80 tokens per clone. Weak mode
removes comments from comparison. Identifier and literal normalization catches
renamed structural copies as well as exact copy/paste.

For a pre-commit run, the checker materializes `HEAD` and the exact staged
index. For integration admission, it materializes the explicit development
base and feature `HEAD`. The mature engine first writes an ephemeral fingerprint
baseline from the base snapshot, then scans the candidate with
`--fail-on-new-clones`. Its JSON reporter is reduced to a bounded console
summary that keeps the existing, new, and total clone counts visible and emits
the exact locations for new clones. This avoids dumping thousands of accepted
historical pairs into every commit while preserving their measured count. Only
a fingerprint absent from the base blocks the change,
and existing findings remain visible as a measured count rather than being
suppressed. There is no committed
suppression baseline to grow silently, and every engine, malformed report,
snapshot, policy, or baseline failure propagates nonzero.

The scope intentionally excludes tests and prose from clone admission. Those
artifacts often repeat setup or contract language by design. Production roots
are scanned together, so copying an unchanged helper into a changed file is
still detected; scanning changed files alone would miss that relationship.

## Practitioner evidence

- The long-lived `jscpd` [import-block report][jscpd-imports] shows that short
  import groups can be mistaken for meaningful clones. The policy uses both a
  line and token floor, scans production roots only, and does not ask developers
  to scatter suppression comments through source files.
- The 2020 [pre-commit exit propagation report][jscpd-exit] documents a detector
  failure that did not stop a commit in an older integration. Gludd invokes the
  locked executable directly and propagates every nonzero result.
- The diff-aware [legacy-codebase report][jscpd-diff] explains why scanning only
  changed files misses a clone between new code and unchanged code, while a
  global percentage can hide a new clone in a large repository. Gludd scans the
  complete bounded production snapshots and compares fingerprints against the
  explicit base.
- Pylint's long-running [local R0801 suppression report][pylint-r0801] records
  the operational difficulty of suppressing one accepted match. That history,
  the repository's existing `jscpd` Ansible role, and the polyglot source set
  favor one language-aware `jscpd` policy instead of adding Pylint solely for
  Python similarity checks.
- A Sonar community [whole-file rule report][sonar-whole-file] describes size
  rules disappearing when pull-request analysis focuses only on changed lines.
  Gludd retains the complete tracked-inventory gate and adds an earlier exact
  staged-blob check; one does not replace the other.

[jscpd-imports]: https://github.com/kucherenko/jscpd/issues/341
[jscpd-exit]: https://github.com/kucherenko/jscpd/issues/363
[jscpd-diff]: https://github.com/kucherenko/jscpd/issues/879
[pylint-r0801]: https://github.com/pylint-dev/pylint/issues/214
[sonar-whole-file]: https://community.sonarsource.com/t/some-rules-do-not-work-in-mrs-prs-decoration-because-of-newcode-focus/152734

## Operations and rollback

Install the exact detector from the reviewed lock with the documented
`node-deps-sync` target. The hook fails closed with that instruction when the
binary is absent; it never downloads an unreviewed tool during a commit. Use the
validate-only Make behavior to check policy and target plumbing without reading
Git state or running the detector.

The rollback is one atomic revert of the hook entries, Make wiring, scripts,
policy, package manifest, and package lock. No data migration or service
rollback is required. Reverting only the package pin while leaving the hook is
invalid because the missing executable is deliberately a hard failure.

## Frozen fixture formatting and early admission (2026-10-13)

The push formatter added a final LF to the checksum-frozen upstream limiter
fixture, invalidating its existing native source-integrity assertion. Its
verified 1176-byte upstream representation intentionally has no final LF.
Restoring those exact bytes makes the original SHA assertion pass again.
Five ordinary source files legitimately required EOF normalization and retain
that normalization and all formatter enforcement.

Both mutating text hooks protect only that exact fixture path, anchored at
both ends. Existing catalog and vendored-byte boundaries remain unchanged.
Adjacent fixtures, application/scripts/tests, prose/config and path lookalikes
remain selected. Security, schema and native checksum checks still run.

The existing scoped content guard now runs maintained trailing-whitespace and
EOF hooks before secrets, line/clone checks and collection. A rewrite returns
nonzero before commit; the guard does not silently restage or hide failures.
Five new portable regressions fail first, then the integrated hook/fixture
cohort passes 46 cases in each installed Python environment. A separate focused
replay uses the exact already-installed upstream hook to preserve frozen bytes
while actually repairing an ordinary file; it is not a cache-dependent unit
test or a release proof.

The [maintained selection contract](https://pre-commit.com/#regular-expressions)
owns per-hook regex filtering, and [individual hook execution](https://pre-commit.com/#pre-commit-run)
provides the existing cheap admission mechanism. The [pinned native EOF source](https://github.com/pre-commit/pre-commit-hooks/blob/v5.0.0/pre_commit_hooks/end_of_file_fixer.py)
owns the byte transformation. A [2020 fixture-selection report](https://github.com/pre-commit/pre-commit-hooks/issues/517)
recommends per-hook boundaries so unrelated checks continue; a [2023 golden-data report](https://github.com/pre-commit/pre-commit/issues/2812)
shows that excluding the text type can unintentionally exempt every text file.
The exact-path and ordinary-file negative controls prevent that broader defect.

These control-plane changes do not restart serving services or change data,
controller images or promotion authority. Rollback is the isolated config,
guard/tests/documentation commit. Full gate, exact-SHA hosted and deployment
acceptance, with unchanged 85/75 coverage requirements, remain pending.

## One native collection before a source commit

The commit workflow now owns the fresh count and collection admission together.
`ship-commit` calls `test-count` after recording the staged tree and before
revalidating that tree. `ship-commit-files` retains its explicit staging scope,
commit lock, and delegation to `ship-commit`. The caller can use that one
sanctioned workflow without first running another manual `test-count`.

Both public entry points use one private Make recipe, `_collection-admission`.
It retains the complete native `pytest tests/ --co -q` inventory, repository
collection lease, xdist trace plugin, observer heartbeat/quiet/duration/retention
policy, and per-invocation Ansible temporary directory beneath OBSERVED_ROOT.
`test-count` retains its `--no-sync` boundaries and three-line success summary;
`collect-check` retains its own observation label and `Collection OK` marker.
Both refuse every nonzero collection result and emit the configured bounded
failure tail. A failed diagnostic tail cannot replace the original collection
exit status; a failed success-summary tail still refuses the commit. Temporary
Ansible state is removed on every ordinary exit, interrupt, or termination.

Every content, secret, clone, lint, docstring, scope, and index-change guard
remains in place. No previous observation or receipt is read as admission, and
there is no caller-provided success boolean, narrower selection, cache, second
scanner, or additional daemon. Each commit invocation starts its own fresh
collection. Full gate, exact-SHA hosted CI, coverage floors, and promotion
requirements remain authoritative and unchanged.

Upstream practitioner evidence supports eliminating repeated collection while
preserving its boundary. In the 2021 pytest user discussion
[Speeding up collection phase](https://github.com/pytest-dev/pytest/discussions/9260),
the reporter measured expensive startup even for a single test file; a
maintainer recommended collection profiling. In the 2019 report
[Tests collection is slow on high number of tests](https://github.com/pytest-dev/pytest/issues/5516),
the reporter described substantial collection cost at scale. These reports
support avoiding duplicate work, but do not measure Gludd or justify skipping
required collection. The 2019 concurrency report
[pytest temporary-directory lock race](https://github.com/pytest-dev/pytest/issues/5456)
also reinforces retaining isolation and the existing canonical lease.

The regression invokes native Make source recipes with harmless Git and UV
capture boundaries. It checks one fresh full-inventory collection, observation
policy, cleanup, failure refusal, content/secret/clone prerequisites, exact
staged scope, before/after staged-tree ordering, and detection of four owner or
guard mutations. These focused regressions establish command routing and
atomicity at the recipe boundary; they do not execute the real full test
inventory, prove coverage, replace canonical acceptance, or establish hosted
throughput improvement. The observed earlier ~56-second, 123537-case collection
is context supplied by the integrator, not a new measurement from this patch.

This change is zero-downtime: it changes command ownership only and adds no
service, listener, migration, or process pool. Existing service operation and
in-flight collectors remain untouched. Rollback reverts the two Make fragments,
matching contract/tests, and this feature documentation together; it restores
the prior two-command operator workflow without a state migration.

Root's canonical RED reproduces eight failures with 21 passing controls.
Mechanical dependent-consumer selection finds three additional source-only
contract failures. Their assertions now inspect native Make expansion through
the existing collection test helper, retaining the original observer-variable,
exit-code, collection-only and no-global-gate-lock checks. Harmless type
annotations preserve all existing lock-plugin assertions and capture fields.
All 192 focused and dependent cases pass warning-fatal on Python 3.14 and
3.11. Strict typing, Ruff, all 227 target contracts, native Make parity/help,
task integrity and the unchanged 253-resource inventory pass. Actual full
collection and successor hosted acceptance remain separately required.

The exact documented test-count example then executes the real complete
inventory: 123565/123566 cases collected (one existing deselection), zero
collection errors, original canonical lease released, and Collection OK in
56.7 observed seconds. This is a fresh native example, not the capture fixture's
printed count; the commit workflow must independently collect once before
committing its exact index.
