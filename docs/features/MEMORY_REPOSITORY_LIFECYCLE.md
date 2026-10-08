# Memory repository lifecycle

S33 makes factory-owned memory results safe after transaction commit while
preserving caller-owned transaction and identity-map behavior. It changes no
database schema, dependency, API field, TTL rule, or list bound.

## Problem and acceptance contract

`MemoryRepository` supports either an injected `AsyncSession` or an
`async_sessionmaker`. The factory path already opened a fresh transaction for
each call, but returned ORM rows only after that transaction had committed and
the session had closed. SQLAlchemy's default `expire_on_commit=True` therefore
expired every loaded column before the caller received the detached object.
Accessing even `key` or `value` could raise `DetachedInstanceError`. Existing
tests configured `expire_on_commit=False`, so they did not exercise the default
lifecycle.

The corrected ownership contract is:

- each factory-owned call creates one session and transaction, commits on
  success, rolls back on failure, and closes before returning;
- `set`, `get`, and `list_by_namespace` fully load and expunge only their
  factory-owned returned rows before commit, so ordinary column access performs
  no implicit I/O after return;
- an injected session remains caller-owned: returned rows stay attached and
  the repository never commits, rolls back, closes, or expunges them;
- lazy TTL deletion still commits on a successful factory-owned `get`;
- list results retain their configured bound and namespace/project filters; and
- a failed operation leaves no partial write and returns its connection to the
  existing engine pool.

## Maintained dependency and practitioner evidence

The implementation uses SQLAlchemy's existing sessionmaker, transaction, and
`expunge()` behavior. It does not add a custom pool, object copier, lock, queue,
or persistence layer. SQLAlchemy's official
[async concurrency guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
requires a separate `AsyncSession` per concurrent task, and its
[session FAQ](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks)
describes the session-per-task model and local context-manager lifetime.

Long-lived user reports show both sides of this boundary:

- [SQLAlchemy discussion #8731](https://github.com/sqlalchemy/sqlalchemy/discussions/8731)
  records a 2022 `DetachedInstanceError` after returning an ORM object from a
  sessionmaker context; the maintainer explains that context exit closes the
  session and leaves the returned object detached.
- [SQLAlchemy discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  records concurrent async operations failing when they share one injected
  session; the recommended boundary is one session at the top of each task.
- [SQLAlchemy discussion #10808](https://github.com/sqlalchemy/sqlalchemy/discussions/10808)
  reiterates that sharing a session also shares a stateful connection and that
  concurrent tasks need distinct sessions.

Those reports support keeping factory-owned operations isolated while leaving
injected unit-of-work sessions attached. Detaching every result regardless of
ownership would repair one path by breaking the other.

## Resources, failure handling, and observability

Each factory-owned method uses at most one session, one transaction, and one
checked-out connection. Concurrency remains bounded by the configured engine
pool; S33 creates no process, thread, listener, timer, file, queue, daemon, or
background task. The acceptance suite uses a four-connection pool, runs four
repository calls concurrently, and observes zero checked-out connections after
completion. Returned rows are bounded by the existing list limit.

The transaction context is the failure boundary. An exception after `flush()`
rolls the pending write back and closes the session. Successful lazy expiration
deletes the stale row in the same transaction. Tests assert both database state
and pool state, rather than relying only on the absence of warnings.

## Zero-downtime rollout and rollback

S33 is a code-only rolling change. Old and new processes can use the same table
and rows because serialization, columns, constraints, indexes, TTL calculation,
and query predicates are unchanged. Route new traffic to the updated process,
let in-flight calls finish, and monitor database errors plus pool saturation.

Rollback is an application-image rollback with no migration or data rewrite.
Route new work back to the preceding image and let updated in-flight calls
finish. Rows committed by the updated code are ordinary memory rows readable by
the preceding code. Caller-owned transactions keep identical commit/rollback
control in both versions, so rollback needs no drain, compatibility flag, or
state conversion.

## Verification

- `make test-specific` for
  `tests/integration/test_memory_repository_factory_lifecycle.py` with warnings
  fatal
- `make test-files` for the existing memory unit, router, and G1 integration
  compatibility suites with warnings fatal
- `make coverage-files` with
  `config/coverage_memory_repository_lifecycle.ini`, aggregate minimum 85, and
  per-file minimum 75
- `make lint-files`, `make typecheck-scope`, `make lint-markdown`,
  `make check-task-integrity`, `make check-resource-ownership`, and
  `make test-count`
