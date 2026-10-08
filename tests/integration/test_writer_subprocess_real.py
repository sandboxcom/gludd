"""Real-process acceptance for fail-closed writer bootstrap."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from general_ludd.writer.process import WriterProcess

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_existing_envelopes(spool_path: Path) -> None:
    envelopes = (
        {
            "topic": "execute_sql",
            "payload": {
                "sql": (
                    "CREATE TABLE writer_boot_probe "
                    "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
            },
        },
        {
            "topic": "execute_sql",
            "payload": {
                "sql": (
                    "INSERT INTO writer_boot_probe (id, value) "
                    "VALUES (:id, :value)"
                ),
                "params": {"id": 1, "value": "ready-after-write"},
            },
        },
    )
    spool_path.write_text(
        "".join(json.dumps(envelope) + "\n" for envelope in envelopes),
        encoding="utf-8",
    )


def test_flat_config_applies_existing_envelope_before_readiness(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "writer.db"
    spool_path = tmp_path / "writer.jsonl"
    _write_existing_envelopes(spool_path)
    writer = WriterProcess(
        {
            "url": f"sqlite+aiosqlite:///{database_path}",
            "inbound_spool_path": str(spool_path),
            "tick_interval": 0.02,
        }
    )

    try:
        assert writer.start(timeout=20.0) is True
        with closing(sqlite3.connect(database_path)) as connection:
            row = connection.execute(
                "SELECT id, value FROM writer_boot_probe"
            ).fetchone()
        assert row == (1, "ready-after-write")
    finally:
        writer.stop(sigterm_timeout=2.0)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"database": {}},
        {"database": {"url": 123}},
        {"database": {"url": "not a sqlalchemy url"}},
    ],
    ids=["missing-database", "missing-url", "non-string-url", "invalid-url"],
)
def test_child_rejects_missing_or_invalid_database_without_readiness(
    tmp_path: Path,
    config: dict[str, Any],
) -> None:
    config_path = tmp_path / "writer.json"
    ready_path = tmp_path / "ready.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    started = time.monotonic()
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "general_ludd.writer._child",
            str(config_path),
            str(ready_path),
            "fail-closed-nonce",
        ],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = child.communicate(timeout=15.0)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5.0)
        pytest.fail("invalid writer child did not exit within the bounded 15s bootstrap")

    assert child.returncode not in (None, 0)
    assert time.monotonic() - started < 15.0
    assert not ready_path.exists()
    assert "database" in (stdout + stderr).lower() or "url" in (
        stdout + stderr
    ).lower()
