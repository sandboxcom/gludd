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

The trusted embedding runtime can now pass `deployment_validator` through
`scripts.backlog_audit.main`, `run_report`, and the verdict renderer to the
existing `audit_task_ledger` owner. Previously the CLI dropped that connection.
The callback is a Python capability, not a command, receipt, import path, or
success flag supplied by `TASKS.md` or CLI arguments. The standalone CLI still
has no deployment owner and therefore cannot verify completion from source
tests alone. This forwarding change does not implement the installed-artifact
probes or independently verified signer evidence that a native runtime owner
must obtain before accepting an item.

The controller-owned `scripts.backlog_native_acceptance` factory now supplies
that native adapter for canonical S39 and S42. It verifies the independently
signed installed subject and lock, runs fixed collection-native probes against
one exact canary generation, and rechecks all identities before acceptance.
Missing installed collections or trusted signer/invocation evidence remain
`INCOMPLETE`; the current EE lock is unchanged. See
[Native backlog deployment acceptance](BACKLOG_NATIVE_ACCEPTANCE.md) for the
closed probe contract, trusted capability boundary and remaining proof limits.

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

The supported `make backlog-audit` entrypoint delegates to the existing native
observer and auditor. Its default live policy keeps collection and task
verdicts enabled. Invalid boolean flags are rejected before either process
starts; the child's nonzero audit result is preserved as command failure.
The observer supplies namespaced status, live output, periodic heartbeats,
bounded execution and owned teardown rather than a second runner.

The documented non-mutating behavioral example is:

```sh
make backlog-audit BACKLOG_AUDIT_VALIDATE_ONLY=1 BACKLOG_AUDIT_COLLECT=1 BACKLOG_AUDIT_VERDICTS=1 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=2
```

Validation-only starts neither the observer nor the auditor and explicitly
reports `proof=none`, `release_ready=false` and `gate_evidence=false`. Set
`BACKLOG_AUDIT_VALIDATE_ONLY=0` for an actual audit, choosing the observer's
documented heartbeat, quiet-time, execution and retention bounds explicitly.
This standalone command does not configure a deployment owner or authorize
completion from task-supplied fields.

