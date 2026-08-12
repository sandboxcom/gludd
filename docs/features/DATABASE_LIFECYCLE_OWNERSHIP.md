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

## Operator and upstream evidence

- [SQLAlchemy discussion #12152](https://github.com/sqlalchemy/sqlalchemy/discussions/12152)
  records an ASGI operator hitting cross-loop connection failures. A SQLAlchemy
  maintainer recommends creating and releasing database resources in the ASGI
  lifespan, with a separate engine per event loop.
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

## Zero-downtime behavior

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
