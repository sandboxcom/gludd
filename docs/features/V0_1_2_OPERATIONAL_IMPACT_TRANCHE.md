# v0.1.2 Operational-Impact Tranche

## Scope and mechanical selection

This tranche treats the 84 unchecked tasks outside the exact v0.1.1 milestone
range (`S83.157` through `S83.168`) as the future-release backlog. A bounded
parser ranked every task line after excluding OpenJEV and release-pipeline
work. The score is deterministic: task priority, number of operational domains
named, explicit production-path fan-out, cross-cutting language, and bounded
effort are positive weights; already-recorded completion evidence is a negative
weight. A greedy pass then rejects candidates whose declared production files
overlap a higher-ranked selection.

The resulting disjoint selections are:

| Rank | Task | Score | Operational reach | Owned production boundary |
| --- | --- | ---: | --- | --- |
| 1 | S83.136 | 195 | Managed self-improvement adapter admission | `self_improve` protocols |
| 2 | S83.116 | 167 | Shared debounce, throttle, and watchdog timing | `util/debounce_v2.py` |
| 3 | S83.104 | 157 | Repository-wide Git mutation serialization | `git_automation/locking.py` |

S83.137 and the next self-improvement candidates rank highly but overlap
S83.136. S83.88 ties S83.104 numerically but owns a frozen-distribution launch
boundary adjacent to current release work, so the release-domain exclusion
selects S83.104. The three selected source families do not import one another,
share mutable state, or require a coordinated rollout.

## S83.116: trailing debounce quiet-period integrity

The synchronous v2 debounce retained the first deadline in a burst. Calls that
arrived later updated the payload but not the deadline, which changed debounce
into a fixed-window throttle and could publish the newest payload before one
complete quiet period. The timer now follows the most recent call while
`max_wait` remains the only cap on a sustained burst. A deterministic clock
regression pins both the no-early-emission boundary and the eventual latest
payload.

This behavior matters anywhere Gludd coalesces noisy state, health, or file
events: premature emission can cause redundant planning, stale observations,
or work against a still-changing input. A long-lived Lodash practitioner report
describes lost or surprising trailing calls when leading, trailing, and
`maxWait` interact, reinforcing the need to specify the last-call boundary
directly: <https://github.com/lodash/lodash/issues/1934>.

## S83.136: runtime-checkable method-only adapters

Planned in this tranche: extend the existing method-only runtime protocol
contract to newer local-worker and managed-evaluation injection seams. Data-
bearing result protocols remain static-only because a shallow structural check
cannot validate their values.

The Python typing community has documented both property side effects during
runtime protocol checks and the fragility of data-member checks. Those reports
support Gludd's narrow method-only policy:
<https://github.com/python/typing/issues/1363> and
<https://github.com/python/typing/discussions/1941>.

## S83.104: cancellation-safe asynchronous Git locking

Planned in this tranche: prove that cancelling an asynchronous lock acquisition
cannot leave a background executor holding the repository mutex. The worker
must finish its blocking acquire and, if it acquired the lock, release it before
the executor is shut down and cancellation propagates.

CPython users have repeatedly reported that cancelling `run_in_executor` work
does not stop its worker thread, including issue
<https://github.com/python/cpython/issues/107505> and the older shutdown/hang
report <https://github.com/python/cpython/issues/73495>. Gludd therefore owns
explicit post-cancellation cleanup instead of treating coroutine cancellation
as worker termination.

## ZDD, observability, and rollback

All three changes are local behavioral corrections with no schema, wire-format,
credential, daemon, or deployment-state migration. They preserve the public
entry points, add deterministic regressions before implementation, and run in
the existing namespaced test environment. Each task lands as an independent
commit, so rollback can remove one boundary without reverting the other two.
No service restart or downtime is required.
