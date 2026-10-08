# Durable Stall Escalation

Status: S18 is implemented with focused evidence; exact-head full-gate and
release evidence remain pending.

## Runtime contract

The daemon already owns one `StallWatchdog`, one `EventBus`, one asyncio event
loop, and one database session factory. S18 adds a subscriber to that existing
composition. The subscriber is active before the watchdog sweeper starts. A
watchdog report still emits `StallDetectedEvent` and `SlowOperationEvent`; the
stall event now also carries the watched operation identifier as its correlation
identifier.

The watchdog callback runs on the sweeper thread. Its EventBus subscriber copies
only bounded scalar evidence, then uses `loop.call_soon_threadsafe()` to schedule
one persistence task on the daemon's existing loop. The watchdog never waits for
the database. A session and `HumanTodoRepository` create an urgent, open
`blocker` owned by `stall-watchdog`. Shutdown stops the sweeper first,
unsubscribes the bridge, drains writes already accepted by the bridge, and only
then disposes the database engine.

Each event has at-most-once persistence semantics. A bounded in-memory FIFO
remembers the most recent 1,024 `stall:<event-id>` identities, and the same
identity is stored in `HumanTodo.session_id` and checked before insert. A replay
in the same daemon or after a restart therefore does not create another row.
The bridge never retries a failed write. It logs one bounded error and leaves the
watchdog operational, avoiding a hidden retry storm or duplicate escalation.

## Data and action boundary

Persisted evidence is deliberately small:

- title: at most 160 characters;
- body: at most 512 characters;
- operation and task identity: at most 96 sanitized characters each;
- elapsed and deadline values: finite, non-negative, capped at one year, and
  rendered to millisecond precision;
- tags: `agent-stall` and `operator-action`.

Captured thread stacks remain on the transient event for live diagnostics but
are never copied into the database. This avoids persisting source, prompts,
credentials, or other process memory that a stack snapshot could expose. The
bridge does not cancel the stalled operation, restart it, retry it, or alter its
state. It only creates an operator-visible blocker. The normal HumanTodo state
machine remains authoritative, so operators can mark an escalation done,
dismiss it with a reason, or supersede it. Existing rows are never recreated or
made non-dismissible by rollout or rollback.

The subscriber accepts at most 64 concurrent writes. At capacity it emits one
error for the new event and does not retry it. Completed identities and active
tasks are both bounded, and S18 creates no process, thread, listener, database
table, package, network request, or periodic daemon.

## Python cancellation and practitioner evidence

Python's maintained asyncio documentation requires cross-thread callbacks to use
[`loop.call_soon_threadsafe()`](https://docs.python.org/3/library/asyncio-dev.html#concurrency-and-multithreading)
and says cancellation is cooperative: `CancelledError` is delivered at the next
opportunity and normally must propagate after cleanup. S18 follows the
cross-thread scheduling rule and never initiates cancellation.

Long-lived CPython issue
[#87555](https://github.com/python/cpython/issues/87555) records an application
hang when `asyncio.wait_for()` waits on work that ignores cancellation. CPython
issue [#107505](https://github.com/python/cpython/issues/107505) records that
cancelling a `run_in_executor()` future does not stop its running thread and was
closed as not planned. A Python community discussion on
[Cancelling threads](https://discuss.python.org/t/cancelling-threads/105287)
likewise notes that forcibly aborting a thread cannot preserve process stability
and that safe cancellation requires cooperation from the work itself. These
practitioner reports are why the watchdog observes and escalates instead of
injecting an exception, wrapping the operation in another timeout, or pretending
that cancelling an asyncio facade terminated the underlying work.

## Failure observability

Scheduling, capacity, unexpected task cancellation, task failure, and database
persistence failure each have bounded error logs containing only the sanitized
stall identity and counts. Database exceptions are contained inside the
subscriber task and cannot escape into `StallWatchdog.poll()` or kill its sweep.
There is no silent fallback and no automatic second attempt.

## ZDD rollout and rollback

The change is additive and schema-free. Mixed old and new daemon workers remain
wire-compatible: old workers continue publishing transient stall events, while
new workers additionally create ordinary HumanTodo rows. Roll out normally
without stopping request traffic or migrating data. The subscriber uses the
existing database pool and asyncio loop, and startup adds no readiness
dependency; a later database failure degrades only that escalation attempt.

Rollback removes the subscriber wiring after a normal daemon drain. It does not
cancel active work or delete any HumanTodo. Already-persisted escalations remain
visible and dismissible through the existing API and CLI, which preserves the
operator record across a rolling rollback. No resource teardown beyond the
existing watchdog, task, and database lifecycle is required.

## Verification

`tests/unit/test_stall_escalation.py` covers cross-thread delivery, exact-once
replay, durable replay detection, bounds, stack exclusion, capacity, failure
containment, drain, and unsubscribe behavior. The former S18 no-subscriber
regression is inverted in `tests/unit/test_stub_closure_s12_s19.py`, and
`tests/integration/test_daemon_lifespan.py` proves that a synthetic daemon stall
creates exactly one durable blocker. Branch-aware focused coverage is configured
in `config/coverage_stall_escalation.ini`.
