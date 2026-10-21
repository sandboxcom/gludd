# Import-Time Measurement Integrity

## Trigger and scope (2026-10-21 UTC)

S98.4's broader import-boundary acceptance exposes two skipped cold-start checks:
514PASS/2SKIP on each supported runtime, preserved in
/tmp/gludd-v012-import-whole-311.log/XML and312 equivalent.
The old helper invents a Python313 JSON transition and otherwise expects numeric
whitespace columns. Actual CPython output has an import time: prefix, pipe
separators and self-first/cumulative-second microseconds. Discarding all real
rows reaches two missing-data skips. Its scalar helper also converts failed or
unparseable children to0.0 milliseconds, which can satisfy an upper bound.

Root owns the coupled repair on development after6f0dc0b. An external authored
proposal is /tmp/gludd-v012-importtime-{decision.md,proposal.patch,
validation-plan.md}; it is untested proposal evidence, not source approval.
Retain source infrastructure separately from cold-consumer acceptance and the
original90/60 runtime-family/exact-SHA release proof.

## Reuse the native numeric row decoder

Expose the existing scripts/stream_command.py numeric decoder as
parse_importtime_row(line), returning (self_us,cumulative_us,module_name).
The original _startup_imports state machine delegates at its existing admitted
row boundary. Its grammar, integer ordering and errors remain the same. No
second parser, loader, observer, runner or proof cache is introduced.

All original three-segment/session/platform/collection constraints, strict byte
decoding, unknown/censored attribution, terminal-state/exit-code retention,
producer namespace, identity/hash/freshness, bounded descriptor reads, FIFO/
symlink refusal and archive publication behavior remain. The decoder itself
grants no observation or release authority. Summing cumulative rows remains
invalid because their work overlaps.

Sixteen additive API/delegation controls fail before extraction:16FAIL0.79s/
native1.6s, /tmp/gludd-v012-importtime-decoder-red.log/XML. This is a missing-API
RED, distinct from the old consumer's demonstrated false skips. Controls retain
numeric column order, valid zero/indentation/tab rows, reject malformed/negative/
cached/invented rows and show each original admitted row delegates exactly once.

The first complete owner replay retains1FAIL/95PASS on BOTH runtimes
(3111.78s/native2.7s;3122.11s/native3.3s). The read-only native Make fixture
inherits run-watched's exported LOG and correctly refuses that outside namespace.
Its invocation now names its own existing checkout-owned LOG explicitly, as the
target contract requires. All original canary, literal input, readonly and
namespace assertions stay intact; no production guard is relaxed. Keep
/tmp/gludd-v012-importtime-decoder-whole-311.log/XML and312 equivalent.
Clearing only MAKEFLAGS is insufficient because exported Make variables also
remain in the child environment.

## Coverage and invocation ownership

config/coverage_observed_command.ini binds all four original complete production
files to required inherited PWD. This follows the already accepted readiness
profile boundary: real checkout files are measured, deliberately modified fixture
copies are excluded by identity rather than coverage omission. No original
production file, assertion, threshold or branch metric is removed. Invoke the
existing serial coverage-files owner from the selected checkout root.

The complete coverage scope retains the six original documented owners plus
native startup, item-progress retention, the repository fixture guard and the
existing xdist timing owner:

~~~text
tests/unit/test_observed_command.py
tests/unit/test_xdist_trace_diagnostics.py
tests/unit/test_gate_refresh_streaming.py
tests/unit/test_observability_guardrails.py
tests/unit/test_active_work_status.py
tests/unit/test_active_work_resource_observability.py
tests/unit/test_runtime_startup_diagnostic.py
tests/unit/test_item_progress_retention.py
tests/unit/test_pytest_param_fixture_collisions.py
tests/unit/test_xdist_trace_timings.py
~~~

