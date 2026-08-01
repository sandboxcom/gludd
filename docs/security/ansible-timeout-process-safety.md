# Ansible timeout process safety

## Decision

`CoreAnsibleRunner` executes a time-bounded in-process Ansible playbook in a
separate, non-daemon worker created with `forkserver` when the platform supports
it and `spawn` otherwise. The worker entry point is importable at module scope,
and all arguments crossing the process boundary are pickle-compatible. Gludd
never falls back to inline execution when a caller requested a positive timeout.

This removes the explicit `fork` call that Python 3.14 warns about when Gunicorn,
pytest, an event consumer, or a dependency has already started another thread.
It also preserves the existing security and lifecycle invariants:

- the seccomp filter is applied in the worker before Ansible executes;
- `setsid()` gives each job a dedicated process group;
- deadline expiry terminates and then kills that group, including Ansible task
  descendants, and returns rc 124;
- the managed-process registry observes the child for its entire lifetime; and
- worker startup failure is returned as a failure instead of weakening the
  requested bound.

## Why `fork` is not an acceptable optimization

Python's multiprocessing documentation says that `fork` from a process with
multiple threads can emit `DeprecationWarning` and directs applications to use a
different start method. Python 3.14 changed the POSIX default away from `fork`;
`forkserver` retains copy-on-write process creation while avoiding the calling
process's multithreaded state. See the [multiprocessing start-method
documentation](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
and [Python 3.14 release notes](https://docs.python.org/3/whatsnew/3.14.html#multiprocessing).

This is a long-lived operational problem, not only a new warning. User reports
include a [2016 deadlock when mixing threading and
multiprocessing](https://bugs.python.org/issue27422), the CPython design issue
[“fork()-without-exec() is broken”](https://github.com/python/cpython/issues/84559),
and the public Python discussion [about deprecating fork with live
threads](https://discuss.python.org/t/concerns-regarding-deprecation-of-fork-with-alive-threads/33555).
The reports describe duplicated locked state, intermittent deadlocks, incomplete
cleanup, and I/O corruption. Checking only Python-visible thread counts is not a
safe workaround because native libraries may own threads and a new thread can
race the check.

## Ansible Runner alternative considered

Ansible Runner supports a `job_timeout`, a per-iteration `cancel_callback`,
asynchronous execution, and status/event callbacks. Its documented timeout
terminates the Ansible process, so it remains the correct boundary for Gludd's
container-isolated runner path. See [Runner settings](https://docs.ansible.com/projects/runner/en/latest/intro/#env-settings-settings-for-runner-itself)
and the [Python interface](https://docs.ansible.com/projects/runner/en/latest/python_interface/).

The core path deliberately keeps Gludd's wrapper process rather than replacing
it with Runner's timeout. That wrapper owns the seccomp-before-exec boundary,
process-group cleanup, managed-process registration, and direct callback result
transport. Switching timeout mechanisms would silently change those guarantees.

## Regression contract

`tests/unit/test_ansible_timeout_process_safety.py` keeps a real background
thread alive, turns the unsafe-fork warning into an exception, and runs a bounded
playbook worker. The security red-team suite separately proves real sleeping
playbooks are killed within the deadline and return rc 124. Routing-only tests
leave the optional environment timeout empty so their patched in-process seam is
not mistaken for a cross-process integration test.
