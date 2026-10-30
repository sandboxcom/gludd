# E2E Coverage Audit Contract

This document defines the evidence required before Gludd can claim that its
end-to-end (E2E) suite covers Python control-flow branches.  A test count or a
regular-expression scan of source files is not a coverage measurement.

## Thresholds and metric

`make audit-coverage` is the canonical audit target.  It instruments the E2E
run with `coverage.py` branch tracking, executes the configured shards, combines
their data, and emits a machine-readable aggregate report.

The report is acceptable only when all of these conditions hold:

* aggregate line coverage and E2E branch coverage are each at least **85%**;
* every measured source file reaches **75%** for both line and branch coverage
  (the project target is 85% wherever practical); and
* every shard completes successfully, with no collection, timeout, or worker
  error omitted from the report.

Branch coverage counts both outcomes of a conditional edge.  It is distinct
from statement/line coverage: executing the line containing an `if` does not
prove that both its true and false paths ran.  Tests must assert the behavior
of each path, rather than merely execute code for metric padding.

### Unrounded admission and display precision

Numeric admission compares the unrounded count-derived ratios with each
configured aggregate and per-file floor. Report percentages retain one decimal
place: 719/846 branches is 84.988179669%, so its displayed 85.0 still fails an
85% aggregate floor. Exact 85/100 and exact per-file 75% remain passing
boundaries. Custom numeric floors and weighted counts retain their existing
meaning; no tolerance or epsilon lowers a floor.

The existing JSON/Cobertura readers use the same decision and source filtering.
Their imported measurements retain generic numeric authority; only completed
owned native execution can establish the E2E provenance described below.
Existing numeric/display fields are unchanged. The additive `threshold_failures`
list carries each actual raw failed metric, threshold and scope (plus file for
per-file failures), defaulting to an empty list when no failure is recorded.
The CLI uses those authoritative causes rather than deriving a reason again
from a rounded display. Legacy reports without this optional detail retain the
existing diagnostic fallback; statuses and admission are not delegated to it.

The bounded 2026-10-24 regression uses owned JSON/XML inputs and actual imported
CLI calls. Before the source repair, 12 FAIL / 16 PASS in 1.38s (native 2.3s)
exposes false success for aggregate branch, aggregate line and per-file line
values just below their floors. All exact/custom-floor positives already pass.
Native receipt: `.gate-logs/observed/coverage-floor-red/coverage-floor-red311-ccb8f79.json`.
After repair the same 28 cases pass in 1.13s (native 2.0s) at
`.gate-logs/observed/coverage-floor-green/coverage-floor-green311-ccb8f79.json`.
Input bytes and generic non-E2E attribution remain. No earlier rounded
`passed=true` observation is rewritten or relabeled as unrounded-floor proof.
Complete source/consumer
profiles, independent review, normal collection and final release proofs remain
separate and pending.

### Complete-consumer fixture conformance

The first complete 12-owner Python 3.11 replay retains 1 FAIL / 323 PASS in
21.98s (native 23.38s) at
`.gate-logs/observed/coverage-files/20261024T062202Z-82025-b89ed7f4.json`.
The unchanged `test_branch_totals_counted_correctly` expects imported E2E
aliases. Immutable source ccb8f792c0a252dccef35302f37ac47e615917ec already omits
those aliases; the raw-floor repair does not remove them. The failed native
owner stops before coverage combination and numeric admission. Python 3.12 is
not launched from that failed result.

Only the five stale imported-alias assertions conform to the established
contract: retain generic 10 total / 8 covered / 2 missing / 80% and explicitly
assert both E2E aliases are absent. All other assertions, fixtures and native
owned-E2E controls remain. Mechanical annotations and typed fake subprocess
results resolve the same owner's 34 existing strict errors without suppressions.
A whole-file replacement initially refuses a non-UTF-8 transport encoding;
the corrected UTF-8 transport preserves the source before verification.

