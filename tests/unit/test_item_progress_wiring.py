"""Real Make and observer wiring for item-progress controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_work_item_public_target_forwards_explicit_controls() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "work-item",
         "WORK_ITEM_ACTION=focus", "WORK_ITEM_ID=S98.4",
         "WORK_ITEM_REASON=direct acceptance repair", "WORK_ITEM_VALIDATE_ONLY=1"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/item_progress.py" in result.stdout
    assert "--task" in result.stdout and "S98.4" in result.stdout
    assert "--validate-only" in result.stdout


def test_watched_public_target_enforces_existing_item_admission() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "run-watched",
         "CMD=make help", "WORK_ITEM_ID=S98.4", "OBSERVED_LABEL=contract",
         "RUN_ID=contract", "STALL_SECS=60", "MAX_SECS=90",
         "LOG=.gate-logs/contract.log", "OBSERVED_ROOT=.gate-logs/observed",
         "OBSERVED_HEARTBEAT_SECS=10", "OBSERVED_RETAIN_RUNS=2"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--item-progress" in result.stdout
    assert "--item-task" in result.stdout and "S98.4" in result.stdout
