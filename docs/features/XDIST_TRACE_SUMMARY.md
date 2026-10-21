# Durable xdist Trace Summaries

Status: implemented for the beta4 diagnostic workflow. Last reviewed:
2026-10-20.

## Purpose

Gludd's xdist trace is an append-only JSONL record designed to survive a worker
exit or interrupted test run. The summary command reads that record without
modifying it. A non-empty failure or unfinished-test result remains failure
evidence; formatting options never turn an incomplete run into a passing one.

## Parent-Readable Observed Commands

Long coverage and collection commands use `scripts/stream_command.py` as their
single observer. This reuses the runner already used by `gate-refresh`; it does
not add a daemon, dependency, or second watchdog implementation. A delegated
agent's terminal belongs to that agent's tool transcript, so its stdout is not
routed into the parent agent's transcript while the tool is running. The shared
filesystem is the durable cross-agent channel.

Each invocation writes beneath `.gate-logs/observed/<label>/`:

- `current.json` is the atomically replaced parent-facing snapshot.
- `<run-id>.json` preserves that run's final and heartbeat state.
- `<run-id>.log` holds complete combined child output.
- `<run-id>.pytest.jsonl` holds pytest events when `--pytest-trace` is enabled.

Status files are written beside their destination, flushed, fsynced, and moved
with `os.replace`. The schema is versioned and always includes `kind`, `label`,
`run_id`, `state`, owner and child PIDs, start/update/last-output timestamps,
heartbeat sequence, elapsed and quiet seconds, byte and line counts, exit code,
termination reason, log path, and optional trace path. States are `starting`,
`running`, `passed`, `failed`, `timed_out`, or `interrupted`.

The default heartbeat is 30 seconds and a reader treats an in-progress snapshot
older than 90 seconds as stale. A dead owner is reported as orphaned; neither
inference is presented as success. Status and log-write failures fail closed
with exit 125. Runtime or quiet-output deadlines terminate the owned process
group and return 124. Signals are forwarded to that group and return
`128 + signal`; ordinary child exit codes are unchanged.

The bounded readers never follow a log indefinitely:

```console
make run-watched CMD='make ci-repro-linux PYV=3.11' \
  OBSERVED_LABEL=ci-repro \
  RUN_ID=ci-repro-311 STALL_SECS=180 MAX_SECS=3600 \
  LOG=.gate-logs/observed/ci-repro/ci-repro-311.log \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 \
  OBSERVED_RETAIN_RUNS=20
make observed-status OBSERVED_LABEL=ci-repro RUN_ID=ci-repro-311 \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_STALE_SECS=90
make observed-tail OBSERVED_LABEL=ci-repro RUN_ID=ci-repro-311 \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_TAIL_LINES=80
```

`OBSERVED_LABEL` names the bounded history directory; `RUN_ID` selects one
immutable retained record within it. Omitting `RUN_ID`, or setting
`RUN_ID=current` when every Make variable must be explicit, reads
`current.json`. The explicit alias validates the pointer's schema, kind, label,
and safe payload `run_id`, then reports that real run identity; malformed
pointers fail closed instead of being treated as a run literally named
`current`. Other `RUN_ID` values keep selecting and validating the exact
immutable record. Keeping these identities explicit prevents a completed run ID
from being mistaken for a label and reported as a missing `current.json` path.

`coverage-files`, `test-count`, `collect-check`, and `run-watched` all use this
contract. Quiet collection still updates its heartbeat while writing complete
output to the run log; the former opaque `/tmp/gludd-collect-output.txt` file is
gone. Coverage keeps its serial execution model and publishes the JSON report
only after aggregate and per-file audits pass. On exit it removes only its
namespaced basetemp, coverage-data fragments, and unpublished report fragment;
durable status, logs, traces, and the last known-good report remain available.
No service restart or deployment mutation is involved.
After a terminal publish, the observer retains the newest 20 terminal runs per
label and prunes only older run-owned status/log/trace triplets. Active runs,
`current.json`, custom external logs, and malformed evidence are never pruned.
Exact-run status and tail requests remain available after `current.json`
advances, until that terminal triplet reaches the configured rotation bound.

While a run is live, `make ps` and `make active-work-status` also inspect atomic
`current.json` pointers in every registered worktree. A status document cannot
invent a process: the recorded owner must be a live `stream_command.py` process
with the same label, and a running child must be its direct OS child. Only that
verified tree is reported. Discovery reads at most the 64 most recently updated
label pointers and reports at most 128 observer-owned processes. The JSON view
places their PID, PPID, task, label, and tree role in `observed_processes`;
delegated model-agent names remain logical status and are never presented as OS
PIDs.

