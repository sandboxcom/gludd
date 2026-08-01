from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_task_ledger.py"


def _run_checker(cwd: Path) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).returncode


def test_tasks_md_missing_exits_1(tmp_path: Path) -> None:
    assert _run_checker(tmp_path) != 0


def test_tasks_md_no_session_exits_1(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("# Archived Phases\n", encoding="utf-8")
    assert _run_checker(tmp_path) != 0


def test_tasks_md_valid_passes(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        "# Archived\n## Current Session\n- [ ] test\n",
        encoding="utf-8",
    )
    assert _run_checker(tmp_path) == 0
