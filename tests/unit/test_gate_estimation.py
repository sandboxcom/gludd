"""Smoke tests for the gate progress estimation module."""

from __future__ import annotations

from pathlib import Path

from general_ludd.gate.estimation import estimate_progress


def _write_log(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_estimate_progress_completed_log_reports_100(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    report = estimate_progress(log, [], now=1_800_000_000.0)
    assert report.percent_complete == 100.0
    assert report.current_label is None
