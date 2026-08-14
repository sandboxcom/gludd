# Monotonic Debounce and Throttle State

## Problem and Root Cause

The deep debounce suite carried private prototype classes instead of importing
the product implementation. Those copies used numeric zero as both a valid
clock reading and a "never fired" sentinel, so leading calls, resets, trailing
delivery, max-wait, and instance isolation failed when a deterministic clock
started at zero. The async implementation also stored the leading callback and
the trailing timer in the same task slot. Enabling both edges therefore
cancelled the callback it had just scheduled.

The adjacent timing replay exposed the same state-identity error in
`StallWatchdog.start`: an API documented as idempotent overwrote the original
start time and deadline. A repeated registration must leave the live operation
unchanged until `finish` closes that generation.

## Contract

- `Debouncer`, `Throttle`, and `AsyncDebouncer` are production APIs. Tests and
  callers do not maintain substitute state machines.
- A synchronous caller may inject a monotonic-compatible clock. `None`, not a
  timestamp such as `0.0`, represents missing state.
- A throttle admits the configured leading edge at most once per window and
  retains only the latest suppressed call for its trailing edge. A late
  deterministic tick records the logical timer deadline, not wall-clock delay,
  so test scheduling jitter cannot move the next window.
- A debounce max-wait may be shorter than its quiet-period wait. It still must
  be positive and finite. Every wait must be finite and nonnegative, preventing
  `NaN` or infinity from creating immortal pending work.
- Async scheduling and timestamp comparison share the running event loop's
  monotonic clock. The leading callback and replaceable trailing timer have
  separate ownership, so scheduling one cannot cancel the other.
- `cancel` removes delayed trailing work; `reset` additionally restores the
  leading-edge admission state. Cancellation remains cooperative at the next
  event-loop checkpoint.
- Re-registering a live watchdog operation is a no-op. Callers must `finish`
  before reusing an operation identifier with a new start or deadline.

## Mature Primitives and Practitioner Evidence

The implementation uses Python's maintained primitives rather than a custom
wall-clock or scheduler: [`time.monotonic`](https://docs.python.org/3/library/time.html#time.monotonic),
the event loop's [`loop.time`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.time),
`asyncio.create_task`, and `asyncio.sleep`. The
[`asyncio` task documentation](https://docs.python.org/3/library/asyncio-task.html#creating-tasks)
also recommends retaining a strong reference to background tasks until their
completion, which the live-leading-task set does with automatic discard.

The edge and max-wait vocabulary follows the mature
[`lodash.debounce` contract](https://lodash.com/docs/#debounce), including the
rule that a both-edge single call does not produce a duplicate trailing call.
Lodash is not added as a Python dependency; only its long-lived behavioral
model informs this process-local implementation.

Long-lived reports show why these details are durable operational concerns:

- A [2015 leading-edge debounce question](https://stackoverflow.com/questions/34552038/debounce-function-that-fires-first-then-debounces-subsequent-actions)
  records the recurring requirement to fire once immediately and suppress the
  rest of a burst.
- A [2016 asyncio scheduling and cancellation report](https://stackoverflow.com/questions/40016501/how-to-schedule-and-cancel-tasks-with-asyncio)
  demonstrates that delayed work needs an owned task handle.
- A [2020 pending-task destruction report](https://stackoverflow.com/questions/63585835/cancelled-asyncio-tasks-result-in-task-was-destroyed-but-it-is-pending)
  documents the shutdown warnings caused by abandoning cancellation without a
  loop turn.
- A [2021 cancellation report](https://stackoverflow.com/questions/68759284/how-to-make-asyncio-cancel-to-actually-cancel-the-task)
  explains that `Task.cancel()` requests cancellation and delivery occurs when
  the event loop next runs the task.
- Python's long-running [clock-resolution issue](https://bugs.python.org/issue31539)
  shows that event-loop timers inherit platform monotonic-clock resolution;
  deterministic tests must assert logical deadlines rather than sub-resolution
  elapsed-time accidents.

## Security, Resources, and Observability

Constructor validation fails closed before mutating state or creating tasks.
Negative, non-finite, and edge-less configurations cannot silently retain work.
Arguments remain process-local and callbacks execute with the caller's existing
authority; the primitives add no serialization, network, filesystem, or command
boundary.

Each instance owns at most one replaceable trailing timer and one latest
argument record. Async leading tasks are retained only while live and are
discarded on completion, bounding retained state to callbacks actually admitted
by the leading-edge policy. Synchronous callback errors propagate immediately;
async callback errors remain visible through normal event-loop task reporting.
No retry loop, thread, or hidden polling process is introduced.

## Zero-Downtime Delivery and Rollback

The state is in-memory and has no schema or persisted representation. Deploy
new workers beside old workers, shift traffic only after the strict focused
suite and coverage gates pass, and drain old workers so their already-admitted
timers reach completion or normal shutdown cancellation. Mixed versions do not
share debounce state.

Rollback sends new traffic to the previous immutable worker version and drains
the repaired workers. No data reversal or migration is required. Rollback must
not restore the test-local prototypes; the production API remains the single
source of timing semantics.

## Verification

The focused suite pins leading, trailing, both-edge, max-wait, pending, reset,
zero-epoch, cancellation, non-finite input, and multi-instance isolation. The
adjacent timing suite pins idempotent watchdog generations and immutable
verdicts. Tests run with warnings promoted to errors, and touched production
files must meet the repository's line-and-branch coverage floors.