The failed function plus all 28 raw-floor cases then pass: 29 PASS in 1.14s
(native 2.0s), receipt
`.gate-logs/observed/coverage-floor-conformance/coverage-floor-conformance311-ccb8f79.json`.
The same 12-owner, two-source profile still requires complete receiving on both
installed runtimes, with its original 180 total / 60 quiet seconds and 85%
aggregate / 75% each-file line AND branch floors. No receipt supplies full-gate,
exact-SHA hosted, deployment or task-completion authority.

### Complete strict-floor source receiving

The corrected complete replay retains the ten-owner manifest below and adds
whole `tests/e2e/test_branch_coverage_thresholds_e2e.py` and
`tests/e2e/test_branch_coverage_e2e.py`: 12 whole owners, 324 cases per runtime.
These receipts belong to the frozen five-path delta on
ccb8f792c0a252dccef35302f37ac47e615917ec in registered worktree
`/tmp/gludd-worktrees/agent-v012-coverage-floor`. They supersede only the earlier
pending source-profile state; every failed receipt remains retained.

| Runtime | Cases | Pytest / native elapsed | Native coverage-files receipt |
| --- | --- | --- | --- |
| Installed 3.11.14 | 324 PASS | 23.08s / 24.533s | `.gate-logs/observed/coverage-files/20261024T063136Z-2973-fbb3b12b.json` |
| Installed 3.12.15 | 324 PASS | 38.39s / 40.662s | `.gate-logs/observed/coverage-files/20261024T063347Z-10356-8047e8ee.json` |

Both runs use unchanged `config/coverage_ci_tooling.ini`, measuring all of
`audit_coverage.py` AND `coverage_missing_lines.py`, with 180 total / 60 quiet
seconds, serial pytest, fatal warnings, literal `ID=` and native trace. Each
trace records 651 events / 324 pairs with zero issues, unfinished tests or
failure reports. These spans establish neither performance improvement nor
live E2E execution.

The actual repaired auditor passes both native reports:
`.gate-logs/coverage-20261024T063201Z.json` and
`.gate-logs/coverage-20261024T063428Z.json`. Independently read complete raw JSON
has identical counts on both runtimes: aggregate lines 466/494 (94.33198%) and
branches 147/170 (86.47059%); auditor lines 349/373 and branches 117/136;
companion lines 117/121 and branches 30/34. Thus both raw aggregate floors
clear 85%, and each file clears both raw 75% floors. The legacy display remains
94.3% line / 86.5% branch aggregate, with per-file displays 93.6/86.0 and
96.7/88.2. Both reports retain `threshold_failures=[]`, `shards=[]` and no E2E
aliases. Raw inputs are `.gate-logs/coverage-v012-raw-floor311.json` and its
`312.json` sibling.

Only this receipt prose follows those source measurements. Independent review,
normal per-commit collection, Main receiving, full canonical gate and exact-SHA
hosted/deployment/promotion proofs remain separate; no task criterion closes.

## Required evidence

An audit result is valid only when its JSON contains the following evidence:

### Branchless percentage units and raw admission (2026-10-24 UTC)

Receiving review found a separate gap in the original raw-floor packet.
When all branch counts are zero, each file uses its raw line percentage for
the existing branch fallback. The aggregate formerly multiplied the mean of
rounded percentages by100 again: two85.0 displays became8500.0. It could admit
a raw fallback below85 even when weighted aggregate lines passed.

The new case measures85010/100000 and8496/10000 covered lines, with zero
branches. Both per-file displays remain85.0 and clear75; weighted aggregate
line coverage is85.005454545percent. The unweighted raw fallback is84.985 and
must fail85 while displaying85.0 in percentage units. The original259-line
source-scope test prefix and every earlier assertion are preserved.

A genuine native case on the separate uncommitted correction based on
0344d12fae95180f43bf07eedd130428da6c30fc failed1case in0.73s/native1.657s,
then passed the SAME case in0.64s/native1.731s. Preserve the peer receipts
.gate-logs/observed/branchless-floor-red/branchless-floor-red311-0344d12.json
and .gate-logs/observed/branchless-floor-green/branchless-floor-green311-0344d12.json.
Both retain matching native trace protocol pairs. These are bounded311 repair
receipts, not the complete profile or current Main acceptance.

