# Database lifecycle ownership

Gludd creates SQLAlchemy, `aiosqlite`, and `diskcache` resources inside the
FastAPI lifespan. The same lifespan owns their shutdown. This avoids depending
on cyclic garbage collection, keeps repeated in-process daemon boots clean, and
prevents a retiring Gunicorn worker from carrying file descriptors past its
drain boundary.

## Shutdown contract

The daemon shuts resources down in dependency order:

1. stop event production and drain the EventLoop's tracked background tasks;
2. stop the execution engine and close HTTP/cache clients;
3. close every daemon-owned `diskcache` handle;
4. stop the writer process and close the long-lived embedding session; and
5. dispose the SQLAlchemy async engine.

Failures are logged with the resource name so teardown remains observable.
Each application instance closes only resources it created; it does not use a
process-global session closer that could disrupt another in-process app or
Gunicorn worker.

## Tick ownership contract

Only one `EventLoop.tick()` may run on an `EventLoop` instance at a time. The
daemon's `run_forever()` task and an administrative/manual tick share an async
lock. This keeps tick-scoped repository references and their `AsyncSession`
owners together until commit and close complete. A second caller waits for the
first complete tick; it never shares or replaces the active session.

## Hosted Python 3.11 connection incident (2026-08-26)

GitHub Actions run `32935772799`, job `98077630448`, completed 995 tests in the
`other` shard before garbage collection exposed one live `aiosqlite.Connection`.
The acquisition owner was the daemon's `EventLoop.run_forever()` task: teardown
requested a background-task drain while that producer could still be inside a
tick and acquire another `AsyncSession`. Disposing the engine later cannot
close a connection that remains checked out.

The corrected dependency order is mechanical: request the producer's
cooperative stop, await it for at most five seconds, cancel and await it within
the same bound if it stalls, and only then drain its tracked child tasks. Engine
disposal remains after both boundaries. The shared async-lifecycle helper also
observes a producer's terminal exception so failed tasks cannot bypass the
dependent drain. Tests cover graceful completion, failure, bounded
cancellation, and real driver closure through the daemon lifespan.

