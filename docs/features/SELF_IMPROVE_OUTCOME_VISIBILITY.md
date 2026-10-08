# Self-improvement outcome visibility

## Contract

`GET /admin/self-improve/status` retains its existing `status`,
`findings_count`, findings, and enqueue fields and adds `outcome_analysis`.
That field is computed from the shared Ornith training-pair database rather than
from one worker's daemon dictionary. A worker with empty local state therefore
reports `completed` when a different worker has persisted a rejected outcome.

The read has fixed limits: one `AsyncSession`, one query, at most 100 rows from
the last seven days, and at most 32 analyzer groups. The response includes only
counts, bounded model and scaffold-kind identifiers, numeric aggregates, and
generated suggestions. It never returns instructions, scaffold content, target
paths, outcome details, or exception text. A missing session factory and a
failed query produce explicit, redacted `unavailable` states; they do not turn
an otherwise healthy status request into an unhandled error.

The event-loop analysis keeps the same collector and `OutcomeAnalyzer` path but
now retains its bounded aggregate in daemon state instead of discarding the
suggestions after logging their count. The database remains authoritative for
the HTTP endpoint.

## Multi-worker and session findings

FastAPI documents that worker processes do not share variables or memory, so a
process-local status dictionary cannot be a cross-worker source of truth
([deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/#memory-per-process)).
The long-lived user report in
[FastAPI issue #592](https://github.com/fastapi/fastapi/issues/592) demonstrates
the same symptom with a counter written in one request and read as its initial
value by another. Persisted training pairs avoid that worker-affinity failure.

Session lifetime is deliberately lexical. The endpoint obtains a fresh
`AsyncSession` from the daemon's existing factory and closes it when the single
read completes. SQLAlchemy recommends passing `AsyncSession` directly for new
async code instead of relying on mutable `async_scoped_session` state, and
warns that failing to remove a task-scoped session retains both the task and
session in memory
([SQLAlchemy asyncio scoped sessions](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncio-scoped-session)).
A multi-year FastAPI community discussion records production reports of stale
scoped sessions, steadily increasing memory, and exhausted connection pools
when async request cleanup is ordered incorrectly
([FastAPI discussion #8017](https://github.com/fastapi/fastapi/discussions/8017)).
The local context manager avoids that registry and cleanup-order class of
failure.

## ZDD, resources, and rollback

This is an additive response-field change with no migration, write, cache,
Redis dependency, background task, process, thread, or external call. Old and
replacement workers can serve concurrently during a rolling deployment because
both read the existing table and older clients can ignore `outcome_analysis`.
The fixed row and group limits bound database work, Python memory, response
size, and per-worker amplification. The read-only session is released before
the response is serialized.

Rollback is code-only: revert the route/module integration and roll workers
normally. No schema, row, artifact, or cache rollback is needed, so the prior
status representation remains available throughout a zero-downtime rollout.

## Verification

`tests/unit/test_self_improve_outcome_visibility.py` proves cross-worker-style
database visibility with empty daemon state, one bounded SQL query, seven-day
filtering, 32-group truncation, redaction, existing-key compatibility, and the
explicit unavailable state. The existing daemon endpoint integration test pins
the additive field on the production registration path.
