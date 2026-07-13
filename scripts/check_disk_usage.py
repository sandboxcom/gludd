#!/usr/bin/env python3
"""Pre-commit disk check: fail if /tmp/gludd-* exceeds 100 MB or disk is >90% full.

Exit 0 = ok, exit 1 = over threshold.
"""
import os
import subprocess
import sys
from pathlib import Path

GLUDD_TMP_LIMIT_MB = 100
DISK_USAGE_PCT_LIMIT = 90


def _gludd_tmp_size_mb() -> float:
    """Return total size of /tmp/gludd-* files/dirs in MB."""
    total = 0
    for entry in Path("/tmp").glob("gludd-*"):
        if entry.is_file():
            total += entry.stat().st_size
        elif entry.is_dir():
            for fp in entry.rglob("*"):
                if fp.is_file():
                    total += fp.stat().st_size
    return total / (1024 * 1024)


def _disk_usage_pct() -> float:
    """Return root disk usage percentage (0–100)."""
    try:
        out = subprocess.check_output(
            ["df", "/"], text=True, timeout=10
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return 0.0
    parts = out.strip().split("\n")[-1].split()
    if len(parts) < 5:
        return 0.0
    try:
        return float(parts[4].rstrip("%"))
    except ValueError:
        return 0.0


def main() -> int:
    failures: list[str] = []
    tmp_mb = _gludd_tmp_size_mb()
    disk_pct = _disk_usage_pct()

    if tmp_mb > GLUDD_TMP_LIMIT_MB:
        failures.append(
            f"/tmp/gludd-* total {tmp_mb:.1f} MB > {GLUDD_TMP_LIMIT_MB} MB limit"
        )
    if disk_pct > DISK_USAGE_PCT_LIMIT:
        failures.append(
            f"disk usage {disk_pct:.1f}% > {DISK_USAGE_PCT_LIMIT}% limit"
        )

    if not failures:
        print(
            f"disk ok: /tmp/gludd-* = {tmp_mb:.1f} MB "
            f"(≤{GLUDD_TMP_LIMIT_MB}), disk = {disk_pct:.1f}% "
            f"(≤{DISK_USAGE_PCT_LIMIT})"
        )
        return 0

    for f in failures:
        print(f"DISK FAIL: {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