This matches the official
[SQLAlchemy asyncio disposal guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#synopsis-core):
async engines must be disposed in an awaitable context, and checked-out
connections are not closed by `AsyncEngine.dispose()`. The upstream
[aiosqlite 0.22.1 changelog](https://github.com/omnilib/aiosqlite/blob/main/CHANGELOG.md#v0221)
also makes the signal intentional: a connection collected without `close()` or
`stop()` emits `ResourceWarning`. The long-lived practitioner report in
[aiosqlite issue #259](https://github.com/omnilib/aiosqlite/issues/259), open
since 2023, documents the same connection-survival failure under cancelled
async work. These sources were rechecked on 2026-08-26.

For ZDD, the retiring worker stops accepting event-loop work while a replacement
worker becomes ready independently; no schema, database file, or durable cache
is mutated by this ordering change. Resource use stays bounded to the existing
producer task and one five-second grace window—no retry task, process, thread,
or extra connection is created. Rollback is a code-only revert of the helper
call followed by a normal rolling worker replacement; there is no data or
artifact rollback step.

## Operator and upstream evidence

- [SQLAlchemy discussion #12152](https://github.com/sqlalchemy/sqlalchemy/discussions/12152)
  records an ASGI operator hitting cross-loop connection failures. A SQLAlchemy
  maintainer recommends creating and releasing database resources in the ASGI
  lifespan, with a separate engine per event loop.
- [SQLAlchemy issue #9973](https://github.com/sqlalchemy/sqlalchemy/issues/9973)
  reproduces the same `IllegalStateChangeError` raised when `close()` overlaps
  `_connection_for_bind()` and records the maintainer guidance that a Session
  is stateful and unsafe for concurrent asyncio calls. Gludd serializes whole
  ticks because their repositories are tick-scoped instance state.
- [SQLAlchemy discussion #10857](https://github.com/sqlalchemy/sqlalchemy/discussions/10857)
  documents async test teardown with `await engine.dispose()` and discusses
  xdist workers starting with independent, empty engine pools.
- [SQLAlchemy discussion #5903](https://github.com/sqlalchemy/sqlalchemy/discussions/5903)
  records a long-lived practitioner report that conflict-update upserts skip
  Python-side `Column.onupdate` values. The maintainer-confirmed workaround is
  why Gludd supplies `updated_at` explicitly in each conflict-update set.
- [SQLAlchemy discussion #11791](https://github.com/sqlalchemy/sqlalchemy/discussions/11791)
  traces an apparently stale timestamp to the ORM identity map returning the
  already-loaded value. Gludd refreshes the row returned from deployment upsert
  before exposing its timestamp and revision to callers.
- [aiosqlite issue #259](https://github.com/omnilib/aiosqlite/issues/259), open
  since 2023, tracks connections that survive cancelled async work. This is why
  Gludd drains tracked tasks before disposing the engine instead of assuming
  cancellation synchronously releases a connection.
- [CPython 3.13 release notes](https://github.com/python/cpython/blob/main/Doc/whatsnew/3.13.rst)
  explain that an unclosed `sqlite3.Connection` now emits `ResourceWarning`.
  Gludd's lifecycle regression forces collection and treats that signal as a
  functional leak, rather than filtering the warning.
- CPython's long-running
  [sqlite adapter issue #90016](https://github.com/python/cpython/issues/90016)
  and maintained [SQLite documentation](https://docs.python.org/3/library/sqlite3.html#default-adapters-and-converters-deprecated)
  were reviewed on 2026-08-20. They document that implicit date and datetime
  adapters are deprecated because applications need an explicit representation.
  Raw SQLite fixture updates therefore bind UTC timestamps through SQLAlchemy's
  timezone-aware `DateTime` type instead of invoking the process-global adapter.

## Migration planning and retention integrity

`plan_migration()` is a read-only preflight. It reports the current revision,
head revision, and pending count without applying DDL. For dialects whose
migration history supports Alembic offline mode, it also renders SQL. Gludd's
SQLite history contains move-and-copy batch operations that require live table
reflection, so an SQLite plan instead returns an explicit diagnostic SQL
comment. Operators must validate that history against a disposable copy; the
planner never runs it against the live database merely to manufacture a
preview. Unknown Alembic errors still propagate.

[Alembic's batch-mode documentation](https://alembic.sqlalchemy.org/en/latest/batch.html#working-in-offline-mode)
states that SQLite batch migrations cannot use `--sql` without supplying full
`copy_from` tables. A long-lived
[Alembic practitioner discussion #1069](https://github.com/sqlalchemy/alembic/discussions/1069)
also records why projects commonly use current-schema SQLite fixtures plus
targeted migration-walk tests instead of treating one rollback-based fixture as
migration proof. Gludd therefore keeps model introspection, revision-chain, and
migration execution checks separate.

Task-decision retention tests keep SQLite foreign keys enabled and seed the
corresponding task-return parents. They do not disable or defer integrity to
make cleanup pass. This follows the failure mode discussed in
[SQLAlchemy discussion #6123](https://github.com/sqlalchemy/sqlalchemy/discussions/6123):
connection-scoped SQLite pragmas and deferred checks do not make invalid parent
references valid. Retention deletes remain caller-committed, matching the
repository transaction boundary.

## Zero-downtime behavior

Migration 042 adds only the `todos.parent_todo_id` lookup index. Existing and
replacement workers use the same columns and constraints before, during, and
after that migration; rollback removes only the performance index. The
candidate preflight checks the revision chain before traffic switches.

Lifecycle cleanup happens only after the worker has stopped accepting work and
its event bridges have drained. During a rolling or blue/green deployment, the
replacement worker can become ready while the old worker completes this local
cleanup. Cache directories remain durable; closing a handle never deletes
cached data, so another worker can continue serving without a cache rebuild or
schema transition.

The regression boots and stops multiple daemon applications against isolated
SQLite databases, forces cyclic collection, and fails on any `unclosed
database` warning. This makes clean worker replacement an executable ZDD
invariant.
