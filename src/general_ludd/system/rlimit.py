"""Best-effort POSIX resource limits (address space + CPU time).

Extracted from ``general_ludd.abtest._child._apply_limits`` so the same proven,
fail-open RLIMIT helper can be reused by other memory-bounded runners (e.g. the
adaptive test runner's per-worker RSS cap) without duplicating the clamping and
platform-guarding logic.

The helper is a no-op where the ``resource`` module or a given limit is
unavailable (e.g. Windows), so callers still work there with only their own
wall-clock/other backstops. It never RAISES the soft limit above the inherited
hard limit — it clamps down to ``min(request, hard)`` so an unprivileged process
cannot fail with ``EPERM`` trying to widen a limit.
"""

from __future__ import annotations

import sys


def apply_limits(mem_mb: int, cpu_s: int) -> None:
    """Apply address-space (``RLIMIT_AS``) and CPU-time (``RLIMIT_CPU``) limits.

    Args:
        mem_mb: Address-space cap in MiB. Values <= 0 skip the memory limit.
        cpu_s: CPU-time cap in seconds. Values <= 0 skip the CPU limit.

    Best-effort: no-op where ``resource`` (or a specific RLIMIT constant) is
    unavailable, and swallows ``ValueError``/``OSError`` from ``setrlimit`` so a
    sandbox that forbids the call does not crash the caller.
    """
    try:
        import resource
    except ImportError:
        return

    if (
        mem_mb > 0
        and hasattr(resource, "RLIMIT_AS")
        and not (sys.platform == "darwin" and getattr(resource, "__name__", "") == "resource")
    ):
        nbytes = mem_mb * 1024 * 1024
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            capped = nbytes if hard == resource.RLIM_INFINITY else min(nbytes, hard)
            # Clamp BOTH soft and hard to `capped`. Passing soft > hard raises
            # ValueError (which we'd swallow, applying nothing); and we never
            # raise the hard limit above what we inherited.
            resource.setrlimit(resource.RLIMIT_AS, (capped, capped))
        except (ValueError, OSError):
            pass

    if cpu_s > 0 and hasattr(resource, "RLIMIT_CPU"):
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
            capped = cpu_s if hard == resource.RLIM_INFINITY else min(cpu_s, hard)
            resource.setrlimit(resource.RLIMIT_CPU, (capped, capped))
        except (ValueError, OSError):
            pass