~~~console
make coverage-files COVERAGE_TESTFILES='tests/unit/test_observed_command.py tests/unit/test_xdist_trace_diagnostics.py tests/unit/test_gate_refresh_streaming.py tests/unit/test_observability_guardrails.py tests/unit/test_active_work_status.py tests/unit/test_active_work_resource_observability.py tests/unit/test_runtime_startup_diagnostic.py tests/unit/test_item_progress_retention.py tests/unit/test_pytest_param_fixture_collisions.py tests/unit/test_xdist_trace_timings.py' COVERAGE_CONFIG=config/coverage_observed_command.ini COVERAGE_REPORT=.gate-logs/coverage-importtime-decoder.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
~~~

Select the installed canonical311 environment for BOTH UV_PROJECT_ENVIRONMENT and
VIRTUAL_ENV, with UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never, before
the outer Make invocation. SESSION names the registered environment. This
300-second source coverage contract is separate from unchanged90/60 admission;
it supplies no global/full-gate or hosted proof.

## Cold-consumer repair obligations

The existing test helper must reuse the canonical row decoder, enforce the native
child return code, retain original stdout/stderr on error, and reject missing or
malformed timing instead of fabricating zero. Finite nonnegative scalar samples
retain their seconds-to-milliseconds conversion. One real child's header and
numeric rows are required; cached labels, duplicate headers, rows before header
and partial malformed output are errors. Unrelated stderr is not timing data.

Keep fresh interpreter argv, installed sys.executable, bytecode policy, source
path,30-second timeout,500ms cold-import,10ms root-self and30% each-module
thresholds and the full original denominator. Missing or all-zero attribution
must fail. Real timing failures require measured source repair; they never
justify a skip, changed denominator or relaxed limit.

## Primary and firsthand research

[Python311](https://docs.python.org/3.11/using/cmdline.html#cmdoption-X) and
[Python312](https://docs.python.org/3.12/using/cmdline.html#cmdoption-X) document
self/cumulative durations and the threaded-output limitation.
[CPython311](https://github.com/python/cpython/blob/3.11/Python/import.c),
[312](https://github.com/python/cpython/blob/3.12/Python/import.c) and
[313](https://github.com/python/cpython/blob/3.13/Python/import.c) emit the same
numeric prefix/pipe format;313 does not switch to JSON.
[Current documentation](https://docs.python.org/3/using/cmdline.html#cmdoption-X)
adds cached cells for explicit importtime=2 from314, which this owner does not use.

The longstanding firsthand
[2017 bpo31415 discussion, preserved as75596](https://github.com/python/cpython/issues/75596)
records machine-readability concerns and an example of the actual numeric format.
Direct older bpo fetch failures remain in the proposal alongside the successful
GitHub/indexed retrieval; an unavailable page is not confirming evidence.
These sources justify reusing the established standard-library decoder, not
project correctness or performance claims.

## Native source acceptance

Complete native startup acceptance passes96/2.49s/native3.5s on311 and
96/2.73s/native3.8s on312, fatal warnings and zero skips/errors;
/tmp/gludd-v012-importtime-decoder-final-311.log/XML and312 equivalent. Ruff,
strict source/test typing and explicit script docstrings pass.

The first nine-owner coverage run passes417/84.47s/native86.7s but correctly
fails BOTH-metric coverage: aggregate88.9line/81.4branch, summarizer78.4/62.5.
Native audit .gate-logs/coverage-20261021T061715Z.json and observed run
20261021T061548Z-68332-6e38641f retain the failure. Its missing input is the
existing complete tests/unit/test_xdist_trace_timings.py owner for the already
expanded summarizer, not another parser repair or a lower threshold. Include
that tenth owner and correct the existing coverage-files contract example to
name the complete scope. Native contract validation passes270 targets; the
changed example executes the original300-second coverage contract. It passes
469/64.43s/native66.4s with91.3line/85.6branch aggregate; all four complete files
pass75 for BOTH, including stream90.7/84.6 and summarizer98.3/95.8. Native report
.gate-logs/coverage-observed-command.json, audit
.gate-logs/coverage-20261021T061949Z.json and observed run
20261021T061843Z-71512-3aab7b10 retain the actual result. The complete11-case
Make-contract owner passes1.73s, /tmp/gludd-v012-coverage-contract-current.xml.
This focused unit profile supplies no E2E, global/full-gate, exact-SHA hosted/
deployment, capacity clearance or task closure proof.
