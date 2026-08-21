# Ansible Multiprocessing Context

Status: implemented
Last reviewed: 2026-08-20

## Problem

`ansible-core` creates task workers from a `fork` multiprocessing context. That
is unsafe when Gludd is called by a threaded service or test runner: Python 3.12
and newer detect the parent threads and warn that the fork can deadlock. The
warning appeared in eight `core_runner_deep` gate nodes on Python 3.14.

Ignoring the warning is not an acceptable runtime contract. A child produced by
forking a threaded parent can inherit locks without the threads that release
them, and the failure may present as an unbounded Ansible run rather than an
immediate exception.

## Runtime contract

Native `ansible.executor.playbook_executor.PlaybookExecutor` calls run in
Gludd's existing bounded process worker. The worker selects `forkserver` where
the platform supports it and otherwise selects `spawn`; it never selects
`fork`. A module-level child entry point keeps the payload picklable, and a
per-run child marker prevents recursive worker creation.

The worker clone carries only execution-owned, serializable settings. Network
policy validation happens before dispatch, while enabled external process
isolation still uses its dedicated runner backend. Injected executor seams that
do not create Ansible processes remain inline, preserving integrations that
provide their own lifecycle.

The parent waits for the configured timeout, with the existing 300-second
default when no override is provided. It fails closed if the child cannot start,
exits without a result, reports an exception, or exceeds the bound. Timeout
cleanup terminates the child process group; all paths close the result queue,
join its feeder thread, close the process handle, restore the child marker, and
close Ansible's connection lock file.

## Upstream and practitioner evidence

Evidence was reviewed on 2026-08-20.

- The [Python multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
  records that Python 3.12 warns when `fork` detects multiple threads, describes
  multithreaded fork as problematic, and documents `forkserver` as generally
  safe because its server is normally single-threaded. Python 3.14 also changed
  the POSIX default from `fork` to `forkserver` for this reason.
- A [PyTorch practitioner report opened 2024-10-25](https://github.com/pytorch/pytorch/issues/138957)
  reproduces the same `popen_fork.py` warning after library work starts threads.
  The triaged issue remained open when reviewed, showing that relying on a
  dependency's implicit default is not a durable library boundary.
- A [doit maintainer report opened 2026-02-08](https://github.com/pydoit/doit/issues/480)
  records the identical warning in a task runner and evaluates `spawn` and
  `forkserver` as the safe alternatives, including their serialization
  trade-offs.
- A [CPython report opened 2025-10-10](https://github.com/python/cpython/issues/139894)
  provides a separate real-world example where explicit `fork` inherits async
  process state and fails on Python 3.14 while `spawn` succeeds.

## Verification and rollback

The regression starts a real background thread and turns the exact Ansible fork
warning into an error. It failed before the runtime boundary and passes after
the change. The focused runner matrix passes 259 tests with warnings treated as
errors, and branch coverage for `core_runner.py` is 87%.

This is a zero-downtime-compatible execution change: no persisted data, wire
format, inventory, playbook, or collection contract changes. Each call owns and
cleans up its worker, so old and new application processes can overlap during a
rolling deployment. Rollback is code-only; calls already running in either
version retain their own worker and cleanup lifecycle.
