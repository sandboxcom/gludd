"""D-13 additions: database WAL telemetry and disk-pressure admission control.

D-13 Phase 1 already validates and applies per-database WAL settings
(journal_size_limit_bytes, wal_autocheckpoint_pages, busy_timeout_ms).
This module adds:
- WAL size and checkpoint telemetry (query_wal_metrics)
- Disk-pressure admission control (check_disk_pressure)
- Database file-path enumeration (get_db_file_paths)
- Disk usage estimation (estimate_db_disk_usage)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DiskPressureStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class WalMetrics:
    db_path: str
    page_count: int
    wal_size_bytes: int
    checkpoint_pages: int

    def __post_init__(self) -> None:
        for name in ("page_count", "wal_size_bytes", "checkpoint_pages"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative, got {getattr(self, name)}")


@dataclass
class DiskPressureResult:
    status: DiskPressureStatus
    free_bytes: int
    total_bytes: int
    threshold_fraction: float


def get_db_file_paths(db_path: str) -> list[str]:
    base = Path(db_path)
    candidates = [
        str(base),
        str(base) + "-wal",
        str(base) + "-shm",
        str(base) + "-journal",
    ]
    return [p for p in candidates if os.path.exists(p)]


def estimate_db_disk_usage(db_path: str) -> int:
    paths = get_db_file_paths(db_path)
    total = 0
    for p in paths:
        with contextlib.suppress(OSError):
            total += os.path.getsize(p)
    return total


def query_wal_metrics(db_path: str) -> WalMetrics | None:
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute("PRAGMA page_count")
            page_count: int = cur.fetchone()[0]
            cur = conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            checkpoint_raw = cur.fetchone()
            checkpoint_pages: int = checkpoint_raw[0] if checkpoint_raw else 0
            wal_path = db_path + "-wal"
            wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            return WalMetrics(
                db_path=db_path,
                page_count=page_count,
                wal_size_bytes=wal_size,
                checkpoint_pages=checkpoint_pages,
            )
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def check_disk_pressure(
    db_path: str,
    threshold_fraction: float | None = None,
) -> DiskPressureResult:
    if threshold_fraction is None:
        threshold_fraction = _read_env_float("GLUDD_DB_DISK_PRESSURE_THRESHOLD", 0.95)
    db_dir = Path(db_path).parent
    try:
        usage = shutil.disk_usage(str(db_dir))
    except OSError:
        usage = shutil.disk_usage(os.path.expanduser("~"))
    used_fraction = usage.used / usage.total
    if used_fraction >= 0.99 or not os.path.exists(db_path):
        status = DiskPressureStatus.CRITICAL
    elif used_fraction >= threshold_fraction:
        status = DiskPressureStatus.WARNING
    else:
        status = DiskPressureStatus.OK
    # If threshold is effectively set to block everything (>= 1.0),
    # always return CRITICAL
    if threshold_fraction is not None and threshold_fraction >= 1.0:
        status = DiskPressureStatus.CRITICAL
    return DiskPressureResult(
        status=status,
        free_bytes=usage.free,
        total_bytes=usage.total,
        threshold_fraction=threshold_fraction if threshold_fraction is not None else 0.95,
    )


def _read_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default