The four-site repair retains raw values in the existing per_file_branch
dictionary, computes its mean without the extra100, and rounds only at both
existing report-formatting sites. Branchful count-weighted arithmetic, empty
input100, schema, source confinement and generic-data/E2E proof limits remain.
Root receives this correction alongside the original floor/resource/EE packet
on Main49f2c5954c27b84355827bc2445223aad55a3a58. Original source receipts are
historical; independent current-source review, complete unchanged profiles and
consumers on both runtimes, normal collection and all final proofs remain
required. No coverage threshold or old assertion is weakened.

* `e2e_branch_totals` and `e2e_branch_coverage` (covered/total and percentage);
* line coverage and the per-file threshold verdicts;
* one result for every discovered E2E shard, including pass/fail status and
  the tests assigned to that shard; and
* failure details for every failed shard, even when a shard exits before it can
  write a normal coverage JSON file.

The aggregate JSON and its source commit must be retained with the gate logs.
If a shard fails, the aggregate percentage is **not** a release result: fix or
quarantine the failure with an explicit task, rerun the complete audit, and
replace the stale report.

### Imported reports and execution ownership

The shared JSON/Cobertura readers evaluate generic numeric coverage. Explicit
`--json-file` or `--xml-file` input does not execute the E2E suite and omits both
`e2e_branch_coverage` and `e2e_branch_totals`. Numeric admission still enforces
the same aggregate/per-file line AND branch floors. The coverage-files owner
and hosted import consumers retain their existing argv, statuses and metrics.

Only the existing native audit runner can attach those E2E fields after
discovering a nonempty E2E inventory, recording every successful shard and
successfully generating coverage JSON. A completed native suite below a
numeric floor still fails. Imported field names, E2E-looking paths, contexts,
old reports and progress sidecars cannot establish execution or completion.

A failed native attempt is recorded before any stale raw coverage is parsed.
Its report stays unmeasured with `passed: false`, the actual native exit code,
shard/failure details and the existing error. Malformed stale JSON cannot hide
that failure. Partial progress and owned coverage-database cleanup remain
authoritative for their existing lifecycle roles.

### Durable in-flight progress

While shards are running, the runner writes an atomic sidecar beside its raw
coverage JSON input: `<coverage-json>.progress.json`. In default CLI mode this
is `.gate-logs/coverage-data-<UTC>.json.progress.json`, independently of the
aggregate `--json-out` destination. The sidecar is the
auditable source for partial-run state and is intentionally not an aggregate
coverage result.  It contains:

* `schema_version`, `run_id`, `pid`, `started_at`, and `updated_at` so an
  operator can correlate the file with one process and one invocation;
* `current_index` and `total`, plus a stable relative `path` for every E2E
  file; each shard summary includes the resolved environment namespace used
  for resource isolation; and
* `counts.attempted`, `counts.passed`, `counts.failed`, and `counts.skipped`,
  with per-file states (`pending`, `running`, `passed`, `failed`,
  `timed_out`, or `skipped`). Completed or failed shards also include ordered
  per-test diagnostics from JUnit XML (`nodeid`, status, and bounded failure
  message/text), so a failing file can be triaged without rerunning it merely
  to discover which test ran first.

The sidecar has `complete: false` for every interrupted, timed-out, or failed
run.  Remaining files are marked `skipped` with a reason when a failure stops
the serial audit.  Only after the final `coverage json` command succeeds is
`status` set to `completed` and `complete` set to `true`; a sidecar must never
be treated as proof of a branch percentage.  Updates use a temporary file and
an atomic replace, so readers never observe truncated JSON.  This makes a
stale or interrupted audit distinguishable from a certified aggregate even if
the process is terminated before the normal failure report is written.

## Subprocess and shard handling

