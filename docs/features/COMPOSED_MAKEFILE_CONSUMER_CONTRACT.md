# Composed Makefile consumer contract

Gludd keeps the root `Makefile` as an ordered entrypoint and stores the executable
target definitions in `make/*.mk`. Tests and audits that reason about target recipes
must therefore inspect the logical Makefile produced by
`scripts.makefile_layout.compose_makefile`, not only the root entrypoint.

## Failure model and canonical boundary

Reading the root file directly is syntactically valid but semantically incomplete:
target discovery, prerequisite validation, cleanup inspection, and observability
audits can silently miss definitions in fragments such as
`make/90-infrastructure-and-services.mk`. The canonical composer resolves the
explicit includes in declared order, rejects dynamic, optional, missing, duplicate,
or escaping includes, and preserves support for intentionally unsplit fixture files.

The S83.171 consumers keep four related boundaries explicit:

- Cleanup, Molecule CI, and deep target-inventory tests read the composed text.
- A dry run launched from a temporary working directory supplies the repository
  include root explicitly with `make -n -I`; its sentinel proves the operation does
  not delete the caller's environment.
- Event-loop offload assertions inspect
  `ExecutionDispatchMixin._dispatch_execute_job`, where the bounded filesystem work
  now lives, instead of pinning the compatibility facade.
- `config/coverage_gap_test_mappings.json` contains only live indirect edges. A
  directly importing behavioral test needs no mapping, while a retained mapping must
  prove both the facade-to-component and test-to-facade imports.

`tests/unit/test_makefile_layout.py` mechanically rejects new tests that read the
root `Makefile` without the composer. The focused cleanup, Molecule, deep-audit,
event-loop, mapping, observability, and split-consumer suites then protect behavior
rather than a monolithic file shape.

## Long-lived practitioner reports

The safeguards address recurring GNU Make surprises reported by users over many
years:

- A 2016 Stack Overflow question about
  [splitting a Makefile into modules](https://stackoverflow.com/questions/36422375/how-to-split-makefile-into-separate-modules/74922596)
  records that included content is processed at the include location. Gludd therefore
  composes fragments in declaration order rather than scanning and sorting the
  directory independently.
- A 2014 discussion of
  [where `include` searches for files](https://stackoverflow.com/questions/27533053/where-does-makes-include-find-files)
  highlights the current-directory lookup followed by explicit `-I` directories.
  This is why the temporary-directory dry run passes the repository include path
  instead of depending on the test process's working directory.
- A 2023 report that
  [`make --dry-run` can still execute recipes](https://stackoverflow.com/questions/76416758/make-dry-run-how-can-it-execute-things-that-are-supposed-not-to-be-executed)
  documents the recursive `$(MAKE)` exception. The cleanup contract forbids that
  escape hatch in the dry-run recipe and retains an on-disk sentinel assertion.

These posts are treated as design evidence, not executable truth; the repository's
tests pin the exact GNU Make behavior on the supported toolchain.

## Zero-downtime delivery and rollback

This contract is a zero-downtime deployment: it changes static tests, documentation,
and coverage ownership only. It starts no daemon, changes no service endpoint, and
requires no process restart. The checks land on a feature branch, run before merge,
and can coexist with either the pre-split or composed repository while the root
entrypoint remains valid.

Rollback is a normal commit revert after the focused suites pass. The composer guard
must not be disabled during rollback; if a fragment layout changes, consumers move to
the new canonical composition in the same change. An indirect coverage mapping may
be restored only when its source facade and behavioral test again provide the two
validated import edges.

## Resource bounds and verification

Composition reads the finite, explicit include list once per module-scoped fixture;
it does not recurse through arbitrary filesystem trees. Coverage ownership uses the
existing bounded static AST index, and the event-loop assertion preserves the
semaphore-bounded offload rather than executing jobs. Focused verification consists
of the S83.171 test files, scoped Ruff and strict mypy, repository collection, file
line limits, duplicate-code admission, and branch-aware coverage of at least 85%
aggregate with every measured source file at or above 75%.
