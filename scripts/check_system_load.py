#!/usr/bin/env python3
"""Read-only system load diagnostic. Prints 1m load avg, CPU count, and verdict.

Exit 0 always — this is a diagnostic, not an enforcement gate.
The AGENTS.md System-Load Gate section uses the verdict to decide dispatch caps.

macOS: sysctl -n vm.loadavg  →  "{1m} {5m} {15m}"
Linux: /proc/loadavg          →  "{1m} {5m} {15m} ..."
CPU:   sysctl -n hw.ncpu (macOS) or nproc (Linux)
"""

from __future__ import annotations

import platform
import subprocess
import sys


def _read_loadavg() -> tuple[float, float, float]:
    system = platform.system()
    if system == "Darwin":
        out = subprocess.check_output(["sysctl", "-n", "vm.loadavg"], text=True, timeout=5)
        parts = out.strip().strip("{}").split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    else:
        with open("/proc/loadavg") as fh:
            parts = fh.read().strip().split()
        return float(parts[0]), float(parts[1]), float(parts[2])


def _cpu_count() -> int:
    system = platform.system()
    if system == "Darwin":
        out = subprocess.check_output(["sysctl", "-n", "hw.ncpu"], text=True, timeout=5)
        return int(out.strip())
    else:
        out = subprocess.check_output(["nproc"], text=True, timeout=5)
        return int(out.strip())


def _verdict(load_1m: float, cpu_count: int) -> tuple[str, str]:
    ratio = load_1m / cpu_count if cpu_count > 0 else float("inf")
    if ratio < 2.0:
        return "OK", f"load {load_1m:.2f} < 2x CPU count {cpu_count} ({ratio:.1f}x)"
    elif ratio <= 3.0:
        return "WARN", f"load {load_1m:.2f} at {ratio:.1f}x CPU count ({cpu_count}) — 2x-3x range"
    else:
        return "CRITICAL", f"load {load_1m:.2f} at {ratio:.1f}x CPU count ({cpu_count}) — above 3x"


def main() -> int:
    try:
        one, five, fifteen = _read_loadavg()
        cpu = _cpu_count()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"SYSTEM-LOAD: ERROR reading load/CPU: {exc}")
        return 0

    verdict_label, detail = _verdict(one, cpu)
    print(f"=== SYSTEM LOAD ===")
    print(f"Load Average : {one:.2f} {five:.2f} {fifteen:.2f}")
    print(f"CPU Count    : {cpu}")
    print(f"Load/CPU     : {one / cpu:.2f}x" if cpu > 0 else "Load/CPU     : N/A")
    print(f"Verdict      : {verdict_label}")
    print(f"Detail       : {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
