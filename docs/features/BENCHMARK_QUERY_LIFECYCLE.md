# Benchmark query lifecycle

S32 gives each factory-backed benchmark operation a session lifecycle that
matches the operation it performs. Writes retain an explicit transaction;
reads no longer commit and expire ORM rows immediately before returning them.
The benchmark schema, router payloads, query bounds, and caller-owned
transaction contract are unchanged.

## Problem and acceptance contract

`BenchmarkRepository` is shared by the daemon and several router paths through
an `async_sessionmaker`. Its common execution helper previously opened
`session.begin()` for every factory operation. That transaction is correct for
`record_result()`, which needs commit-or-rollback durability. It is incorrect
for read-only `get_model_scores()` and `list_recent()`: context exit commits the
read transaction, default `expire_on_commit=True` expires every returned ORM
row, and closing the session leaves callers with detached, unreadable objects.

The corrected contract is:

- factory-owned writes use an operation-scoped session and `session.begin()`,
  commit on success, roll back on failure, and close before returning;
- factory-owned reads use a fresh operation-scoped session and release its
  implicit read transaction by closing without a commit;
- concurrent factory reads never share mutable session or transaction state;
- an explicitly supplied session remains caller-owned and is neither committed
  nor closed by the repository;
- `get_model_scores()` retains the configured list cap, while `list_recent()`
  still clamps its caller limit to that cap; and
- query ordering, filtering, aggregate shapes, and routing scores do not change.

The lifecycle acceptance uses the sessionmaker's default expiration policy,
persists four benchmark rows, and runs all four factory query surfaces
concurrently. It accesses returned model attributes only after every query
context has closed, verifies durable rows independently, and proves the pool
has no checked-out connection. Separate acceptance paths prove failed writes
roll back without poisoning the next operation and caller-owned work remains
rollbackable by its caller.

## Mature dependency and practitioner evidence

S32 uses SQLAlchemy's existing lifecycle primitives rather than adding a lock,
session registry, query cache, or background database worker. SQLAlchemy's
[async-session concurrency guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
requires a separate `AsyncSession` per concurrent task because one session is
mutable transaction state. Its
[`AsyncSession.begin()` documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession.begin)
defines the transaction context used only for the write path; the surrounding
sessionmaker context guarantees closure.

The long-lived upstream
[SQLAlchemy discussion #8731](https://github.com/sqlalchemy/sqlalchemy/discussions/8731)
records the same detached-result failure: a sessionmaker transaction context
commits, closes, and returns a detached ORM instance. Maintainer guidance
separates transaction ownership from data operations and recommends passing a
caller session when attached state is required. Gludd follows that guidance:
caller-supplied sessions stay attached and caller-owned, while factory reads
return already-loaded scalar rows without an unnecessary commit-expiration
boundary.

## Resources, observability, and failure behavior

Each factory operation owns at most one session and one pooled connection. The
engine pool remains the concurrency bound. S32 creates no process, thread,
listener, timer, queue, cache, file, or detached asyncio task. Read cardinality
does not increase, and the existing dynamic repository cap remains enforced.

Read failures retain their existing visible exception path while the context
releases its session and connection. A failed factory write rolls back and
closes before the exception propagates, so later operations receive clean
state. Caller-owned failures deliberately leave rollback, commit, and closure
to the caller.

## Zero-downtime rollout and rollback

No migration, data rewrite, configuration change, API version, or dependency
change is required. Old and new processes can run concurrently against the
same append-only benchmark table. The change affects only local transaction
boundaries; committed rows and response schemas remain mutually compatible.

Deploy the application image normally and observe database-pool saturation,
benchmark query failures, and adaptive-router errors. Rollback is source/image
only: drain in-flight requests from the S32 image and restore the preceding
image. Do not delete benchmark history, downgrade the database, or drain a new
resource; S32 introduces none.

## Verification

- `make test-specific TESTFILE=tests/integration/test_benchmark_query_lifecycle.py::test_default_expiration_concurrent_queries_release_factory_sessions PYTEST_ARGS='-q -W error'`
- `make test-files` over the S32 lifecycle, benchmark repository, routing,
  skill, role, and cap suites with `PYTEST_ARGS='-q -W error'`
- `make coverage-files` with
  `config/coverage_benchmark_query_lifecycle.ini`, aggregate minimum `85`, and
  per-file minimum `75`
- scoped `make lint-files`, `make typecheck-scope`, `make lint-docstrings`, and
  `make lint-markdown`
- `make check-resource-ownership` and `make test-count`
