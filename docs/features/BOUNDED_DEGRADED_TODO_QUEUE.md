# Bounded degraded-mode todo queue

Gludd normally persists work items through its database repository.  When no
session factory is available, the todo router keeps a compatibility queue in
daemon state.  That fallback must remain bounded in a long-lived Gunicorn
worker: on its first read or write, the router converts the factory's plain
startup list to `collections.deque(maxlen=1000)`.  Appending beyond the limit
evicts the oldest item and retains the most recent work.

The conversion is deliberately lazy.  `create_daemon_app()` retains its stable
startup-state contract (`todos == []`), while any operation that can consume or
grow the fallback first installs the bound.  Supplying an already bounded deque
is idempotent and preserves the existing object.

## Upstream operational findings

- The [Python `collections.deque` documentation](https://docs.python.org/3/library/collections.html#collections.deque)
  states that omitting `maxlen` permits arbitrary growth, while a bounded deque
  discards items from the opposite end and is intended for recent-activity
  pools.  This is the direct basis for the Gludd bound and FIFO eviction tests.
- Long-running queue discussions also show why the bound must be explicit and
  locally testable.  [CPython issue #119534](https://github.com/python/cpython/issues/119534)
  documents a platform limit that made an oversized `multiprocessing.Queue`
  fail at construction.  Gludd's in-process compatibility queue does not use a
  semaphore-backed multiprocessing queue; its fixed application-level cap is
  therefore portable and predictable.
- [CPython issue #92824](https://github.com/python/cpython/issues/92824) records
  maintainers' guidance that stronger delivery guarantees require a persistent
  queue.  Gludd follows that separation: the database is authoritative, and the
  bounded deque is explicitly a degraded-mode compatibility path.

## Verification

`tests/e2e/test_cli_daemon_lifecycle_workflows.py` pins the plain startup state.
`tests/unit/test_todos_router_bounds.py` pins lazy conversion, the 1,000-item
ceiling, FIFO eviction, preserved seeded items, and read-by-id behavior.
