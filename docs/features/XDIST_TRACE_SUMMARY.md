# Durable xdist Trace Summaries

Status: implemented for the beta4 diagnostic workflow. Last reviewed:
2026-09-03.

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
immutable retained record within it. Omitting `RUN_ID` reads `current.json`.
Keeping these identities explicit prevents a completed run ID from being
mistaken for a label and reported as a missing `current.json` path.

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
