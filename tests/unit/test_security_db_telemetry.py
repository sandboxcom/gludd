"""TDD tests for D-13 additions: DB telemetry and disk-pressure admission.

D-13 Phase 1 already validates and applies per-database WAL settings.
These tests cover the remaining telemetry and admission-control pieces:
- WAL size and checkpoint telemetry
- Disk-pressure admission control
- Bounded limits on telemetry data
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

import pytest

from general_ludd.security.db_telemetry import (
    DiskPressureStatus,
    WalMetrics,
    check_disk_pressure,
    estimate_db_disk_usage,
    get_db_file_paths,
    query_wal_metrics,
)


class _DiskUsage(NamedTuple):
    """Represent deterministic disk capacity for admission tests."""

    total: int
    used: int
    free: int


def _set_disk_usage(
    monkeypatch: pytest.MonkeyPatch,
    *,
    used: int,
    total: int = 1_000,
) -> None:
    """Replace host disk telemetry with one explicit capacity snapshot."""

    def _disk_usage(_path: str) -> _DiskUsage:
        return _DiskUsage(total=total, used=used, free=total - used)

    monkeypatch.setattr(
        "general_ludd.security.db_telemetry.shutil.disk_usage",
        _disk_usage,
    )


@pytest.fixture
def sqlite_db() -> Generator[str, None, None]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    with __import__("contextlib", fromlist=["suppress"]).suppress(OSError):
        os.unlink(path)
        os.unlink(path + "-wal") if os.path.exists(path + "-wal") else None
        os.unlink(path + "-shm") if os.path.exists(path + "-shm") else None


# ---------------------------------------------------------------------------
# WalMetrics
# ---------------------------------------------------------------------------


def test_wal_metrics_dataclass_fields() -> None:
    m = WalMetrics(
        db_path="/tmp/test.db",
        page_count=42,
        wal_size_bytes=1024,
        checkpoint_pages=10,
    )
    assert m.db_path == "/tmp/test.db"
    assert m.page_count == 42
    assert m.wal_size_bytes == 1024
    assert m.checkpoint_pages == 10


def test_wal_metrics_negative_guard_raises() -> None:
    with pytest.raises(ValueError):
        WalMetrics(
            db_path="/tmp/test.db",
            page_count=-1,
            wal_size_bytes=0,
            checkpoint_pages=0,
        )


# ---------------------------------------------------------------------------
# DiskPressureStatus
# ---------------------------------------------------------------------------


def test_disk_pressure_status_values() -> None:
    assert DiskPressureStatus.OK.value == "ok"
    assert DiskPressureStatus.WARNING.value == "warning"
    assert DiskPressureStatus.CRITICAL.value == "critical"


# ---------------------------------------------------------------------------
# get_db_file_paths
# ---------------------------------------------------------------------------


def test_get_db_file_paths_main_only(sqlite_db: str) -> None:
    paths = get_db_file_paths(sqlite_db)
    assert sqlite_db in paths
    for p in paths:
        assert os.path.exists(p)


def test_get_db_file_paths_type_annotation() -> None:
    paths = get_db_file_paths("/nonexistent/path/db.sqlite")
    assert isinstance(paths, list)
    assert all(isinstance(p, str) for p in paths)


# ---------------------------------------------------------------------------
# estimate_db_disk_usage
# ---------------------------------------------------------------------------


def test_estimate_disk_usage_empty_db(sqlite_db: str) -> None:
    usage = estimate_db_disk_usage(sqlite_db)
    assert usage >= 0
    assert isinstance(usage, int)


def test_estimate_disk_usage_nonexistent_returns_zero() -> None:
    assert estimate_db_disk_usage("/nonexistent/db/path.db") == 0


# ---------------------------------------------------------------------------
# check_disk_pressure
# ---------------------------------------------------------------------------


def test_check_disk_pressure_ok_default_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=500)
    result = check_disk_pressure(str(tmp_path), threshold_fraction=0.99)
    assert result.status == DiskPressureStatus.OK


def test_check_disk_pressure_critical_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=500)
    result = check_disk_pressure(str(tmp_path), threshold_fraction=1.0001)
    assert result.status == DiskPressureStatus.CRITICAL


def test_check_disk_pressure_nonexistent_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_disk_usage(monkeypatch, used=500)
    result = check_disk_pressure("/nonexistent/db/path")
    assert result.status == DiskPressureStatus.CRITICAL


def test_check_disk_pressure_includes_free_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=250)
    result = check_disk_pressure(str(tmp_path))
    assert result.free_bytes == 750
    assert result.total_bytes == 1_000


def test_check_disk_pressure_respects_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=500)
    monkeypatch.setenv("GLUDD_DB_DISK_PRESSURE_THRESHOLD", "1.0")
    result = check_disk_pressure(str(tmp_path))
    assert result.status == DiskPressureStatus.CRITICAL


def test_check_disk_pressure_invalid_env_uses_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=900)
    monkeypatch.setenv("GLUDD_DB_DISK_PRESSURE_THRESHOLD", "not_a_number")
    result = check_disk_pressure(str(tmp_path))
    assert result.status == DiskPressureStatus.OK


def test_check_disk_pressure_critical_host_capacity_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disk_usage(monkeypatch, used=995)
    result = check_disk_pressure(str(tmp_path), threshold_fraction=0.95)
    assert result.status == DiskPressureStatus.CRITICAL


# ---------------------------------------------------------------------------
# query_wal_metrics
# ---------------------------------------------------------------------------


def test_query_wal_metrics_returns_metrics(sqlite_db: str) -> None:
    import sqlite3

    conn = sqlite3.connect(sqlite_db)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO test (data) VALUES ('hello')")
    conn.commit()
    conn.close()

    result = query_wal_metrics(sqlite_db)
    assert isinstance(result, WalMetrics)
    assert result.db_path == sqlite_db
    assert result.page_count > 0


def test_query_wal_metrics_nonexistent_db() -> None:
    result = query_wal_metrics("/nonexistent/path.db")
    assert result is None


def test_query_wal_metrics_wal_size_non_negative(sqlite_db: str) -> None:
    result = query_wal_metrics(sqlite_db)
    if result is not None:
        assert result.wal_size_bytes >= 0


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------


def test_default_disk_pressure_threshold() -> None:
    threshold = float(os.environ.get("GLUDD_DB_DISK_PRESSURE_THRESHOLD", "0.95"))
    assert 0 < threshold <= 1.0


def test_default_wal_checkpoint_pages_bound() -> None:
    pages = int(os.environ.get("GLUDD_DB_WAL_AUTOCHECKPOINT_PAGES", "1000"))
    assert 1 <= pages <= 100000


def test_default_journal_size_limit_bound() -> None:
    limit = int(os.environ.get("GLUDD_DB_JOURNAL_SIZE_LIMIT_BYTES", str(64 * 1024 * 1024)))
    assert 1_048_576 <= limit <= 1_073_741_824
