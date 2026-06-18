#!/usr/bin/env python3
"""
gate_worker_count.py — compute a memory-capped pytest-xdist worker count.

Pure function `compute_workers` is the canonical logic; this script's
__main__ block is the shell entry-point called by run_gate.sh so that
the logic is unit-testable without invoking bash.

Rules (in priority order):
1.  total_ram_bytes / _RAM_PER_WORKER gives a hard memory ceiling (floor 1).
2.  An unset / empty GLUDD_XDIST → min(cpu_count // 4, mem_cap), floor 1.
3.  GLUDD_XDIST='auto'  → same as unset.
4.  GLUDD_XDIST=<int>   → min(int, mem_cap), floor 1.
        An explicit value may ONLY lower the count, never raise it above
        the memory ceiling.
5.  The result is always >= 1.

Usage (called by run_gate.sh):
    python3 scripts/gate_worker_count.py
Reads GLUDD_XDIST from the environment; writes the chosen count to stdout
and a diagnostic line to stderr.
"""

import os
import sys

_RAM_PER_WORKER: int = 4 * 1024 ** 3  # 4 GiB per worker


def compute_workers(
    gludd_xdist: str | None,
    cpu_count: int,
    total_ram_bytes: int,
) -> int:
    """
    Return the number of pytest-xdist workers to use.

    Parameters
    ----------
    gludd_xdist:
        The raw value of the GLUDD_XDIST environment variable (or None /
        empty string when unset).
    cpu_count:
        Logical CPU count (os.cpu_count()).
    total_ram_bytes:
        Total physical RAM in bytes
        (os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')).

    Returns
    -------
    int >= 1
    """
    mem_cap = max(1, total_ram_bytes // _RAM_PER_WORKER)
    cpu_quarter = max(1, cpu_count // 4)

    xdist = (gludd_xdist or "").strip()

    if not xdist or xdist.lower() == "auto":
        # Unset or 'auto': derive from CPU, capped by memory.
        count = min(cpu_quarter, mem_cap)
    else:
        # Explicit numeric override: honour it but never exceed mem_cap.
        try:
            requested = int(xdist)
        except ValueError:
            # Unparseable → fall back to safe default.
            requested = cpu_quarter
        count = min(max(1, requested), mem_cap)

    return max(1, count)


if __name__ == "__main__":
    try:
        total_ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, AttributeError, OSError):
        # Fallback: assume 8 GiB when sysconf is unavailable (Windows, etc.)
        total_ram = 8 * 1024 ** 3

    cpu = os.cpu_count() or 1
    xdist_env = os.environ.get("GLUDD_XDIST")

    workers = compute_workers(xdist_env, cpu, total_ram)

    print(
        f"[gate_worker_count] cpu={cpu} ram_gb={total_ram // 1024**3:.1f} "
        f"mem_cap={max(1, total_ram // _RAM_PER_WORKER)} "
        f"GLUDD_XDIST={xdist_env!r} -> workers={workers}",
        file=sys.stderr,
    )
    print(workers)
