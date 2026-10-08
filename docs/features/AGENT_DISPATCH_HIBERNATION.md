# Agent Dispatch Hibernation

Status: S23 is implemented with focused evidence; exact-head full-gate and
release evidence remain pending.

## Runtime contract

An agent that calls `AgentDispatcher.dispatch_many()` is active as a control
task but idle while its children execute. Its suspended coroutine frame retains
the parent `AgentTask`, including the conversation history. S23 connects the
existing hibernation store to that real wait boundary.

`dispatch_one()` publishes the executing task through one task-local
`ContextVar`. A nested batch is eligible only when every child names that exact
task ID as its common `parent_task_id`. `dispatch_many()` then creates one
Pydantic `AgentEnvironmentSnapshot` for the batch and enters the existing
`HibernationController` async context manager before scheduling the children.
After authenticated JSON is safely on disk, the parent message list is cleared.
The context manager also drops its final strong reference to the serialized
snapshot before yielding, so the object graph is genuinely collectible while
children run. On context exit, state is restored before control returns to the
parent.

The policy is deliberately narrow:

| Condition | Result |
|---|---|
| Controller absent | Dispatch unchanged; parent remains resident |
| Parent depth below 3 | Dispatch unchanged |
| Parent has fewer than 8 messages | Dispatch unchanged |
| Child batch has no single matching parent | Dispatch unchanged |
| Snapshot JSON exceeds 16 MiB | Dispatch unchanged; no file is published |
| Eligible common-parent batch | Exactly one snapshot is parked for the batch |

Success, child error, the existing 30-minute batch timeout, and caller
cancellation all leave the async context through `finally`. Rehydration and
discard therefore complete before the parent resumes or cancellation
propagates. The dispatcher does not alter child ordering, result conversion,
semaphore limits, or the timeout budget.

## Python task and context guidance

Python documents that an `asyncio.Task` copies the current context when it is
created, and that cancellation cleanup belongs in `try/finally` before
`CancelledError` propagates. Those contracts make a `ContextVar` preferable to
a process-global parent map: sibling tasks receive isolated context copies,
while the token reset in `dispatch_one()` prevents a completed child or parent
from contaminating later dispatches. See the maintained
[`asyncio` task documentation](https://docs.python.org/3/library/asyncio-task.html#creating-tasks)
and [`contextvars` asyncio guidance](https://docs.python.org/3/library/contextvars.html#asyncio-support).

The Python asyncio conceptual guide also explains that a coroutine's local
variables remain scoped to that coroutine until it exits. A suspended parent is
cheap in CPU terms, but its local object graph is still live. S23 removes only
the dominant conversation payload; it does not pretend to migrate the Task,
coroutine frame, semaphore ownership, or event loop.

## Practitioner evidence

This is preventive resource control, not a claim that CPython itself leaks the
parent context. Two long-lived practitioner reports informed the boundary:

- CPython issue
  [#85865](https://github.com/python/cpython/issues/85865), opened in 2020,
  records unexpectedly retained resident memory in long-running asyncio
  executor workloads. Its distinct executor lifecycle reinforces that awaiting
  work is not, by itself, proof that resident memory has returned.
- aiohttp discussion
  [#5993](https://github.com/aio-libs/aiohttp/discussions/5993), opened in 2021,
  reports memory growth in a long-lived application around concurrent
  `asyncio.gather()` request batches, especially after network failures. The
  report remains a useful operator warning: concurrent wait paths need explicit
  ownership and terminal cleanup on failure as well as success.

S23 addresses the repository's known ownership case directly: the parent
conversation is intentionally live but not needed until the child batch
finishes. It neither diagnoses nor masks unrelated allocator, executor, HTTP,
or third-party retention.

## Security and bounded resources

The feature reuses `HibernationStore`: strict Pydantic models, JSON rather than
pickle, keyed HMAC-SHA256 with constant-time verification, an owner-only path
jail, owner-only files, and atomic replacement. The parent is cleared only
after the bounded write succeeds. Hydration verifies both the envelope MAC and
the trusted in-memory handle before state is accepted.

Serialization, hydration, and deletion run through `asyncio.to_thread()` so
filesystem work does not block the event loop. At most one snapshot is created
per eligible batch, payload admission is capped at 16 MiB, and the existing
depth/message thresholds avoid churn for shallow or light parents. The change
adds no package, schema, subprocess, listener, queue, retry, daemon, or dedicated
worker. It uses the process's existing event loop, default executor, dispatcher
semaphores, and hibernation directory.

## Zero-downtime rollout and rollback

The change is additive and wire/schema neutral. Updated and older workers can
coexist: only an updated worker that owns both parent and child batch performs
the local park. Snapshot files are process-local implementation state and never
authorize dispatch or cross a service boundary. A failed eligibility check is a
resident dispatch, not a partial migration.

For rollback, first stop admitting new nested batches to each worker, drain its
active batches through success, error, timeout, or cancellation so every parked
parent rehydrates and every snapshot is discarded, then replace that worker
with the prior revision. No traffic outage, database migration, file conversion,
or destructive cleanup is required. Residue is never treated as resumable work.

## Verification

`tests/unit/test_agent_dispatch_hibernation.py` pins one depth-three parent with
eight messages parking exactly once while two children block. It compares the
pre/post serialized parent state and requires zero snapshot residue after
success, child failure, timeout, and cancellation. A weak-reference assertion
proves that the in-memory snapshot object graph is released during the wait.
It also pins absent
controller, shallow/light parent, mixed-parent, oversized-payload bypasses, and
off-event-loop snapshot I/O. Boundary cases also reject non-positive limits,
retain malformed parent messages in memory, and preserve the existing token
revival hook after rehydration. Branch-aware coverage is configured in
`config/coverage_agent_dispatch_hibernation.ini`; exact-head gate evidence is
required before S23 can move from `in_progress` to `completed`.
