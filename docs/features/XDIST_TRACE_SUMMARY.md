# Durable xdist Trace Summaries

Status: implemented for the beta4 diagnostic workflow. Last reviewed:
2026-08-20.

## Purpose

Gludd's xdist trace is an append-only JSONL record designed to survive a worker
exit or interrupted test run. The summary command reads that record without
modifying it. A non-empty failure or unfinished-test result remains failure
evidence; formatting options never turn an incomplete run into a passing one.

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