E2E tests launch subprocesses and may run in parallel.  Coverage data therefore
must use unique per-process files and be combined only after all workers exit.
Never infer coverage from a partial `.coverage` file or from a run that was
interrupted.  The audit runner records shard progress and keeps failed-shard
reports so a connector or lifecycle regression cannot silently disappear.

This requirement follows long-lived coverage.py guidance: subprocess
measurement needs parallel data files and an explicit combine step ([coverage.py
subprocess documentation](https://coverage.readthedocs.io/en/7.14.1/subprocess.html)).
The same failure mode is regularly reported by practitioners: a user forum
discussion warns that a high percentage can still be meaningless when tests do
not exercise both outcomes of a branch ([Reddit discussion on meaningful
coverage](https://www.reddit.com/r/ExperiencedDevs/comments/1cgucr6/is_anyone_having_a_hell_of_a_time_with_codecov/)).

## Suite provenance repair (2026-10-22 UTC)

S98.4 source base 815cd5dff5bf337eda518f9d1ff04673145a043c retains an actual
counterexample: .gate-logs/coverage-20261022T001007Z.json measures only
detect_secrets_readonly.py with 99.0% line / 96.4% branch and no shards, yet the
shared evaluator labeled it tests/e2e. Retain those numeric measurements as
focused source evidence; their E2E attribution was unsupported.

The repair removes only the shared evaluator's unconditional E2E aliases.
Main attaches the unchanged native totals after its own successful nonempty
shard run. Native nonzero status now emits the existing unmeasured failure
schema before inspecting stale JSON, preserving the error/status/shards and
owned teardown. No new dependency, parser, classifier, observer, cache, CLI
flag or Make target is introduced. JSON/XML readers, thresholds, native
budgets, worker count, progress owner and cleanup remain.

### Failing-first controls and consumer conformance

The new complete 14-case test owner uses the real parsers, main, discovery,
runner, progress writer and cleanup. Only subprocess.run is a controlled child
seam. It covers JSON context labels, Cobertura and explicit CLI imports,
completed native 100% and below-floor 75%, and native pytest 19/coverage-json 17/
timeout 124 against both old percentages and malformed stale JSON. Native
positive controls retain the complete prior 75% E2E totals dictionary. These
are confined reporting controls, not a live E2E or deployment run.

Preserve the native sequence under /tmp/gludd-v012-coverage-suite-*:

* red312: the first escaped-text fixture write caused zero collection/one
  syntax ERROR. This is a transport failure, not a semantic RED. Literal
  write-text-b64/replace-text-b64 preserves source bytes; legacy write-text
  interprets escapes.
* semantic-red312: 14 FAIL / .96s included two incorrect sidecar-path assumptions.
  Correct only the fixture to the real raw-input sidecar.
* native-red312: 12 FAIL / 2 PASS / .87s / native 1.7s is the genuine pre-repair control.
  Six imports falsely claimed E2E; six failed native attempts certified stale
  percentages or raised decoding errors. Both native positive controls pass.
* green312: 14 PASS / .75s / native 1.7s after the producer repair.
* legacy312: 5 FAIL / 118 PASS / 3.80s / native 4.7s identifies one old unit and four old
  reporting assertions that relied on unsupported imported E2E aliases.

Only those five assertions conform to the corrected contract. Keep their
generic75/70/weighted numeric checks, arcs, contexts, thresholds and outcomes;
add absent-alias assertions and explicit generic totals. Two reporting case
names now describe imported evidence accurately. No test is deleted, skipped,
xfail-marked or weakened. Native positive controls preserve the original E2E
dictionary. Canonical annotation work also retains the initial 34 + 16 strict
errors and separately verifies the two existing complete test owners.

Ruff retains the corrected canonical scripts/pytest import grouping. The
separate script docstring check exposes D202; remove only its blank line.
An initial literal replacement refuses a non-UTF-8 transport encoding; repair
that edit before verification. Failed fixture/transport checks stay history,
not production failure evidence or a waiver. The new routing test also exposes
two strict object-membership errors; narrow each decoded native row before
retaining both exclusion assertions. Separate strict scopes pass for the
producer and all five changed test owners. The final 14 source controls plus
the route pass 15/.82s/native 1.7s on Python 3.11 after these corrections.

### Complete source acceptance and reproducible owners

Both installed runtimes pass all 251 cases across the same ten complete
source/consumer files: Python 3.11 in 20.40s/native 22.5s and Python 3.12 in 21.24s/
native 23.6s. Keep the unchanged complete config/coverage_ci_tooling.ini
profile, including audit_coverage.py AND coverage_missing_lines.py, branch
tracking and subprocess patching. The native audits retain 94.7% line / 86.7%
branch aggregate; each file independently exceeds 75% for both metrics:
audit_coverage 94.1/86.3 and coverage_missing_lines 96.7/88.2.

Reports .gate-logs/coverage-20261022T003346Z.json and
.gate-logs/coverage-20261022T003536Z.json now contain generic metrics and
shards=[] without either E2E alias. These source receipts predate only whitespace/comment corrections; they do not measure global or live E2E
coverage. A combined coverage.py display percentage is not the independent
line/branch floor verdict.

Use these existing native commands with every variable explicit:

~~~console
make coverage-files COVERAGE_TESTFILES="tests/unit/test_audit_coverage.py tests/unit/test_audit_coverage_progress.py tests/unit/test_audit_coverage_batch5_fail_closed.py tests/unit/test_audit_coverage_source_scope.py tests/unit/test_branch_coverage_contract.py tests/e2e/test_branch_coverage_reporting_e2e.py tests/unit/test_audit_coverage_suite_provenance.py tests/unit/test_coverage_missing_lines.py tests/unit/test_ci_hosted_failure_tooling.py tests/unit/test_ci_named_shard_files.py" COVERAGE_CONFIG=config/coverage_ci_tooling.ini COVERAGE_REPORT=.gate-logs/coverage-v012-suite-provenance311.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=5 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=180 OBSERVED_RETAIN_RUNS=20 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
make coverage-files COVERAGE_TESTFILES="tests/unit/test_audit_coverage.py tests/unit/test_audit_coverage_progress.py tests/unit/test_audit_coverage_batch5_fail_closed.py tests/unit/test_audit_coverage_source_scope.py tests/unit/test_branch_coverage_contract.py tests/e2e/test_branch_coverage_reporting_e2e.py tests/unit/test_audit_coverage_suite_provenance.py tests/unit/test_coverage_missing_lines.py tests/unit/test_ci_hosted_failure_tooling.py tests/unit/test_ci_named_shard_files.py" COVERAGE_CONFIG=config/coverage_ci_tooling.ini COVERAGE_REPORT=.gate-logs/coverage-v012-suite-provenance312.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=5 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=180 OBSERVED_RETAIN_RUNS=20 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 UV_NO_SYNC=1 UV_PYTHON=3.12.15 UV_PYTHON_DOWNLOADS=never
~~~

Family 151 coverage-native-suite-provenance appends one exact cheap Cobertura
witness after all 150 predecessors in the first existing admission child.
The actual native route fails first 1 FAIL/.77s/native 1.6s; the canonical
promotion owner then passes its six pins in 1.79s. Five complete routing/workflow
owners pass 372 each: Python 3.11 in 73.81s/native 75.1s and Python 3.12 in
84.31s/native 85.6s. Retain /tmp/gludd-v012-coverage-suite-routing{311,312}.{log,xml}.
The five files are test_gate_failure_promotion.py,
test_runtime_admission_scheduling.py, test_runtime_admission_durations.py,
test_ci_workflow_integrity.py and test_admission_cold_start_selection.py,
all under tests/unit. Python 3.11 precedes only the typed row narrowing;
the subsequent 15-case replay above covers that final edit.
All 276 Make target contracts and 1157 targets/zero duplicates pass. The full
native resource-inventory writer retains 264 entries with no inventory diff.
Preserve all prior selectors/order, three children 2/1/1, 90 total /
60 quiet, 21 phases / 2850 seconds and full gate authority. No whole-owner widening occurs.

The seven complete documentation/branch-contract/fixture-namespace owners pass
189 cases in 9.52s/native 10.5s. Native Ruff, six separate strict scopes,
script docstrings, six-document locked Markdown and SESSION marker validation
pass. Full resource verification retains all 264 acquisitions and teardown
records. File-line admission remains below 2500. Native Node26 compatibility
passes six controls in 11.60s through its default Python 3.14/two-worker owner;
that receipt is distinct from the canonical release profile. No plugin source
changes and no plugin restart duty are introduced.

### Upstream evidence and release limits

[Coverage.py JSON](https://coverage.readthedocs.io/en/latest/commands/cmd_json.html)
and [XML](https://coverage.readthedocs.io/en/latest/commands/cmd_xml.html) report
coverage data. [Measurement contexts](https://coverage.readthedocs.io/en/latest/contexts.html)
can be arbitrary static labels or tool-assigned dynamic labels, including
multiple combined contexts. Our inference is that labels cannot certify a
successful suite run; owned execution results must supply that evidence.

Firsthand [coverage.py issue 900](https://github.com/coveragepy/coveragepy/issues/900)
opened 2019-12-20 and fixed in 5.0.1 documents contexts lost with relative paths
and JSON/HTML reports. Retain contexts as diagnostics. This historical fixed
issue is not asserted to cause the project's current unconditional mislabel.
Research reuses the mature native reporter; no custom provenance classifier
or third-party package is needed.

Independent exact-source review, candidate collection, full canonical gate,
global 85 aggregate / 75 each-file LINE AND BRANCH and exact-SHA hosted/deployment
proof remain separate. Capacity 96>95 and the original 60907 publication 124 /
90.554s / null trace / no transport remain blocked. A source repair or new SHA
authorizes neither unchanged replay nor promotion. All seven S98 tasks stay
unchecked; ZDD, cleanup, retention and branch/promotion gates remain.

## Exact source e5 and receiving boundary

Exact development e5acbad56eba25af0d8633074d4dd9a671ca0b06 has independent
APPROVE SOURCE ONLY against 815cd5dff5bf337eda518f9d1ff04673145a043c.
Root fully read /tmp/gludd-v012-coverage-suite-source-review.md; no blocking
correction was found. This separate four-document receiving receipt preserves
the frozen code and acknowledges every integrated research/annotation result.

Normal e5 hooks retain zero new clones and six protected checkpoints, with
128623 selected / 128647 collected, 24 deselected and zero collection errors
in 48.50s/native 54.0s. The native receipt is
.gate-logs/observed/test-count/20261022T004337Z-26836-0b5e4352.json:
passed/exit0/53.969s. This collection is distinct from earlier f36/815 receipts
and the focused behavior/coverage above; no full gate result follows.

Source-review exports /tmp/gludd-reviewe5ac and
/tmp/gludd-review815-coverage-suite are review snapshots, not release candidate
sealing. Native disk after e5 remains 96% used/8.0 GiB free. The unchanged
capacity/publication failures and all seven tasks remain open. SESSION owns
the exact continuation command after the actual prerequisite changes.
Full canonical gate/global coverage, exact-SHA hosted/deployment/reviewed-head
and promotion still require their existing native owners. Never restart this
accepted source repair merely because final release evidence remains missing.

## Review checklist

Before recording a percentage in `TASKS.md` or a release note:

1. Run `make audit-coverage` from a clean, namespaced workspace.
2. Confirm the discovered E2E file/function counts and that every shard has a
   terminal pass result.
3. Inspect `e2e_branch_totals`, `e2e_branch_coverage`, and each per-file verdict
   in the aggregate JSON.
4. Resolve every failed shard and every file below 75%; do not lower thresholds
   or edit tests only to make the number pass.
5. Record the report path, commit SHA, command, and gate output beside the task
   evidence.  A missing or stale aggregate report means the coverage claim is
   unverified.
