"""Unit coverage for the fail-closed writer subprocess lifecycle."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from general_ludd.writer.process import WriterProcess


def _real_config(tmp_path: Path, **child_options: Any) -> dict[str, Any]:
    """Return the flat database shape passed by daemon startup."""
    return {
        "url": f"sqlite+aiosqlite:///{tmp_path / 'writer.db'}",
        "tick_interval": 0.02,
        **child_options,
    }


def _wait_for_exit(proc_pid: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(proc_pid, 0)
        except (ProcessLookupError, OSError):
            return True
        time.sleep(0.05)
    return False


def test_writer_process_normalizes_flat_production_database_config(
    tmp_path: Path,
) -> None:
    config = _real_config(
        tmp_path,
        inbound_spool_path=str(tmp_path / "writer.jsonl"),
    )

    writer = WriterProcess(config)

    assert writer.config == {
        "database": {"url": config["url"]},
        "inbound_spool_path": config["inbound_spool_path"],
        "tick_interval": 0.02,
    }


def test_writer_process_copies_already_normalized_config(tmp_path: Path) -> None:
    config = {
        "database": {"url": f"sqlite+aiosqlite:///{tmp_path / 'writer.db'}"},
        "tick_interval": 0.01,
    }

    writer = WriterProcess(config)
    config["database"]["url"] = "changed"  # type: ignore[index]

    assert writer.config["database"]["url"] != "changed"


def test_writer_process_rejects_non_mapping_config() -> None:
    with pytest.raises(TypeError, match="config must be a dict"):
        WriterProcess(config=[])  # type: ignore[arg-type]


def test_writer_process_spawn_starts_real_subprocess(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    try:
        assert writer.start(timeout=20.0) is True
        assert writer.is_alive() is True
        assert writer.pid is not None
        assert writer.pid != os.getpid()
        assert writer.pid > 0
    finally:
        writer.stop()


def test_writer_process_stop_terminates_child(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    writer.start(timeout=20.0)
    pid = writer.pid
    assert pid is not None

    assert writer.stop() is True
    assert writer.is_alive() is False
    assert _wait_for_exit(pid, timeout=10.0) is True


def test_writer_process_stop_sigkill_if_sigterm_ignored(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path, ignore_sigterm=True))
    writer.start(timeout=20.0)
    pid = writer.pid
    assert pid is not None

    started = time.monotonic()
    assert writer.stop(sigterm_timeout=0.2) is True

    assert time.monotonic() - started < 10.0
    assert _wait_for_exit(pid, timeout=5.0) is True


def test_writer_process_readiness_handshake(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    try:
        started = time.monotonic()
        assert writer.start(timeout=20.0) is True
        assert time.monotonic() - started < 15.0
        assert writer.is_ready() is True
        assert len(writer._readiness_nonce) == 64
    finally:
        writer.stop()


def test_writer_process_start_times_out_when_child_never_ready(
    tmp_path: Path,
) -> None:
    writer = WriterProcess(config=_real_config(tmp_path, skip_ready=True))

    with pytest.raises(TimeoutError, match="did not signal readiness"):
        writer.start(timeout=0.5)

    assert writer.is_ready() is False
    assert writer.is_alive() is False


def test_writer_process_health_check_returns_false_after_death(
    tmp_path: Path,
) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    writer.start(timeout=20.0)
    pid = writer.pid
    assert pid is not None

    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and writer.is_alive():
        time.sleep(0.05)

    assert writer.is_alive() is False
    assert writer.stop() is True


def test_writer_process_double_start_is_error(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    try:
        writer.start(timeout=20.0)
        with pytest.raises(RuntimeError, match="called twice"):
            writer.start(timeout=20.0)
    finally:
        writer.stop()


def test_writer_process_stop_is_idempotent(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    writer.start(timeout=20.0)

    assert writer.stop() is True
    assert writer.stop() is True
    assert writer.stop() is True


def test_writer_process_stop_before_start_is_safe(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))

    assert writer.stop() is True
    assert writer.is_alive() is False


def test_writer_process_uses_current_interpreter(tmp_path: Path) -> None:
    writer = WriterProcess(config=_real_config(tmp_path))
    try:
        writer.start(timeout=20.0)
        assert writer._argv[0] == sys.executable
    finally:
        writer.stop()
