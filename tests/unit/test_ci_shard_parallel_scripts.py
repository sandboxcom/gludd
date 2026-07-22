from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAMES = (
    "ci_shards_parallel_status.py",
    "run_ci_shard_summary.py",
    "run_ci_shards_parallel.py",
    "start_ci_shards_parallel_bg.py",
)


def test_local_ci_shard_helper_scripts_compile() -> None:
    for script_name in SCRIPT_NAMES:
        py_compile.compile(str(ROOT / "scripts" / script_name), doraise=True)


def test_ci_shards_parallel_status_fails_closed_without_state(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "ci_shards_parallel_status.py"

    result = subprocess.run(
        [sys.executable, str(script), "--lines", "1"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "CI-SHARDS-STATUS missing state" in result.stdout


def test_ci_shards_parallel_status_reports_log_freshness_and_last_heartbeat(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "ci_shards_parallel_status.py"
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_path = log_dir / "ci-shards-parallel.log"
    heartbeat_line = "SHARD-HEARTBEAT pending=" + json.dumps(["unit-2"]) + " completed={}"
    log_path.write_text(
        chr(10).join(["=== ci shard unit-2: launch ===", heartbeat_line, "still running"]) + chr(10),
        encoding="utf-8",
    )
    state = {
        "pid": os.getpid(),
        "log": str(log_path),
        "shards": ["unit-2"],
        "started_at": "2026-07-22T00:00:00+00:00",
    }
    (log_dir / "ci-shards-parallel-state.json").write_text(json.dumps(state), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), "--lines", "2"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "log_age_seconds=" in result.stdout
    assert "last_heartbeat_age_seconds=" in result.stdout
    assert "last_heartbeat=" + heartbeat_line in result.stdout


def test_ci_shards_parallel_status_watch_mode_emits_poll_heartbeats(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "ci_shards_parallel_status.py"
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_path = log_dir / "ci-shards-parallel.log"
    heartbeat_line = "SHARD-HEARTBEAT pending=" + json.dumps(["unit-3"]) + " completed={}"
    log_path.write_text(heartbeat_line + chr(10), encoding="utf-8")
    state = {"pid": os.getpid(), "log": str(log_path), "shards": ["unit-3"]}
    (log_dir / "ci-shards-parallel-state.json").write_text(json.dumps(state), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--lines",
            "1",
            "--watch",
            "--interval-seconds",
            "0.01",
            "--max-polls",
            "1",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "CI-SHARDS-WATCH heartbeat" in result.stdout
