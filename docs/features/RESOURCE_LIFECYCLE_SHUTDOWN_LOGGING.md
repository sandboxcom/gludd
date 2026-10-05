# Resource lifecycle shutdown logging

Gludd's cross-provider lifecycle manager owns cleanup for resources that remain
registered when a process receives a termination signal or runs Python's
`atexit` callbacks. Cleanup must stay observable even when shutdown happens
after a test runner, terminal adapter, or log-capture plugin has already closed
the stream attached to Python's logging handlers.

The normal path continues to use the module logger. At the late-shutdown
boundary, the manager inspects the logger hierarchy before emitting cleanup
records. If any stream handler is already closed, the boundary temporarily
routes every lifecycle warning and error through a handler backed by process
stderr file descriptor 2. This preserves the initial cleanup warning and any
destroy-owner error or exception without writing to a dead capture object. The
original handlers and propagation setting are restored after cleanup, so a
direct invocation during a live process cannot mutate later logging behavior.

An empty registry performs the cleanup check without emitting a misleading
warning. A registry with pending resources still emits the warning before
destroying them. The regression matrix closes an isolated capture stream,
keeps an owned resource pending, and proves both that destruction occurs and
that the warning reaches the file-descriptor fallback. It also covers an
unavailable stderr descriptor and an isolated non-propagating logger so neither
shutdown failure nor unrelated root handlers can escape the boundary.

## Practitioner evidence

- [pytest issue #5577](https://github.com/pytest-dev/pytest/issues/5577) has
  tracked `ValueError: I/O operation on closed file` from logging in an
  `atexit` callback since 2019. Its minimal reproducer matches Gludd's boundary:
  pytest finishes capture before Python invokes the callback. Later projects
  continued linking fixes to the same report through 2025, demonstrating that
  application-owned late-shutdown handling remains necessary.
- [Stack Overflow's long-running pytest logging discussion](https://stackoverflow.com/questions/4724122/how-can-i-combine-stdlib-logging-with-py-test)
  documents the same failure from an `atexit` function writing through a
  captured standard stream. Gludd therefore retains the warning through the
  process-owned descriptor instead of muting logging globally or disabling
  pytest capture.

This boundary is intentionally narrow: ordinary runtime logging retains its
configured handlers, formatting, levels, and propagation. Only a demonstrably
closed stream activates the late-shutdown fallback.
