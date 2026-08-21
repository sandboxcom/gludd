# Safe cache protocol binding

Status: implemented in the safe disk-cache adapter and behavior-tested. Release
evidence is tracked in `TASKS.md` as S83.54.

## Problem

`SafeCache` described Python operator and context-manager slots as class-level
`Callable` attributes. Type checkers bind callable class attributes as methods,
so zero-argument annotations for `__iter__` and `__enter__` lost the implicit
instance argument. Every consumer using iteration or `with` then failed the
repository-wide type boundary even though the runtime adapter behaved correctly.

The ambiguity is especially risky at this security boundary: weakening the
protocol to `Any` would hide both cache API drift and resource-lifecycle errors.

## Behavioral contract

1. Iteration, containment, item access, mutation, entry, and exit are declared as
   bound protocol methods with an explicit `self` parameter.
2. `__enter__` returns the same structural cache protocol.
3. `__exit__` accepts the standard exception type, value, and traceback tuple
   and does not suppress exceptions.
4. Runtime cache construction, safe MessagePack serialization, legacy-pickle
   rejection, and owner-only directory permissions are unchanged.
5. The protocol remains structural; consumers do not depend on a concrete
   diskcache implementation.

## Zero-downtime, security, and resource boundary

This is a static contract repair. It changes no cache files, wire formats, ports,
database schema, daemon, deployment, or persistent service state. Existing
processes do not restart, so rollout and rollback are zero-downtime source
changes.

The adapter continues to reject pickle deserialization, extension values,
file-like storage, and non-bytes namespaces. Context-manager cleanup remains
explicitly typed, preventing callers from silently losing the close boundary.
The change adds no process, thread, socket, cache entry, or disk allocation.

### Local-memory acquisition lifecycle (2026-08-20)

`LocalAgentMemory` creates its owner-only directory at construction but opens
the DiskCache/SQLite resource only when the first data operation needs it.
Pure key derivation therefore owns no database connection. `close()` closes an
opened cache, clears the owned reference, and is idempotent both before first
use and after shutdown. A later data operation may deliberately acquire a new
cache instance, preserving the existing reusable-store behavior.

This changes neither cache keys nor the MessagePack namespace, so old and new
workers can overlap during a rolling deployment without a schema or file-format
migration. Rollback restores eager acquisition; operators need not drain
traffic, but should close stores before replacing a worker. The lazy boundary
adds no task, thread, process, socket, or temporary artifact, and avoids opening
SQLite for metadata-only work.

## Practitioner evidence

A Stack Overflow user reported the same `Callable` class-attribute binding
diagnostic in 2022 and documented a callback `Protocol` method as the durable
workaround:

- [Stack Overflow: type hint an instance-level function](https://stackoverflow.com/questions/70974078/how-to-type-hint-an-instance-level-function-i-e-not-a-method)

The linked mypy issue was opened in 2015 and records the long-lived descriptor
ambiguity: a class-level `Callable` is treated as a bound method and its first
argument is consumed. Declaring actual protocol methods makes binding explicit:

- [mypy issue #708: Cannot assign to field of Callable type](https://github.com/python/mypy/issues/708)

CPython's maintained typing implementation identifies `__enter__` returning
the current instance as a primary use of a self-typed method:

- [CPython typing implementation](https://github.com/python/cpython/blob/main/Lib/typing.py)

On 2023-06-08, CPython maintainers opened
[issue #105539](https://github.com/python/cpython/issues/105539) after
practitioners observed that unclosed connections were invisible. Python 3.13
then made missing explicit SQLite closure a `ResourceWarning`, as recorded in
the [upstream release notes](https://docs.python.org/3/whatsnew/3.13.html#sqlite3).
Gludd treats that warning as lifecycle evidence and prevents unnecessary
acquisition instead of suppressing finalizer diagnostics.

## Verification

- `tests/unit/test_safe_diskcache.py` structurally asserts that all six slots
  are real protocol methods and exercises serialization and permission safety.
- `tests/unit/test_cache_strategy_deep.py` proves key-only local-memory work and
  close-before-open do not acquire SQLite, while the cumulative file remains
  warning-clean on Python 3.14.
- The focused suite runs under strict warnings and covers the changed source at
  more than the repository's 85 percent aggregate and 75 percent file floors.
- Repository-wide mypy validates every cache consumer, including local memory,
  Searx retrieval, and web retrieval.
- The full release gate remains authoritative for promotion.