`--no-backlog-verdicts` disables only the new task-source and pytest execution
path. The bug-class sweep, guard-coverage report, headings, and final summary
continue to run. No daemon, schema, listener, background process, or persisted
application state is added, so rollback does not interrupt application traffic
or require application cleanup. The Make entrypoint retains diagnostics only
through the existing bounded observer. `BACKLOG_AUDIT_VERDICTS=0` maps to that
narrow rollback; `BACKLOG_AUDIT_COLLECT=0` maps to `--no-collect` and does not
disable task verdict execution.

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
- The [Pods recreation rules practitioner thread](https://discuss.kubernetes.io/t/pods-recreation-rules/27076)
  began in February 2024 and was revisited in September 2026. It reports a
  successful CI/CD deployment leaving the old application running with a
  reused image tag. This supports checking the actual deployed subject, not
  treating pipeline success as item acceptance; it is not a reproduction of
  that Kubernetes issue in Gludd.
- [Docker's image-digest documentation](https://docs.docker.com/dhi/explore/security-concepts/digests/)
  explains immutable image identity. [SLSA build provenance](https://slsa.dev/spec/v1.2/build-provenance)
  binds artifacts to their build and source. A deployment owner must apply
  those independent identity checks and execute the item's native runtime
  probe; CLI owner forwarding is not itself that proof.

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

`tests/unit/test_backlog_cli_deployment_owner.py` exercises all three CLI
entrypoints against the actual canonical parser and auditor. Its controls
retain one deduplicated evidence invocation, exact-boolean acceptance,
independent item outcomes, owner exceptions, missing source evidence, and
rejection of self-reported deployment fields without a trusted owner. The
existing coverage profile still measures all three original adapter owners.

`tests/unit/test_backlog_audit_target.py` executes the actual Make recipe and
native observer with only the auditor boundary replaced. It covers all section
flag combinations, exact argv and observer bounds, propagated failures,
invalid flags, no-execution validation and the registered public contract.


## Canonical identity and duplicate checked claims (2026-10-22 UTC)

S98.4 starts this repair at development
9b3e90a25ac1af18a0890b731f059712d992986a. The native eb6 completed-backlog
diagnostic retains failure 2 in 124.267 seconds, 81 unique heads/83 branches,
evidence_complete=false and nullable completion totals. Root traces the three
current duplicate identities to unsupported legacy primary suffixes: the existing
canonical parser reads S76.1a/S76.1b as S76, S77.3a/S77.3b as S77, and twelve
S78.W*/S78.FINAL labels as S78. These are lossy canonical identities, not
identical raw labels. Immutable historical ledgers remain unchanged.

The existing validator documented duplicate rejection but ignored the
checked-only branch. Four additive regressions first retain 4 FAIL/22 PASS in
0.71s/native 1.6s: current identities collide, and each of three duplicate checked
fixtures incorrectly returns 0. Adding the missing checked-count refusal makes
the real native validator report all three collisions with Make 2. The parser,
regex, task marker/status grammar, record limits and historical inventory
unknown/duplicate refusals remain unchanged.

Current metadata uses the existing uppercase hyphenated grammar while keeping
every old name as an explicit parenthesized legacy alias. No task, checkbox,
status, evidence, priority or release assignment is added or removed.

| Legacy names retained as aliases | Canonical primary names |
| --- | --- |
| S76.1a, S76.1b | S76-1A, S76-1B |
| S77.3a, S77.3b | S77-3A, S77-3B |
| S78.W10 through S78.W14 | S78-W10 through S78-W14 |
| S78.W15, S78.W15a | S78-W15, S78-W15A |
| S78.W16 through S78.W19 | S78-W16 through S78-W19 |
| S78.FINAL | S78-FINAL |

Sixteen primary names change; the native ledger still has 188 checked and
139 unchecked records. The latter are seven current release tasks plus 132
excluded backlog items. Native validate-task-ledger returns 0 after the data
repair. All 22 original test cases/assertions remain; thirteen additive cases
cover actual identity uniqueness, duplicate checked claims and the old native
status/stale/missing-ID/redispatch outcomes. The full owner passes 35 tests in
0.73s/native 1.7s. An existing typed pytest fixture replaces its old object
annotation and suppression comment, preserving the original assertion.

The runtime admission already selects the complete
tests/unit/test_task_ledger_validation.py owner in its first child, and canonical
admission owns validate-task-ledger. The new real-data guard therefore joins the
existing execution path without a new scanner, cache, daemon, phase, selector
or widened deadline. Keep all 153 failure families, original 2/1/1 children,
90 total/60 quiet, 21 phases/2850 seconds, fatal warnings, full gate and both
85% aggregate/75% per-file line and branch floors.

The unchanged config/coverage_proof_observations.ini measures all four original
production owners, including validate_task_ledger.py. Complete native consumer
and coverage receipts are obtained separately from source review, collection,
final gate and exact-SHA hosted/deployment/promotion proof. A diagnostic can
continue to report unknown historical ledgers after current metadata is valid;
new source or a repaired current record never certifies those immutable ledgers.

The [Python 3.11 re reference](https://docs.python.org/3.11/library/re.html)
distinguishes prefix matching and word boundaries from a complete token match.
The [April 2022 practitioner thread](https://discuss.python.org/t/need-help-with-my-code-regular-expression/14810)
shows a related diagnostic pitfall: captured results omit a matched sign outside
the capture group, and an unescaped dot broadens matching. This is supporting
practitioner context, not a reproduction of Gludd's identity grammar. The native
regressions and actual ledger output establish this defect. Reuse the existing
stdlib parser and validator; a second parser or dependency would split authority.

No managed service, listener, database or daemon state changes. Rollback restores
the exact source/metadata pair while preserving the failure receipts, aliases
and immutable refs; it changes no live serving route. Capacity at 97% used,
original publication60907, unknown natural scanner results and all required
release proofs remain pending. No task is ticked by this source repair.


Complete eleven-owner consumer acceptance passes 579 cases on each runtime:
Python 3.11 in 27.06s under the unchanged four-file coverage profile (native
28.893s), and Python 3.12 in 25.02s/native 26.4s. Preserve the native coverage
receipt .gate-logs/observed/coverage-files/20261022T053052Z-36922-21757449.json,
its full log/trace, and /tmp/gludd-v012-ledger-consumers312.{log,xml}.
The terminal coverage audit .gate-logs/coverage-20261022T053121Z.json measures
97.3% aggregate line/94.8% aggregate branch coverage. The changed validator is
97.2% line/95.2% branch; the other three original owners remain above 75% on
both measures. No profile, exclusion or threshold changes.

The original completed-inventory legacy-duplicate fixture and all its
partial/unknown assertions pass unchanged in both complete replays. Their
historical malformed inputs stay malformed; this repair corrects current
metadata rather than broadening the parser or erasing evidence. Source and test
strict scopes pass independently, and Ruff passes both changed Python files.

Reproduce the complete four-file coverage proof with the installed runtime:

~~~console
make coverage-files WORK_ITEM_ID=S98.4 COVERAGE_TESTFILES='tests/unit/test_proof_observations.py tests/unit/test_ci_await_exact.py tests/unit/test_pipeline_status_exact_sha.py tests/unit/test_pipeline_status_failure_branches.py tests/unit/test_pipeline_status_job_progress.py tests/unit/test_pipeline_status_inprocess_ledger.py tests/unit/test_ci_verdict_all_required.py tests/unit/test_ci_verdict_structural_repair.py tests/unit/test_task_ledger_validation.py tests/unit/test_branch_reconciliation_completed.py tests/unit/test_backlog_audit_task_sources.py' COVERAGE_CONFIG=config/coverage_proof_observations.ini COVERAGE_REPORT=.gate-logs/coverage-ledger-identity-311.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=180 OBSERVED_RETAIN_RUNS=20 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

These focused 180-second envelopes do not alter the original 90/60 runtime
admission or establish canonical gate, exact-SHA CI/deployment or release proof.
Only the native report after a source commit can inspect that new immutable
metadata. Re-running the current inventory against unchanged9b before then
would repeat its old malformed input; keep the existing failed observation.


Complete documentation/handoff/contract/branch/guardrail/split acceptance passes
204 cases in82.11s/native83.1s under its unchanged90/60 caller. Preserve
/tmp/gludd-v012-ledger-operating311.{log,xml} and native JSON under
.gate-logs/observed/ledger-operating. This full guardrail owner also invokes its
existing collection, lint and healthcheck controls; its quiet interval remains
inside60, with native heartbeats retained. All six Markdown documents, strict
line limits, eight-path registration and SESSION validation pass. The initial
literal blank-spacing replacement refuses before mutation because its expected
needle is absent; reading the actual range supplies the corrected exact edit.
Source/metadata/test review is complete inline; fresh normal collection and an
independent exact-source decision still require their actual receipts.
