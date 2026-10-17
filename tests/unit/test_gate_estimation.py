"""Smoke tests for the gate progress estimation module."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from general_ludd.gate.estimation import (
    MIN_SEGMENT_SECONDS,
    _average_durations,
    _average_segment_seconds,
    _elapsed_seconds,
    _fallback_average,
    _starting_report,
    _template_sequence,
    _terminal_report,
    estimate_progress,
)


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


def test_estimate_progress_total_eta_floor_for_large_pipeline(tmp_path: Path) -> None:
    """A partial, fast historical run must not make a large pipeline look <1h."""
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
        "=== GATE: FAILED ===\n",
    )
    # Current run has many shards/batches so the total pipeline is large.
    shard_lines = "\n".join(
        f"=== GATE TEST SHARD {i}/60: shard-{i:02d} files=2 batches=2 max-files=32 ===" for i in range(1, 61)
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n" + shard_lines + "\nSERIAL-SHARD-PHASE phase=shard-01:batch-001:setup result=0\n",
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 5, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 30, tzinfo=UTC).timestamp()
    os.utime(historical, (hist_start, hist_end))
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    assert report.total_segments >= 120
    # Floor guarantees at least 30s per segment -> >1h for a large pipeline.
    assert report.estimated_total_seconds >= 3600.0


def test_estimate_progress_total_eta_reflects_historical_average(tmp_path: Path) -> None:
    """When historical runs are complete, the estimate uses their average speed."""
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0\n"
        "SHARD-PASS shard=unit-1a1 rc=0\n"
        "=== GATE: PASSED ===\n",
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    # Historical run took 240s for 4 segments -> 60s/segment.
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 4, 0, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 15, tzinfo=UTC).timestamp()
    os.utime(historical, (hist_start, hist_end))
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    # 4 segments at 60s -> average drives a total longer than the 30s floor.
    assert report.estimated_total_seconds >= 120.0


def test_estimate_progress_segment_fields_are_distinct(tmp_path: Path) -> None:
    """Segment remaining/percent must be a subset of the total, not identical."""
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/2: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0\n"
        "SHARD-PASS shard=unit-1a1 rc=0\n"
        "=== GATE TEST SHARD 2/2: unit-1b files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1b:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1b:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1b batch=1 rc=0\n"
        "SERIAL-SHARD-PHASE phase=unit-1b:batch-002:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1b:batch-002 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1b batch=2 rc=0\n"
        "SHARD-PASS shard=unit-1b rc=0\n"
        "=== GATE: PASSED ===\n",
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/2: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "=== GATE TEST SHARD 2/2: unit-1b files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 1, 0, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 15, tzinfo=UTC).timestamp()
    os.utime(historical, (hist_start, hist_end))
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    # Segment remaining is only the active batch; total remaining is the whole
    # pipeline projection, so the segment portion must be smaller.
    assert report.segment_remaining_seconds < report.estimated_remaining_seconds
    # The two percentage fields are independently computed and must differ when
    # more than one segment remains.
    assert report.segment_percent_complete != report.percent_complete
    # Negative: total percent is pipeline-wide, not a copy of segment percent.
    assert 0.0 < report.percent_complete < 100.0


def test_estimate_progress_total_percent_is_pipeline_wide(tmp_path: Path) -> None:
    """Total percent must be driven by the whole pipeline, not only the active segment."""
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0\n"
        "SHARD-PASS shard=unit-1a1 rc=0\n"
        "=== GATE: PASSED ===\n",
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 1, 0, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 45, tzinfo=UTC).timestamp()
    os.utime(historical, (hist_start, hist_end))
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    # Active batch is ~75% complete, but total pipeline percent must be lower.
    assert report.segment_percent_complete > 50.0
    assert report.percent_complete < report.segment_percent_complete
    # Negative: pipeline-wide percent should never equal segment-only percent when
    # more than one segment exists.
    assert report.percent_complete != report.segment_percent_complete


def test_estimate_progress_no_history_uses_segment_floor(tmp_path: Path) -> None:
    """Without historical runs the total projection uses the per-segment floor."""
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 10, tzinfo=UTC).timestamp()
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [], now=now)
    # preflights, test, active batch, remaining batch -> 4 segments * 30s floor.
    assert report.total_segments == 4
    assert report.estimated_total_seconds >= 120.0
    assert report.percent_complete == 50.0  # 2 of 4 segments completed


def test_estimate_progress_active_phase_falls_back_to_zero_average(tmp_path: Path) -> None:
    """An active phase (no colon) with no historical average yields 0 active avg."""
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=1 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n"
        "OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0\n"
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
        "SHARD-PASS shard=unit-1a1 rc=0\n"
        "=== GATE: FAILED ===\n",
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n=== GATE PHASE: preflights ===\n",
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 30, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 5, tzinfo=UTC).timestamp()
    os.utime(historical, (hist_start, hist_end))
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    assert report.current_label == "preflights"
    assert report.segment_percent_complete == 0.0
    assert report.total_segments == 3  # active preflights + test + unit-1a1:batch-001


def test_estimate_progress_starting_run_with_no_template(tmp_path: Path) -> None:
    """A brand-new run with no template reports zero percent and unknown remaining."""
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n",
    )
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 5, tzinfo=UTC).timestamp()
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [], now=now)
    assert report.current_label == "starting"
    assert report.percent_complete == 0.0
    assert report.total_segments == 0
    assert report.remaining_known is False


def test_fallback_average_prefers_shard_then_global_then_default() -> None:
    """_fallback_average resolves same-shard -> any-batch -> default."""
    averages = {
        "unit-1a1:batch-001": 10.0,
        "unit-1a1:batch-002": 20.0,
    }
    # Exact label matches are handled by the caller via averages.get(); the helper
    # itself aggregates within the shard when the label is not present.
    assert _fallback_average("unit-1a1:batch-003", averages) == 15.0
    assert _fallback_average("unit-1b:batch-001", averages) == 15.0
    assert _fallback_average("preflights", averages) == 0.0
    assert _fallback_average("lint", {}) == 0.0
    assert _fallback_average("unit-2a:batch-001", {}) == 60.0


def test_average_segment_seconds_floor_and_empty() -> None:
    """_average_segment_seconds floors small averages and handles missing history."""
    from general_ludd.gate._types import GateEvent, GateRun

    assert _average_segment_seconds([]) == MIN_SEGMENT_SECONDS

    run = GateRun(
        path=Path("/tmp/gate.log"),
        events=[GateEvent(kind="phase", label="test", start_line=1, end_line=2)],
        total_lines=2,
        start_time=0.0,
        end_time=10.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="FAILED",
        resume_batches=0,
    )
    assert _average_segment_seconds([run]) == MIN_SEGMENT_SECONDS

    fast_run = GateRun(
        path=Path("/tmp/gate.log"),
        events=[
            GateEvent(kind="phase", label="a", start_line=1, end_line=2),
            GateEvent(kind="phase", label="b", start_line=2, end_line=3),
        ],
        total_lines=3,
        start_time=0.0,
        end_time=10.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="FAILED",
        resume_batches=0,
    )
    assert _average_segment_seconds([fast_run]) == MIN_SEGMENT_SECONDS

    slow_run = GateRun(
        path=Path("/tmp/gate.log"),
        events=[
            GateEvent(kind="phase", label="a", start_line=1, end_line=2),
            GateEvent(kind="phase", label="b", start_line=2, end_line=3),
        ],
        total_lines=3,
        start_time=0.0,
        end_time=200.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="FAILED",
        resume_batches=0,
    )
    assert _average_segment_seconds([slow_run]) > MIN_SEGMENT_SECONDS


def test_average_durations_and_template_sequence() -> None:
    """_average_durations and _template_sequence handle empty and partial inputs."""
    from general_ludd.gate._types import GateEvent, GateRun

    assert _average_durations([]) == {}
    assert _template_sequence([]) == []

    run_a = GateRun(
        path=Path("/tmp/a.log"),
        events=[
            GateEvent(kind="phase", label="preflights", start_line=1, end_line=2),
            GateEvent(kind="phase", label="test", start_line=2, end_line=3),
        ],
        total_lines=3,
        start_time=0.0,
        end_time=10.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="FAILED",
        resume_batches=0,
    )
    run_b = GateRun(
        path=Path("/tmp/b.log"),
        events=[
            GateEvent(kind="phase", label="preflights", start_line=1, end_line=2),
            GateEvent(kind="phase", label="test", start_line=2, end_line=3),
            GateEvent(kind="batch", label="unit-1:batch-001", start_line=3, end_line=4),
        ],
        total_lines=4,
        start_time=0.0,
        end_time=20.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="FAILED",
        resume_batches=0,
    )
    assert set(_average_durations([run_a, run_b]).keys()) == {"preflights", "test", "unit-1:batch-001"}
    assert _template_sequence([run_a, run_b]) == ["preflights", "test", "unit-1:batch-001"]


def test_elapsed_seconds_prefers_heartbeat_and_wall_time() -> None:
    """_elapsed_seconds uses heartbeats when present, otherwise wall time."""
    from general_ludd.gate._types import GateRun, Heartbeat

    base: dict = dict(
        path=Path("/tmp/gate.log"),
        events=[],
        total_lines=1,
        start_time=1000.0,
        end_time=None,
        shards=[],
        lines=[],
        terminal=None,
        resume_batches=0,
    )
    run_no_hb = GateRun(**base, heartbeats=[])
    assert _elapsed_seconds(run_no_hb, now=1100.0) == 100.0

    run_with_hb = GateRun(**base, heartbeats=[Heartbeat(line=1, elapsed=250.0)])
    assert _elapsed_seconds(run_with_hb, now=1100.0) == 250.0


def test_terminal_and_starting_reports() -> None:
    """_terminal_report and _starting_report produce the expected shapes."""
    from general_ludd.gate._types import GateEvent, GateRun

    run = GateRun(
        path=Path("/tmp/gate.log"),
        events=[GateEvent(kind="phase", label="test", start_line=1, end_line=2)],
        total_lines=2,
        start_time=0.0,
        end_time=10.0,
        heartbeats=[],
        shards=[],
        lines=[],
        terminal="PASSED",
        resume_batches=0,
    )
    terminal = _terminal_report(run, elapsed=10.0)
    assert terminal.percent_complete == 100.0
    assert terminal.total_segments == 1

    starting_known = _starting_report(elapsed=5.0, has_template=True)
    assert starting_known.remaining_known is True
    assert starting_known.percent_complete == 0.0

    starting_unknown = _starting_report(elapsed=5.0, has_template=False)
    assert starting_unknown.remaining_known is False


def test_estimate_progress_active_batch_without_history_falls_back_to_default(tmp_path: Path) -> None:
    """An active batch with no history uses the 60s fallback for remaining time."""
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=1 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 10, tzinfo=UTC).timestamp()
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [], now=now)
    # Fallback average is 60s; some elapsed time is already inside the active segment.
    assert 0.0 < report.segment_remaining_seconds <= 60.0
    assert report.segment_percent_complete > 0.0
