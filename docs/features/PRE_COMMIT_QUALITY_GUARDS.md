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
