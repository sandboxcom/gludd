# Structural Python audit contracts

Gludd's structural tests protect importability, test ownership, and bounded module size without freezing repository
snapshots that become false as features are added.

## Package discovery

Every directory containing Python modules must be discoverable as either a regular package or a PEP 420 namespace
package. An `__init__.py` file is therefore required only when regular-package behavior is semantically necessary. Empty
`__init__.py` marker files are valid; non-empty package APIs must parse, import, document themselves, and keep their
declared exports consistent.

This distinction addresses a long-running source of user confusion documented in the Python community discussion
["__init__.py, PEP 420, and iter_modules confusion"](https://discuss.python.org/t/init-py-pep-420-and-iter-modules-confusion/9642).
The repository audit asks Python's import machinery whether an unmarked directory is a namespace package instead of
maintaining a hand-written directory allowlist.

## Test ownership and size growth

Large source modules are associated with tests through explicit static imports, including literal
`importlib.import_module(...)` calls. Test filenames are not required to mirror implementation filenames, so module
splits and feature-oriented test names do not create false missing-test failures. The ten largest modules must all have
an importing test.

The hard per-file ceiling is 2,500 lines. Aggregate growth is bounded by average source-file size rather than an
absolute repository-wide line snapshot, allowing the project to add cohesive modules while continuing to detect
monolithic growth.

## Pytest fixture lifecycle

Class containers in these audits use function-scoped fixtures because each fixture feeds one test. This avoids the
class-instance lifecycle ambiguity reported by users in
[pytest issue #10819](https://github.com/pytest-dev/pytest/issues/10819), which pytest 9.1 deprecated ahead of removal in
pytest 10.
