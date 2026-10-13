# Backlog-audit task sources

## Outcome

The backlog audit executes evidence for checked `TASKS.md` completion claims.
Passing unit tests and present implementation files alone produce `INCOMPLETE`,
not `VERIFIED_COMPLETE`. Completion also requires a trusted, item-scoped
deployment validator to return exactly `True`. A task's own status or inline
`deployment_ready` field is not deployment evidence. Missing acceptance, probe
errors, unsupported acceptance criteria, missing or skipped tests, collection
errors, and failed setup/call/teardown phases fail closed.

The validator is injected by the caller that owns deployment acceptance; the
CLI does not invent an acceptance result when no owner is connected. Acceptance
of one item is independent of another item's incomplete work. This item-level
verdict does not replace the canonical gate, exact-SHA CI, and release promotion
requirements for shipping the combined release.

The implementation repairs an API-drift gap in `scripts/backlog_audit.py`. The
script previously constructed `BacklogAuditor` without its required
`test_runner` argument and then caught the resulting `TypeError` as an
informational warning. It now loads real task records and prints a nonzero,
visible error if source or execution wiring fails.

## Data flow

1. `backlog_sources.load_task_ledger` confines `TASKS.md` to the selected
   repository, applies byte and record limits, and calls the canonical
   `scripts.validate_task_ledger.extract_tasks` parser.
2. Checked records are normalized to the existing `BacklogAuditor` task shape.
   Only pytest node IDs in the `evidence:` field are executable evidence.
   Backtick file references before the metadata fields become touched files.
3. `backlog_audit.run_evidence_tests` deduplicates all node IDs from all tasks
   and passes them to exactly one serial `pytest.main` call. A public pytest
   result plugin records collection failures and the setup, call, and teardown
   outcomes for every collected node.
4. `BacklogAuditor` receives cached results; it does not start another pytest
   session for each task. Its file reader resolves only regular files inside
    the selected repository and refuses symlink escapes. `audit_task_ledger`
    forwards the caller's item-scoped deployment validator; no validator means
    no item can be classified as verified complete.
5. The CLI preserves `BUG-CLASS SWEEP`, `GUARD COVERAGE`, `BACKLOG VERDICTS`,
   and the final `SUMMARY` sections. False or incomplete task claims now make
   the command nonzero.

## Bounds and failure policy

| Boundary | Limit or behavior |
|---|---|
| Ledger input | 8 MiB, regular UTF-8 file, stable identity during parse |
| Ledger records | 512 checked plus unchecked task records |
| Evidence | 64 deduplicated node IDs globally and per task |
| Touched files | 32 repository-relative references per task |
| Pytest scheduling | One in-process session, xdist disabled, inherited addopts cleared, explicit 180-second timeout |
| Output | One start marker, at most 64 result markers, one terminal marker |
| Missing test evidence | `FALSE_CLAIM`; never an implicit full-suite run |
| Collection/internal/usage/no-tests error | Every requested evidence ID fails |
| Skipped or incomplete phase sequence | Requested evidence ID fails |
| Missing deployment owner, rejected acceptance, non-boolean result, or probe error | `INCOMPLETE`; never unit-test-only completion |
| Empty or unsupported acceptance criterion | `INCOMPLETE`; never assumed satisfied |
| Unsafe path, duplicate ID, status mismatch, unsupported marker | Source error and nonzero CLI exit |

The project ledger's canonical parser accepts only `- [x]` and `- [ ]` task
markers. The adapter does not fork that parser. Instead, it detects other GFM
task-marker spellings and fails visibly so an uppercase marker or alternate
bullet cannot silently disappear from the audit.

## Zero-downtime rollback

`--no-backlog-verdicts` disables only the new task-source and pytest execution
path. The bug-class sweep, guard-coverage report, headings, and final summary
continue to run. No daemon, schema, listener, background process, or persisted
state is added, so rollback does not interrupt application traffic or require
cleanup.

## Standards and practitioner evidence

- The [GitHub Flavored Markdown task-list specification](https://github.github.com/gfm/#task-list-items-extension-)
  defines a task marker as a bracketed whitespace character or lowercase or
  uppercase `x`. Gludd deliberately retains its narrower canonical ledger
  grammar and rejects rather than silently omits the additional forms.
- [Gitea issue #10978](https://github.com/go-gitea/gitea/issues/10978), opened
  in 2020, documents a durable real-world split where uppercase `X` rendered as
  checked in an issue but was missing from list-view completion counts. That is
  the exact class of cross-surface disagreement this adapter refuses.
- The official [pytest invocation documentation](https://docs.pytest.org/en/stable/how-to/usage.html#calling-pytest-from-python-code)
  documents explicit arguments and plugin objects for `pytest.main`, and warns
  that repeat calls in one process can observe stale imported modules. The
  audit therefore builds one deduplicated session and one result plugin.
- [pytest issue #3143](https://github.com/pytest-dev/pytest/issues/3143), opened
  in 2018, records the practitioner symptom behind that warning: repeated
  `pytest.main` calls returned the same result after the test file changed.
  One session per audit avoids that stale-result ambiguity.
- The official [pytest plugin documentation](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)
  defines public `pytest_` hooks for collection, execution, and reporting. The
  result collector uses those hooks rather than private pytest internals.

## Verification

Focused unit tests cover canonical-source reuse, the API-drift regression,
resource limits, duplicate evidence, collection errors, all test phases,
missing tests, no-test tasks, repository and symlink confinement, visible
progress, CLI failure propagation, and the narrow rollback. Branch-aware
coverage also exercises missing, failed, non-boolean and successful deployment
acceptance, and independent verdicts for unrelated items. The completion-policy
profile is `config/coverage_backlog_deployment_completion.ini`. Adapter coverage
uses `config/coverage_backlog_audit_task_sources.ini`; the required
floor is 85% aggregate and 75% for every measured production file.
