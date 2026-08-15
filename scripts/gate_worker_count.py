#!/usr/bin/env python3
"""gate_worker_count.py — memory-bounded xdist worker count.

Formula:
    cpu_based  = max(1, cpu_count // 4)
    mem_based  = max(1, floor(available_RAM_GB / PER_WORKER_GB))
    workers    = min(cpu_based, mem_based)

Environment overrides (all optional):
    GLUDD_XDIST        — hard override; if set, its value is printed verbatim
                         and the formula is skipped entirely.
    GLUDD_PER_WORKER_GB — per-worker RAM budget in GB (default: 1.5).
                          Accepts floats, e.g. "2.0".

Exit codes: always 0 (prints the worker count to stdout).

Usage (from Makefile / run_gate.sh):
    WORKERS=$(python3 scripts/gate_worker_count.py)
"""

from __future__ import annotations

import math
import os
import sys


def _available_ram_gb() -> float:
    """Return available (not total) system RAM in GiB, best-effort.

    Tries (in order):
      1. psutil.virtual_memory().available  — cross-platform, precise
      2. /proc/meminfo MemAvailable          — Linux without psutil
      3. sysctl vm.page_free_count * pagesize — macOS without psutil
      4. Falls back to total/2 via os.sysconf  — last resort

    Never raises; returns a positive float in all cases.
    """
    # --- psutil (preferred) ------------------------------------------------
    try:
        import psutil  # type: ignore[import-untyped]

        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        pass

    # --- Linux /proc/meminfo -----------------------------------------------
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
    except Exception:
        pass

    # --- macOS sysctl ------------------------------------------------------
    try:
        import subprocess

        pagesize_out = subprocess.check_output(
            ["sysctl", "-n", "hw.pagesize"], text=True, timeout=2
        ).strip()
        free_out = subprocess.check_output(
            ["sysctl", "-n", "vm.page_free_count"], text=True, timeout=2
        ).strip()
        pagesize = int(pagesize_out)
        free_pages = int(free_out)
        return (pagesize * free_pages) / (1024 ** 3)
    except Exception:
        pass

    # --- last resort: total / 2 via os.sysconf ----------------------------
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_bytes = pages * page_size
        return total_bytes / 2 / (1024 ** 3)
    except Exception:
        pass

    # Absolute fallback: assume 2 GB available (yields 1 worker at default budget)
    return 2.0


def compute_worker_count(
    *,
    cpu_count: int | None = None,
    available_ram_gb: float | None = None,
    per_worker_gb: float = 1.5,
) -> int:
    """Return the memory-bounded xdist worker count.

    Parameters
    ----------
    cpu_count:
        Number of logical CPUs. Defaults to ``os.cpu_count()``.
    available_ram_gb:
        Available system RAM in GiB. Defaults to a live reading via
        ``_available_ram_gb()``.
    per_worker_gb:
        RAM budget per xdist worker in GiB. Default 1.5 GiB.

    Returns
    -------
    int
        ``min(cpu_based, mem_based)`` where:
        - ``cpu_based  = max(1, cpu_count // 4)``
        - ``mem_based  = max(1, floor(available_ram_gb / per_worker_gb))``
    """
    if per_worker_gb <= 0:
        raise ValueError(f"per_worker_gb must be positive, got {per_worker_gb}")

    cpus: int = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    ram_gb: float = available_ram_gb if available_ram_gb is not None else _available_ram_gb()

    cpu_based = max(1, cpus // 4)
    mem_based = max(1, math.floor(ram_gb / per_worker_gb))

    return min(cpu_based, mem_based)


def main() -> None:
    # Hard override: GLUDD_XDIST beats the formula entirely.
    xdist_env = os.environ.get("GLUDD_XDIST")
    if xdist_env:
        print(xdist_env)
        return

    per_worker_gb_str = os.environ.get("GLUDD_PER_WORKER_GB", "1.5")
    try:
        per_worker_gb = float(per_worker_gb_str)
    except ValueError:
        print(
            f"gate_worker_count: invalid GLUDD_PER_WORKER_GB={per_worker_gb_str!r}, "
            "falling back to 1.5",
            file=sys.stderr,
        )
        per_worker_gb = 1.5

    count = compute_worker_count(per_worker_gb=per_worker_gb)
    print(count)


if __name__ == "__main__":
    main()
