# FreeLLMAPI Lease-Aware Rollback

## Outcome

FreeLLMAPI bridge generations now retain every artifact used by in-flight work,
even after later promotions make that generation neither active nor immediately
previous. Rollback atomically changes the generation assigned to new work, then
waits a bounded time for leases on the displaced generation without moving or
cancelling them.

This is a process-local rollback rehearsal for the executable ZDD boundary in
S83.163. Every drain result remains `runtime_admitted=false` and the candidate
decision remains `HOLD`. It performs no network or provider call, artifact
deletion, sidecar launch, or task scheduling outside Gludd.

## Ownership contract

`FreeLLMAPIRollbackWorkflow` owns a bounded map from a content-free lease digest
to the immutable generation selected when the lease starts.

| Operation | Generation effect | Cleanup effect |
|---|---|---|
| `acquire_lease` | Pins the current generation | Protects every artifact in that generation |
| `promote` | Routes later leases to green | Keeps active leases on their original generation |
| `rollback` | Routes later leases back to blue | Keeps in-flight green leases valid and protected |
| `rollback_and_drain` | Atomically routes new leases to blue, then waits for green | Protects the pending drain target through timeout and retry |
| `release_lease` | Removes one exact ownership record | Makes an otherwise old generation eligible |
| `lease` context | Acquires before work and releases in `finally` | Prevents exception-path ownership leaks |

Lease identities must be lowercase `sha256:` URIs. Errors never reflect the
identity, prompt, model output, endpoint, provider body, or task content. Duplicate
acquisition and unknown release fail closed. The registry defaults to 1,024 active
leases and refuses the next lease at capacity instead of growing without bound.
`active_lease_count` supplies content-free lifecycle telemetry.

All generation pointers, lease ownership, pending-drain state, and cleanup
snapshots share one `threading.Condition(threading.RLock())`. The first
`rollback_and_drain` call swaps the active pointer while holding that lock and
records the displaced generation. A retry waits for that same generation; it
does not switch the pointer a second time. Leases acquired after the switch bind
to the new active generation and do not extend the old generation's drain.

Each successful exact release calls `Condition.notify_all()` after deleting its
ownership record. Unknown or malformed releases do not notify. The waiter loops
over the generation-specific lease count, so notifications for other generations
and spurious wakeups cannot claim completion.

## ZDD and rollback

Blue/green assignment is a pointer change for new work. Work that began on blue
finishes on blue after promotion; work that began on green finishes on green after
rollback. Artifact cleanup takes the union of the active generation, immediately
previous generation, and every leased generation. It cannot reclaim bytes still
needed by an in-flight invocation.

The context-managed API is the normal integration boundary:

```python
with workflow.lease(lease_digest) as generation:
    invoke_generation(generation)
```

`finally` release preserves exact ownership when invocation raises or is
cancelled. Reverting this source change requires no schema migration or service
restart, but an operator must first drain leases created by the newer process.
The state is process-local and contains only immutable digests and generation
metadata.

`rollback_and_drain` accepts only finite timeouts where
`0 < timeout_seconds <= 3600` and computes its deadline with `time.monotonic()`;
wall-clock changes therefore cannot lengthen or shorten the wait. Its optional
heartbeat interval is finite, positive, and at most 30 seconds. Heartbeats expose
only generation numbers, remaining lease count, and monotonic elapsed time--no
lease digest, artifact digest, prompt, response, endpoint, or provider data.

On timeout, the active pointer stays rolled back and the pending green generation
stays protected. Exact lease release followed by a retry acknowledges the drain
and removes that extra protection. `cleanup_eligible_generations` can then report
an otherwise unreferenced generation as eligible; it never deletes an artifact.

## Proven workflow sources

- Python's official
  [`Condition` documentation](https://docs.python.org/3/library/threading.html#condition-objects)
  defines the lock-bound predicate loop, release-while-waiting behavior, and
  notification/reacquisition semantics used here.
- Python's official
  [`time.monotonic` documentation](https://docs.python.org/3/library/time.html#time.monotonic)
  defines the non-decreasing clock used for the bounded deadline.
- Envoy's official
  [draining documentation](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/operations/draining)
  separates stopping new streams from waiting for existing streams to complete;
  the workflow mirrors that separation for generation leases without embedding
  Envoy or starting another process.

## Practitioner evidence

These reports are not treated as proof about Gludd; they are long-lived operator
evidence for the failure mode the tests reproduce.

- [Envoy issue #2776](https://github.com/envoyproxy/envoy/issues/2776), opened in
  2018, reports requests losing their connection during hot restart when the old
  process was ended before its drain completed. Gludd therefore never equates a
  successful generation switch with permission to reclaim the prior generation.
- [Gunicorn issue #2297](https://github.com/benoitc/gunicorn/issues/2297), opened
  in 2020, reports keepalive connections remaining stuck until the graceful
  timeout after a worker stopped accepting new requests. Gludd therefore combines
  an explicit ownership predicate with exact release notifications and a bounded
  timeout instead of inferring drain completion from routing state.

## Verification

The focused tests cover promotion, atomic rollback under concurrency, pre- and
post-switch lease identity, monotonic timeout bounds, content-free heartbeats,
exact release notification, timeout protection, retry without a second switch,
exception cleanup, capacity exhaustion, and post-drain cleanup eligibility.
`config/coverage_freellmapi_rollback_workflow.ini` measures line and branch
coverage for this module; it must remain at least 85%, with the project floor of
75% still applying to every measured file.
