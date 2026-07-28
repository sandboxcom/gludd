from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_task_ledger.py"


def _run_checker(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_tasks_md_missing_exits_1(tmp_path: Path) -> None:
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "TASKS.md not found" in result.stdout


def test_tasks_md_no_session_exits_1(tmp_path: Path) -> None:
    f = tmp_path / "TASKS.md"
    f.write_text("# Archived Phases\n")
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "lacks 'Current Session'" in result.stdout


def test_tasks_md_valid_passes(tmp_path: Path) -> None:
    f = tmp_path / "TASKS.md"
    f.write_text("# Archived\n## Current Session\n- [ ] test\n")
    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "OK: TASKS.md current" in result.stdout
