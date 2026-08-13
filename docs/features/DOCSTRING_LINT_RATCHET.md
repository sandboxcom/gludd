# Incremental docstring lint ratchet

Status: implemented with the repository's locked Ruff executable and enforced
on staged production Python files. Release evidence is tracked in `TASKS.md`
as S83.52.

## Problem

The former deep test implemented a second docstring linter with Python AST
walking. Its 17 assertions all scanned the entire package and expanded into
115 undocumented modules, 1,013 undocumented public classes, and overlapping
function and method lists. Every assertion failed, but the output supplied no
bounded migration unit and duplicated rules already maintained by Ruff.

That design conflicted with the repository rule to use mature tools instead of
building custom linters. It also made a documentation-only legacy inventory a
release blocker while providing no way for a small source change to improve
the package safely.

## Behavioral contract

1. `make lint-docstrings DOCSTRING_FILES='...'` requires an explicit list.
2. Every input must be an existing tracked Python file below
   `src/general_ludd`; tests, scripts, options, untracked files, and host paths
   fail before Ruff starts.
3. The target delegates to locked Ruff `D` rules with the Google convention.
   There is no second parser, per-file suppression, inline bypass, or generated
   baseline allowlist.
4. `git-commit`, `commit-no-verify`, `repo-commit`, and `ship-commit` all run
   the same guard for staged production Python files.
5. A legacy module becomes fully compliant when it is next touched. Ruff works
   on the complete changed file, not selected changed lines, so nearby stale
   documentation is repaired in the same atomic commit.
6. Legacy omissions are not represented as passing documentation. They remain
   visible source debt, while the release suite verifies the executable
   ratchet instead of repeating thousands of overlapping failures.

## Zero-downtime, security, and resource boundary

This is a commit-time quality change only. It changes no application import,
API, database, daemon, port, or deployment state. Existing running services
continue unchanged. Rollback is limited to the Make target, Ruff convention,
and commit prerequisite.

The target rejects paths outside the tracked production package before
invoking Ruff, preventing option injection and accidental host-file scans.
One Ruff process handles only the explicit file list and prints its diagnostics
directly. No background worker, cache daemon, or persistent state is created.

## Practitioner evidence

A Ruff user asked for pre-commit linting on changed lines after already
restricting Ruff to changed files. The maintainer discussion establishes the
actual boundary: Ruff is file-scoped, not changed-line-scoped. Gludd therefore
uses the whole staged file as the smallest honest ratchet unit:

- [Ruff discussion #10977](https://github.com/astral-sh/ruff/discussions/10977)

A separate long-lived Ruff issue records that the docstring convention is a
Ruff-specific setting and is not inherited from another tool's configuration.
Gludd declares the Google convention explicitly in `pyproject.toml`:

- [Ruff issue #9043](https://github.com/astral-sh/ruff/issues/9043)

The maintained settings reference documents convention filtering and the
recommended workflow of selecting `D` rules with an explicit convention:

- [Ruff pydocstyle settings](https://docs.astral.sh/ruff/settings/#lint_pydocstyle)

### Recursive Make path normalization

Git emits staged paths one per line. Passing that raw multiline value through a
quoted recursive Make command can split the shell program itself. The commit
guard therefore converts only newline separators to spaces before invoking the
already path-validating `lint-docstrings` target. It does not evaluate,
unquote, or broaden any path.

This matches the long-lived
[GNU Make bug 51974](https://savannah.gnu.org/bugs/index.php?51974=),
whose reproducer ends in the same unmatched-quote and unexpected-EOF shell
failure after a multiline Make expansion. A
[Stack Overflow practitioner report](https://stackoverflow.com/questions/7281395/output-multiline-variable-to-a-file-with-gnu-make/7287289)
likewise traces unterminated quotes to expanding a multiline Make value inside
a shell command. Flattening the known newline-delimited file list at the
producer boundary avoids that parser ambiguity while retaining the target's
tracked-path and package-root checks.

## Verification

- `tests/unit/test_docstring_coverage_deep.py` behavior-tests success, missing
  scope, path rejection, target metadata, commit wiring, and removal of the
  bespoke AST scanner.
- `make lint-docstrings
  DOCSTRING_FILES=src/general_ludd/security/xmss.py` is the registered
  behavioral example.
- `make check-make-target-contract` and `make validate-makefile` validate the
  tracked target and its shared infrastructure.
- The full release gate remains authoritative for promotion.
