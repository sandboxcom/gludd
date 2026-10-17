"""Smoke tests for the gate progress render module."""

from __future__ import annotations

from general_ludd.gate._types import ProgressReport
from general_ludd.gate.render import render_progress


def test_render_progress_emits_one_line() -> None:
    report = ProgressReport(
        current_label="unit-1a1:batch-001",
        elapsed_seconds=60.0,
        estimated_total_seconds=120.0,
        estimated_remaining_seconds=60.0,
        percent_complete=50.0,
        completed_segments=5,
        total_segments=10,
        remaining_known=True,
    )
    line = render_progress(report, status="RUNNING (pid=1)", next_poll_secs=120.0)
    assert "\n" not in line
    assert "50.0% of gate complete" in line
    assert "next: keep working; next poll in ~120s" in line
