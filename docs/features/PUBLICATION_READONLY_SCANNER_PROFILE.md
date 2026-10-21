# Bounded Publication Readonly Scanner Profile

## Decision and exact source

S98.4 retains original publication60907:124/90.554s, main2304PASS83.79s,
freshHELP1/.56s, eleven-tail without a terminal result, trace_path:null and no
Git transport. This diagnostic establishes no cause or repaired throughput.
No production optimization is selected and the same wrapper is not reprofiled
merely to obtain a lower elapsed value.

The research decision /tmp/gludd-v012-publication-next-repair-design.md names
one new question: does the actual readonly-TASKS case reported as2.60s in60907
have redundant parent work, or does it wait for its real scanner descendants?
Root reads the decision and executes its bounded existing-owner diagnostic at
clean development9ac03d9f32f81c53ffcca842c2132d470919fb2c. Native worktree-state
remains clean afterward. This is source-bound diagnosis, not exact-SHA CI.

The original test still invokes scripts/detect_secrets_readonly.py with the real
baseline and TASKS.md, asserts the same baseline SHA256 before and after and
requires the real scanner's exit0. No scanner, input file, plugin, assertion or warning policy is substituted.
Original admission's2/1/1 worker policy is untouched; this diagnostic deliberately
profiles one selected case in its controller.

## Exact native execution

The existing profiler is invoked under run-watched,30quiet/45total, one worker,
fatal warnings and the installed311 tuple for both parent and child:

~~~console
make run-watched WORK_ITEM_ID=S98.4 CMD='make --no-print-directory test-files-cprofile TESTFILES=tests/unit/test_secrets_baseline_compaction.py::test_current_task_inventory_passes_real_readonly_secret_owner NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q -n 0 --maxprocesses 1 --ignore-glob=/Users/shawnwilson/gludd/tests/*" GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never' OBSERVED_LABEL=v012-publication-readonly-profile RUN_ID='' STALL_SECS=30 MAX_SECS=45 LOG='' OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS=20 RUN_WATCHED_PYTEST_TRACE=1 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

Native run20261021T224501Z-56941-8e5c4db5 reports1PASS3.48s, exit0/native4.534s.
Immutable log/status are under
.gate-logs/observed/v012-publication-readonly-profile/ with that exact run ID.
Its current pointer is native-produced, not edited as a handoff.

The existing profiler publishes its exclusive directory
.gate-logs/test-files-cprofile/profile-56974-458a40fdea36ae7a/,
containing pytest.pstats, report.txt and trace.jsonl. It records2024072calls/
1979540primitive calls in3.400 process-local profiled seconds. This begins after
pytest session start; cold startup and child/grandchild CPU are unobserved.

The actual test's cumulative call is2.358s. Its single subprocess.run is2.357s,
communicate2.354s and the underlying poll2.353s. Those cumulative rows overlap.
Inference: the measured parent mostly waits for the required native child.
There is no demonstrated redundant parent operation to remove. This cannot
identify which scanner/grandchild operation consumed time or explain60907.

## Keep observer and profiler trace identities distinct

The outer observer allocates
.gate-logs/observed/v012-publication-readonly-profile/20261021T224501Z-56941-8e5c4db5.pytest.jsonl.
Its exact native summary has0events/0started/0finished/0issues. That proves
allocation/readability only, not measured pytest execution.

The profiler owns its separate existing trace and generated run ID
profile-56974-458a40fdea36ae7a. With the installed311 tuple, the actual reader is:

~~~console
make test-xdist-trace-summary LOG=.gate-logs/test-files-cprofile/profile-56974-458a40fdea36ae7a/trace.jsonl RUN_ID=profile-56974-458a40fdea36ae7a TRACE_SUMMARY_TIMINGS=1 TRACE_SUMMARY_MEMORY=0
~~~

That native summary reports5events,1started/1finished/1paired protocol and
zero issues. The controller56974 span is2.832465 wall seconds, including fixture
setup/call/teardown. It is not just the2.358s call or child CPU. Native output
explicitly retains diagnostic-only, gate_evidence=false and release_ready=false.
The existing profiler and reader introduce no second observer, runner or cache.

Do not combine the two run IDs, relabel the empty outer trace as missing test
results or manufacture a trace for60907. Preserve all raw artifacts and previous
declined inventory/tail/real-child proposals.

## Long-lived evidence and proof limits

The [Python311 profiler reference](https://docs.python.org/3.11/library/profile.html)
distinguishes internal and cumulative timings and warns that profiling overhead
does not supply a benchmark. This supports retaining process and metric scope;
it does not establish a Gludd latency repair.

The firsthand [pytest fixture-attribution discussion](https://github.com/pytest-dev/pytest/issues/8741)
starts in2021 with a database-fixture reporter whose first test inherits shared
setup time. Maintainers distinguish setup from call and discuss fixture scope;
the reporter accepts that the setup reporting is technically correct.
It motivates preserving setup/call/protocol distinctions, not blaming pytest or
attributing this repository's old timeout to its last visible node.

The question above is answered only at the parent boundary. Keep no-attribution
and stop this wrapper-profile proposal. A further patch needs a deterministic
redundant-operation witness in the actual native owner; another sample of the
same wrapper is not such a witness. Do not drop required scanning, fixtures,
baseline checks or original selectors.

Actual97percent volume use remains above95. Original60907's failing input
remains unresolved. Only actual prerequisite clearance and a named changed
runtime input permit the existing release-proof admission/final-proof owners.
Source review, local source acceptance, live watchdog handoff and this diagnostic
remain separate from complete canonical gate/global85aggregate/75each-file line
AND branch coverage, exact-SHA all-shard hosted CI/deployment, reviewed-head
receipt and guarded promotion. All seven S98 tasks remain unchecked.