Pytest-xdist documents that worker stdout and stderr
[cannot be transferred live](https://pytest-xdist.readthedocs.io/en/stable/known-limitations.html#output-stdout-and-stderr-from-workers)
because execnet does not support it. The pytest hook trace is the workaround: it
records bounded lifecycle events rather than pretending worker output can cross
that channel. Every event carries the observed run ID, collection records only
its count, and summaries can select one run from an append-only log:

```console
make test-xdist-trace-summary LOG=/tmp/gludd-xdist-progress.log \
  RUN_ID=20260902T120000Z-1234-abcd1234
```

The reporting modes are deliberately separate:

- The default compact mode reports run counts, every unique failed node ID,
  unfinished tests, and each worker's last test. It omits verbose tracebacks and
  memory collections.
- `--failures-only` emits only the trace path, failure counts, unique failed node
  IDs, and unfinished tests. This is the smallest durable failure handoff.
- `--include-memory` opts either concise mode into the per-worker memory map and
  largest-increase list.
- `--verbose` retains the complete diagnostic summary, including up to 50
  failure records and the memory collections.

The existing Make target keeps its compact default:

```console
make test-xdist-trace-summary LOG=/tmp/gludd-beta4-full-trace-rerun.log
```

The script-level flags are intended for a diagnostic consumer that needs a
failures-only or memory-enriched JSON document. Output is deterministic for the
same trace: node IDs retain their first-seen order, workers are sorted, memory
increases are ordered largest first, and no reporting mode deletes the source
log. That read-only lifecycle preserves zero-downtime diagnostic evidence while
another namespaced run writes a different log.

## Validated test-protocol wall spans (v0.1.2)

The existing summary owner optionally ranks the retained START/FINISH spans:

```console
make test-xdist-trace-summary LOG=/tmp/gludd-xdist-progress.log RUN_ID=selected-run TRACE_SUMMARY_TIMINGS=1
```

Set TRACE_SUMMARY_TIMINGS=0 for the unchanged compact default. Only0/1 are
accepted. The script's equivalent flag is --include-timings; it also works
with --failures-only. Verbose output includes the full timing diagnostic.

Pairing requires exact nonempty run ID, worker label, positive process PID and
full node ID. Repeated serial occurrences remain separate. Module and worker
totals retain the same run/worker/PID identity, including restarted workers;
parameters and @group suffixes stay intact in node rows. Missing identity,
non-finite/invalid timestamps, duplicate or overlapping STARTs, unmatched
FINISH, unfinished START and backward/overlapping intervals produce issues.
Ambiguous spans are excluded. Counts and original failure/incomplete evidence
remain available; source traces are never modified.

Each diagnostic array is capped at25, with full paired/issue counts and stable
ordering. The clock is the producer's time.time wall clock. These protocol
spans include setup, fixtures, call, teardown and child waits. They exclude
unrecorded cold startup; parallel workers overlap. Neither cumulative profiler
rows nor module/worker sums are CPU measurements or a combined elapsed time.
The diagnostic explicitly retains authority=diagnostic-only,
gate_evidence=false and release_ready=false. Its exit0 means report retrieval,
never passing pytest, repaired CI or release readiness.

The separate maintained-tool research selected the existing stdlib JSON,
cProfile/pstats and pytest-xdist trace producers. No parser, profiler package,
runner, daemon, observer or cache is added.
[Python's profiler reference](https://docs.python.org/3.11/library/profile.html)
distinguishes self time from cumulative time, which includes called functions.
The firsthand October2018
[xdist collection-efficiency report](https://github.com/pytest-dev/pytest-xdist/issues/353)
describes an idle controller while workers collect and explicitly seeks worker
profiles. This long-lived report supports measuring the actual process; it
does not diagnose Gludd's current timeout or justify disabling plugins.

### Measured attribution before selecting a repair

Published development6ec94ab4daf59e45b44720a44ffca19b2e7d0b23 has hosted Build
39519193318. Its311 job118958621444 passes2023main/73.87s, separate help1/1.62s
and ten tail cases, then the native90/60 observer times out124/90.6s during the
last lock assertion. The312 job118958621454 passes the complete admission in
89.5s and fresh127723/127747collection,24deselected/0errors. The failed311 lane
blocks all eight canonical shards. Preserve their censored/unknown intervals;
neither the312 lane nor the diagnostic Azure coverage supplies a green Build.

The exact-source local diagnostic uses registered macOS311.14/default
Hypothesis; hosted311.17/Linux uses built-in ci. These uncontrolled environments
cannot establish a latency improvement or the Hypothesis profile as a cause.
Main diagnostic2023PASS61.24s/native64.1s measures61.196s of parent profile,
including60.101s lock waits for worker messages. The complete unchanged tail
passes11/11.90s/native12.8s. Both use a180s diagnostic cap and confer no90/60
repair receipt.

Read-only summary of the original main trace pairs2023/2023 with0issues;
worker82870/gw0 has962spans/51.793266s and82871/gw1 has1061/52.239549s.
Heartbeat module spans are8.014915s/15cases and9.793082s/24cases respectively.
The largest individual span is the original fixture-inventory guard5.463142s.
These are protocol observations, not a proven production hotspot. The tail
pairs11/11 with0issues: reviewed-head3spans/7.281170s, lock invariant1/2.428635s
and watchdog7/0.371636s. Do not weaken the lock assertion or add a test-only
TOML cache because it printed last.

Native raw profiles are .gate-logs/test-files-cprofile/
profile-82867-e1377694a456ce79 and profile-91748-806f15bfe5ddfda0
(report.txt,pytest.pstats,trace.jsonl). Literal unchanged native summary output
is retained as /tmp/gludd-v012-main-trace-timings.txt and
/tmp/gludd-v012-tail-trace-timings.txt. The exact measured commands, limits,
production cumulative rows and rejected speculative Git batching are retained
in /tmp/gludd-v012-hosted-runtime-cost-decision.md. Summarize these retained
traces through the owner; no2023-case rerun is needed to recover their ranking.

The new45-case matrix first records44FAIL/1PASS1.55s/native2.7s.
An added repeated-node/process-isolation control then fails1/0.79s against the
intermediate global module aggregation. Only the existing summary owner and
its option/help/contract change; original trace assertions stay intact.
Initial three-owner acceptance passes153/7.90s/native8.9s. The four-file
coverage replay passes290tests/28.20s but correctly rejects70% aggregate:
stream_command's existing startup paths require their complete owners.
That failed receipt remains failed; the successor adds those whole owners
without omitting any production file or lowering85aggregate/75each BOTH.
Final measurement and exact source commit are recorded with the receiving
handoff; none of this diagnostic support closes S98.4.

Final unchanged four-file coverage then passes431/70.85s/native73.0s:
91.5% aggregate lines/85.5% branches, every file above75% both; complete
summarizer98.2%/95.6%. Native immutable audit is
.gate-logs/coverage-20261021T002239Z.json. Its source scope is unchanged from the
failed290-case attempt. No covered line or production file was omitted.

A further native negative witness catches backtick execution inside the new
Make option before commit:1FAIL/1.29s. Make now transports that literal through
the override-owned GLUDD_TRACE_SUMMARY_TIMINGS carrier and selects only a
constant validated CLI flag; the public variable is unexported. No raw value
enters shell command text. The final47-case timing matrix plus whole original
diagnostic, fixture, Make-contract, session and objective owners passes201
cases/9.90s/native10.9s. Ruff, strict2files,269target contracts,1174targets/0
duplicates and the documented mode0 behavioral example pass. These later
Make/test-layout changes do not alter the measured summarizer Python bytes.

Reproduce the complete registered profile with the installed checkout-owned
runtime selected explicitly; align VIRTUAL_ENV with UV_PROJECT_ENVIRONMENT:

```console
make coverage-files \
  COVERAGE_TESTFILES='tests/unit/test_observed_command.py tests/unit/test_xdist_trace_diagnostics.py tests/unit/test_xdist_trace_timings.py tests/unit/test_gate_refresh_streaming.py tests/unit/test_observability_guardrails.py tests/unit/test_active_work_status.py tests/unit/test_active_work_resource_observability.py tests/unit/test_pytest_param_fixture_collisions.py tests/unit/test_runtime_startup_diagnostic.py tests/unit/test_observer_retention_concurrency.py tests/unit/test_item_progress_retention.py tests/unit/test_owned_grandchild_cleanup.py -n 0 --maxprocesses 1' \
  COVERAGE_CONFIG=config/coverage_observed_command.ini \
  COVERAGE_REPORT=.gate-logs/coverage-v012-trace-timings-complete.json \
  COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 \
  OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
```

### Independent occurrence-accounting repair

Source5b10dd57eef9cc70f0b56fb34ca3dbfa9b7443f9 retains fresh
127770/127794collection,24deselected/0errors46.99s/native52.1s, but independent
review returns CHANGES REQUIRED at
/tmp/gludd-v012-candidate-5b10dd5-source-review.md. Its exact six-event witness
START A10,START A11,FINISH A12,START B13,FINISH B14,FINISH A15 wrongly accepts
B13-14 because one pending entry overwrites the second A occurrence.

Five additive controls first fail5/1.05s/native2.1s. The same existing timing
owner now counts each pending occurrence, poisons overlapping spans and consumes
one occurrence per matching FINISH. A stream resumes timing only after every
occurrence drains. An unfinished_start issue includes outstanding_occurrences;
original legacy failure/unfinished/memory processing is unchanged. Exact
witness, two/three duplicate counts, incomplete drains and serial recovery retain
all prior47controls, giving52timing cases.

The same complete12-owner/four-production-file profile passes437/71.34s,
native73.4s:91.5aggregate lines/85.6branches, every file above75% both.
Complete summarizer98.3line/95.8branch; immutable audit
.gate-logs/coverage-20261021T003417Z.json. Final52timing controls plus whole
original diagnostic/fixture/session/objective owners pass195/11.06s,
native12.4s; Ruff/strict2files, locked Markdown and universal line limits pass.
Original negative review, source
receipts and failed witnesses remain. Successor source review, actual hosted
cost repair and all final release proofs stay separate.

## RSS Unit Contract

Legacy events call their field `rss_kb`, but Python's `ru_maxrss` is
platform-dependent: macOS reports bytes while Linux reports KiB. Treating the
macOS value as KiB inflates memory by 1,024 times.

The summary normalizes every supported input to unambiguous output fields:

- `*_rss_bytes` is an integer byte count.
- `*_rss_kib` is the byte count divided by 1,024 and rounded down.
- Future `rss_bytes` or `rss_kib` event fields are authoritative.
- Legacy `rss_kb` input is inferred as bytes when the trace contains a value of
  at least 16,777,216; otherwise it is inferred as KiB. The chosen legacy unit
  is recorded as `legacy_rss_input_unit`.
- `--legacy-rss-unit bytes` and `--legacy-rss-unit kib` override inference for
  an exceptional or relocated legacy trace.

Memory collections remain out of compact JSON unless `--include-memory` or
`--verbose` explicitly requests them. This avoids transferring the per-worker
map and the 25 largest increases when a consumer only needs a verdict handoff.

## Beta4 Rerun Evidence

The durable trace `/tmp/gludd-beta4-full-trace-rerun.log` was inspected on
2026-08-20. It contained 188,531 events, 93,933 starts, 93,932 finishes, and one
unfinished test on `gw1`. The final controller failure identifies
`tests/unit/test_skills_router_deep.py::TestSkillsFetchGithubErrors::test_fetch_github_skill_with_branch_parameter`
as the node running when the worker exited.

The same trace begins near a legacy `rss_kb` value of 92,684,288. On the macOS
run that is 92,684,288 bytes, or 90,512 KiB, rather than 92,684,288 KiB. This
observation is pinned by a deterministic unit test; the repository test does not
depend on the temporary trace continuing to exist.

The trace also contains test-generated diagnostic events, so its 661 failed
reports and 331 unique failed node IDs are evidence to inspect, not a substitute
for pytest's exit status. The summary therefore retains failure counts and
unfinished work rather than manufacturing a pass/fail verdict.

## Upstream Practitioner Evidence

These upstream reports were reviewed on 2026-08-20. They motivate durable,
bounded diagnostics but do not establish the root cause of Gludd's worker exit.

- pytest-xdist issue
  [#110](https://github.com/pytest-dev/pytest-xdist/issues/110), opened
  2017-01-11, records a practitioner finding workers stuck for more than 13
  hours and using the last visible worker/test assignment to narrow the hang.
  That long-lived report directly motivates a durable last-event trace and a
  heartbeat that a separate parent can read.

- pytest-xdist issue
  [#922](https://github.com/pytest-dev/pytest-xdist/issues/922), opened
  2023-06-23, records a user whose parallel run crashed after roughly 10 to 15
  tests with a worker/internal error.
- pytest-xdist issue
  [#739](https://github.com/pytest-dev/pytest-xdist/issues/739), opened
  2021-12-03, records an intermittent macOS parallel-worker crash reported as
  rarer than one run in 100. The low reproduction rate supports retaining a
  durable last-node record instead of relying on an immediate rerun.
- pytest issue [#5642](https://github.com/pytest-dev/pytest/issues/5642), opened
  2019-07-22, records fixture objects being retained much longer than expected,
  sometimes until the end of a suite. Per-worker peak and growth fields make
  this class of long-run memory behavior inspectable.
- pytest-xdist issue
  [#877](https://github.com/pytest-dev/pytest-xdist/issues/877), opened
  2023-02-12, describes CI output becoming impractical for a suite with more
  than 100,000 tests. Failures-only output and opt-in memory collections address
  the same operational need without discarding the underlying JSONL evidence.
