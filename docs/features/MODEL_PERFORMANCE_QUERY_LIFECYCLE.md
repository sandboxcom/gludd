# Model-performance query lifecycle

S31 gives every factory-backed model-performance read or aggregate refresh an
operation-scoped SQLAlchemy session. It completes the lifecycle boundary begun
by S30 without changing telemetry schemas, HTTP responses, or caller-owned
transactions.

## Problem and acceptance contract

The daemon constructs one `ModelPerformanceRepository` from an
`async_sessionmaker` and shares it with the event loop, performance router, and
administrative endpoints. Before S31, the first factory-backed refresh or read
created an `AsyncSession`, stored it on that shared repository, and reused it
indefinitely. Concurrent requests could therefore operate on the same mutable
transaction state. A factory-backed refresh also flushed aggregate rows without
committing them, so another session could not observe the refresh.

The corrected contract is:

- an explicitly supplied method session or constructor session remains owned by
  its caller and is neither committed nor closed by the repository;
- a factory-backed read creates and closes one session for that operation;
- a factory-backed refresh uses one transaction that commits on success, rolls
  back on failure, and closes before returning;
- concurrent reads never share repository-cached session state; and
- existing query shapes, sorting, filters, and the 1,000-row recent-call cap
  remain unchanged.

The lifecycle acceptance seeds four durable model calls, refreshes aggregates,
runs seven query surfaces concurrently under warnings-as-errors, reads the
committed aggregates from an independent session, and verifies that neither a
repository session nor a checked-out connection remains afterward.

## Mature dependency and practitioner evidence

S31 uses the SQLAlchemy 2.x lifecycle primitives already locked by Gludd. The
[asyncio documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
states that concurrent asyncio tasks require separate `AsyncSession` instances
because a session represents mutable transaction state. SQLAlchemy's
[`async_sessionmaker`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.async_sessionmaker)
provides both the read context and `begin()` transaction context needed here;
the latter commits and closes on successful context exit.

This failure mode has persisted in real async applications. In the long-lived
[SQLAlchemy discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554),
operators report `asyncio.gather()` failures when service methods share an
injected session. The maintainer guidance is to create a session where each new
task begins, and the discussion specifically considers passing the sessionmaker
instead of sharing a session. That evidence favors factory-scoped contexts over
a Gludd-specific lock, scoped-session registry, or background database queue.

## Resources, observability, and failure behavior

Each factory-backed operation acquires at most one session and one pooled
connection. Concurrent operations remain bounded by the existing SQLAlchemy
engine pool; S31 creates no process, thread, listener, timer, queue, cache, file,
or detached asyncio task. Query cardinality and payload bounds do not increase.
The recent-call limit remains capped at 1,000 and aggregate queries retain their
existing database grouping.

Read failures and refresh failures retain their existing visible exception
paths. The context manager always releases factory-owned state. Transactional
refresh failures roll back instead of publishing partial aggregate rows.
Caller-owned failures leave commit, rollback, and closure to the caller, exactly
as before.

## Zero-downtime rollout and rollback

No migration, table rewrite, configuration flag, API version, or dependency
change is required. Old and new processes can run concurrently against the same
append-only call table and aggregate table. New refreshes publish complete
transactions that old processes can read, and existing in-flight caller-owned
transactions keep their prior ownership semantics.

Deploy the application image normally and observe database-pool saturation,
refresh failures, and endpoint error logs. Rollback is source/image-only: stop
routing new requests to the S31 image, allow in-flight operations to finish,
and restore the preceding image. Do not delete telemetry or aggregate rows,
downgrade the database, or drain a new resource; S31 introduces none.

## Verification

- `make test-specific TESTFILE=tests/integration/test_model_performance_query_lifecycle.py::test_factory_owned_refresh_and_concurrent_queries_commit_and_release_sessions PYTEST_ARGS='-W error'`
- `make test-files` over the S30/S31 lifecycle, repository, router, fit-loop,
  and API suites with `PYTEST_ARGS='-W error'`
- `make coverage-files` with
  `config/coverage_model_performance_query_lifecycle.ini`, aggregate minimum
  `85`, and per-file minimum `75`
- scoped `make lint-files`, `make typecheck-scope`, `make lint-docstrings`, and
  `make lint-markdown`
- `make check-resource-ownership` and `make test-count`
