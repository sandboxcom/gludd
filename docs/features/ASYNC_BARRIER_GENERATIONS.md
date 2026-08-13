# Async Barrier Generation Contract

## Scope

Gludd's `Barrier` coordinates a fixed number of coroutines across repeated
phases, while `WaitGroup` releases callers after a manual count reaches zero.
This contract covers generation isolation, abort, timeout, cancellation, reset,
and the project's existing synchronous control methods. It does not make either
primitive thread-safe; all state transitions belong to one asyncio event loop.

Python's maintained `asyncio.Barrier` is the lifecycle reference: a successful
generation moves from filling to draining and does not refill until every
participant has left. Its `abort()` and `reset()` methods are asynchronous,
however, while Gludd's established public API and callers require those controls
to take effect synchronously. The implementation therefore uses standard
`asyncio.Event` primitives and mirrors the maintained filling/draining model
without adding another coordination library:
[Python asyncio synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Barrier).

## Generation and failure contract

Each generation owns a distinct release event. The final arriving party starts
draining and wakes that generation; a returning party that immediately calls
`wait()` again remains behind the drain boundary until every peer has returned.
The last departing party rotates the event and advances the generation.

Abort is immediate and idempotent. Active and future waiters receive
`BarrierAborted` until an explicit reset. A deadline raises
`BarrierTimeout` for the caller that owns it and breaks the barrier so new
callers fail closed with `BarrierBroken`. Cancellation is never swallowed: the
cancelled task receives `CancelledError`, while the shared barrier becomes
broken so peers cannot hang indefinitely. Reset is rejected while admitted
waiters remain and otherwise creates a fresh event without reusing a signalled
object.

`WaitGroup.wait()` preserves the same public timeout type instead of exposing
the implementation-level built-in `TimeoutError`.

## Security, observability, and resource safety

A barrier is an availability boundary. Silent generation reuse, lost wakeups,
or exception translation can strand request handlers and consume worker slots
without progress. Callers should record the primitive name, generation, party
count, waiter count, transition (release, abort, timeout, reset), and elapsed
wait time. Logs must not include task payloads, credentials, or coroutine local
state.

No background task, thread, polling loop, or process is created. Event objects
are rotated only after a generation drains, and references from completed
generations are released. Deployment health should alert on timeouts, aborts,
broken-state rejections, and wait duration near the configured deadline.

## Zero-downtime rollout and rollback

The public constructor, properties, synchronous controls, context-manager
interface, exception classes, and zero-party behavior remain compatible. Roll
out to one worker first, exercise two consecutive generations plus abort/reset,
then expand while monitoring broken-state and wait-duration metrics. Existing
in-flight calls finish inside their loaded worker process, so mixed versions do
not share barrier state.

Rollback replaces workers with the previous build using the same drain-first
sequence. There is no persisted schema or wire-format migration. A broken
in-memory barrier must be reset or its owning worker drained before either
forward or rollback traffic is admitted.

## Practitioner evidence

A long-lived CPython discussion reports that cancellation-based timeouts require
an explicit cancellation-safety contract; users otherwise cannot know whether a
partially completed operation lost state:
[CPython issue #92824](https://github.com/python/cpython/issues/92824).
Gludd makes the cancellation point and state transition explicit, then tests the
broken-state boundary.

A separate practitioner report traced an asyncio shutdown hang to waiters that
were not woken after an exceptional path:
[CPython issue #105288](https://github.com/python/cpython/issues/105288).
That failure mode directly motivates waking both the current-generation event
and the drain gate whenever this barrier breaks.

## Verification

The focused suite covers two-, three-, four-, five-, and twenty-party barriers,
two and ten consecutive generations, active/future abort, default and per-call
timeouts, reset recovery, context-manager use, zero-party behavior, concurrent
waiters, and WaitGroup countdown. It passes 29 tests under strict warnings with
92.18% branch coverage for
`src/general_ludd/coordination/barrier.py`.
