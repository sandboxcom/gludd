"""Tests for gate progress/ETA estimation."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from general_ludd.gate.progress import (
    GateRun,
    ProgressReport,
    cooldown_remaining,
    discover_gate_logs,
    estimate_progress,
    failure_summary,
    parse_gate_log,
    record_cooldown,
    render_progress,
)


def _write_log(tmp_path: Path, name: str, content: str, mtime: float | None = None) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(content).lstrip())
    if mtime is not None:
        path.touch()
    return path


def test_script_main_refuses_rapid_repolls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "120",
    ]
    assert main(argv) == 0
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "COOLDOWN" in captured.out
    assert "gate: FINISHED: PASSED" in captured.out
    assert captured.out.count("gate: FINISHED: PASSED") == 2


def test_script_main_force_skips_cooldown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "120",
    ]
    assert main(argv) == 0
    assert main([*argv, "--force"]) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out


def test_script_main_prints_status_and_next_action(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "0",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out
    assert "next: commit green work, push, verify remote" in captured.out


def test_script_main_sleep_after_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import time

    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "0",
        "--sleep",
        "0.2",
    ]
    start = time.monotonic()
    assert main(argv) == 0
    elapsed = time.monotonic() - start
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out
    assert elapsed >= 0.15


def test_script_main_sleep_zero_is_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "0",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out


def test_make_target_gate_progress_has_cooldown_knobs() -> None:
    from scripts.makefile_layout import compose_makefile

    makefile = compose_makefile(Path(__file__).resolve().parents[2] / "Makefile")
    idx = makefile.index("gate-progress:")
    block = makefile[idx:].split("\n\n", 1)[0]
    assert "--max-historical $(GATE_PROGRESS_MAX_HISTORICAL)" in block
    assert "--cooldown-secs $(GATE_PROGRESS_COOLDOWN_SECS)" in block
    assert "--sleep $(GATE_PROGRESS_SLEEP)" in block
    assert "$(GATE_PROGRESS_FORCE)" in block
    assert "$(GATE_PROGRESS_ARGS)" in block


def test_cooldown_refuses_rapid_polls_and_records_timestamp(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    assert cooldown_remaining(state_file, 120.0, now=1_000_000.0) == 0.0
    record_cooldown(state_file, now=1_000_000.0)
    assert cooldown_remaining(state_file, 120.0, now=1_000_010.0) == pytest.approx(110.0)
    assert cooldown_remaining(state_file, 120.0, now=1_000_130.0) == 0.0
    state_file.write_text("{corrupt")
    assert cooldown_remaining(state_file, 120.0, now=1_000_200.0) == 0.0


def test_script_main_allows_poll_when_cooldown_almost_expired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    state = tmp_path / "state.json"
    import time as _time

    state.write_text(f'{{"last_check_epoch": {_time.time() - 10.0}}}', encoding="utf-8")
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(state),
        "--cooldown-secs",
        "120",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out
    assert "COOLDOWN" in captured.out
    state.write_text(f'{{"last_check_epoch": {_time.time() - 119.5}}}', encoding="utf-8")
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: PASSED" in captured.out
    assert "COOLDOWN" not in captured.out


def test_script_main_failed_reports_phase_when_no_test_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (tmp_path / ".gate-status").write_text(
        "=== GATE 2026-10-17T11:48:52Z ===\n"
        "preflights PASS 35\n"
        "lint PASS 0\n"
        "typecheck FAIL 2\n"
        "collect PASS 0\n"
        "test NOT-STARTED validation-failed\n",
        encoding="utf-8",
    )
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: typecheck ===\n"
        "=== GATE: FAILED ===\n",
    )
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(tmp_path / "state.json"),
        "--cooldown-secs",
        "0",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: FINISHED: FAILED" in captured.out
    assert "next: fix phase typecheck FAIL 2" in captured.out
    assert "then make gate-resume" in captured.out
    assert "poll at natural breaks" not in captured.out


def test_script_main_running_output_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import os as _os

    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (tmp_path / ".gate-background.pid").write_text(str(_os.getpid()), encoding="utf-8")
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=4 max-files=32 ===\n"
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0\n",
    )
    argv = [
        "--log-dir",
        str(log_dir),
        "--current",
        str(current),
        "--state-file",
        str(tmp_path / "state.json"),
        "--cooldown-secs",
        "120",
    ]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "gate: RUNNING" in captured.out
    assert "% of gate complete" in captured.out
    assert "next: keep working; next poll in ~120s" in captured.out


def test_failure_summary_extracts_test_ids_and_error(tmp_path: Path) -> None:
    test_id = (
        "tests/unit/test_enforcement_executable_modes.py::test_enforcement_runtime_surfaces_have_executable_git_mode"
    )
    content = "\n".join(
        [
            "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd",
            "=== GATE PHASE: test ===",
            "=== GATE TEST SHARD 1/1: unit-1b files=2 batches=1 max-files=32 ===",
            "SERIAL-SHARD-PHASE phase=unit-1b:batch-014:setup result=0",
            "=========== FAILURES ===========",
            "______ test_enforcement_runtime_surfaces_have_executable_git_mode ______",
            "E   AssertionError: non-executable enforcement sources: "
            "{'.opencode/plugin/enforce-no-idle-monitoring.ts': '100644'}",
            "FAILED " + test_id,
            "OWNED-PYTEST-RESULT label=unit-1b:batch-014 rc=1",
            "SHARD-FAIL shard=unit-1b batch=14 rc=1; later-batches=not-started",
            "=== GATE: FAILED ===",
        ]
    )
    log = _write_log(tmp_path, "gate-20261017000000-00000000.log", content)
    run = parse_gate_log(log)
    summary = failure_summary(run)
    assert test_id in summary
    assert "AssertionError" in summary


def test_failure_summary_falls_back_when_no_error_line(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        FAILED tests/unit/test_some_thing.py::test_some_case
        === GATE: FAILED ===
        """,
    )
    run = parse_gate_log(log)
    summary = failure_summary(run)
    assert "tests/unit/test_some_thing.py::test_some_case" in summary


def test_render_progress_failed_directs_to_fix_not_poll() -> None:
    report = ProgressReport(
        current_label=None,
        elapsed_seconds=100.0,
        estimated_total_seconds=100.0,
        estimated_remaining_seconds=0.0,
        percent_complete=100.0,
        completed_segments=10,
        total_segments=10,
        remaining_known=True,
    )
    failed = render_progress(
        report,
        status="FINISHED: FAILED",
        failure=(
            "tests/unit/test_enforcement_executable_modes.py::test_enforcement_runtime_"
            "surfaces_have_executable_git_mode (AssertionError: non-executable "
            "enforcement sources)"
        ),
    )
    assert "next: fix tests/unit/test_enforcement_executable_modes.py" in failed
    assert "then make gate-resume" in failed
    assert "poll at natural breaks" not in failed


def test_parse_gate_log_captures_resume_recover_batch_count(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: test ===\n"
        "RESUME-RECOVER candidate_sha=d4702ce2ce994a1c6e105b067e28f450248f0b08 batches=24\n",
    )
    run = parse_gate_log(log)
    assert run.resume_batches == 24


def test_estimate_progress_resume_aware_percent(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=4 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-003:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-003 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=3 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-004:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-004 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=4 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE: FAILED ===
        """,
    )
    current_plain = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        "[gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=4 max-files=32 ===\n",
    )
    current_resume = _write_log(
        tmp_path,
        "gate-20261017010000-33333333.log",
        "[gate-background] launcher run_id=20261017T010000Z-33333333 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE PHASE: test ===\n"
        "RESUME-RECOVER candidate_sha=d4702ce2ce994a1c6e105b067e28f450248f0b08 batches=3\n"
        "=== GATE TEST SHARD 1/1: unit-1a1 files=2 batches=4 max-files=32 ===\n",
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 40, tzinfo=UTC).timestamp()
    import os

    os.utime(historical, (hist_start, hist_end))
    now = datetime(2026, 10, 17, 1, 0, 10, tzinfo=UTC).timestamp()
    plain = estimate_progress(current_plain, [historical], now=now)
    resumed = estimate_progress(current_resume, [historical], now=now)
    assert resumed.estimated_remaining_seconds < plain.estimated_remaining_seconds
    assert resumed.percent_complete > plain.percent_complete


def test_parse_gate_log_extracts_phases_and_batches(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: lint ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/2: unit-1a1 files=10 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        tests/unit/test_foo.py . [100%]
        ============================== 1 passed in 1.00s ===============================
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE TEST SHARD 2/2: unit-1b files=10 batches=1 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1b:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1b:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1b batch=1 rc=0
        SHARD-PASS shard=unit-1b rc=0
        === GATE PHASE: smoke ===
        === GATE: PASSED ===
        """,
    )
    run = parse_gate_log(log)
    assert isinstance(run, GateRun)
    assert run.terminal == "PASSED"
    assert run.total_lines > 0
    phase_labels = {e.label for e in run.events if e.kind == "phase"}
    assert phase_labels >= {"preflights", "lint", "test", "smoke"}
    batch_labels = {e.label for e in run.events if e.kind == "batch"}
    assert batch_labels == {"unit-1a1:batch-001", "unit-1a1:batch-002", "unit-1b:batch-001"}


def test_discover_gate_logs_sorts_by_mtime_newest_first(tmp_path: Path) -> None:
    names = [
        "gate-20261017030000-aaaaaaaa.log",
        "gate-20261017010000-bbbbbbbb.log",
        "gate-20261017020000-cccccccc.log",
    ]
    for i, name in enumerate(names):
        path = _write_log(tmp_path, name, f"log {i}\n")
        mtime = 1_800_000_000 + (i * 3600)
        path.write_text(path.read_text())
        import os

        os.utime(path, (mtime, mtime))
    found = discover_gate_logs(tmp_path)
    assert [p.name for p in found] == [
        "gate-20261017020000-cccccccc.log",
        "gate-20261017010000-bbbbbbbb.log",
        "gate-20261017030000-aaaaaaaa.log",
    ]


def test_render_progress_includes_status_prefix() -> None:
    report = ProgressReport(
        current_label="unit-1a1:batch-002",
        elapsed_seconds=30.0,
        estimated_total_seconds=120.0,
        estimated_remaining_seconds=90.0,
        percent_complete=25.0,
        completed_segments=3,
        total_segments=12,
        remaining_known=True,
    )
    plain = render_progress(report)
    assert plain.startswith("gate: unit-1a1:batch-002 | 25.0% of gate complete |")
    with_status = render_progress(report, status="RUNNING (pid=123)")
    assert with_status.startswith("gate: RUNNING (pid=123) | unit-1a1:batch-002 |")
    assert "25.0% of gate complete" in with_status
    assert "next: keep working; poll at natural breaks" in with_status
    with_wait = render_progress(report, status="RUNNING (pid=123)", next_poll_secs=120.0)
    assert "next: keep working; next poll in ~120s" in with_wait
    assert "poll at natural breaks" not in with_wait


def test_render_progress_suggests_next_action_for_terminal_states() -> None:
    report = ProgressReport(
        current_label=None,
        elapsed_seconds=100.0,
        estimated_total_seconds=100.0,
        estimated_remaining_seconds=0.0,
        percent_complete=100.0,
        completed_segments=10,
        total_segments=10,
        remaining_known=True,
    )
    passed = render_progress(report, status="FINISHED: PASSED")
    assert "next: commit green work, push, verify remote" in passed
    failed = render_progress(report, status="FINISHED: FAILED")
    assert "next: gate-tail the failure, fix root cause, gate-resume" in failed
    idle = render_progress(report, status="IDLE (no running gate)")
    assert "next: gate-background (or gate-resume if a prior gate failed)" in idle


def test_estimate_progress_completed_run(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE: PASSED ===
        """,
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        """
        [gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE: PASSED ===
        """,
    )
    start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    end = datetime(2026, 10, 17, 1, 0, 30, tzinfo=UTC).timestamp()
    current.touch()
    import os

    os.utime(current, (end, end))
    os.utime(historical, (start, start))
    report = estimate_progress(current, [historical], now=end)
    assert isinstance(report, ProgressReport)
    assert report.percent_complete >= 99.0
    assert report.estimated_remaining_seconds == pytest.approx(0.0, abs=1.0)


def test_estimate_progress_running_run(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE: FAILED ===
        """,
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        """
        [gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        """,
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 20, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 12, tzinfo=UTC).timestamp()
    import os

    os.utime(historical, (hist_start, hist_end))
    current.touch()
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    assert report.current_label == "unit-1a1:batch-002"
    assert report.completed_segments == 3  # preflights, test phase, batch-001
    assert report.estimated_remaining_seconds > 0
    assert report.remaining_known is True
    assert 0 < report.percent_complete < 100


def test_estimate_progress_synthesizes_batches_beyond_template(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=3 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        === GATE: FAILED ===
        """,
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        """
        [gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=3 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        """,
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 20, tzinfo=UTC).timestamp()
    cur_start = datetime(2026, 10, 17, 1, 0, 0, tzinfo=UTC).timestamp()
    now = datetime(2026, 10, 17, 1, 0, 4, tzinfo=UTC).timestamp()
    import os

    os.utime(historical, (hist_start, hist_end))
    current.touch()
    os.utime(current, (cur_start, cur_start))
    report = estimate_progress(current, [historical], now=now)
    assert report.current_label == "unit-1a1:batch-001"
    assert report.remaining_known is True
    assert report.estimated_remaining_seconds > 0
    assert 0 < report.percent_complete < 100


def test_estimate_progress_uses_heartbeat_elapsed(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        [gate-background-watch] heartbeat run_id=20261017T000000Z-00000000 pid=1 elapsed=0.0s remaining=21600.0s
        === GATE PHASE: preflights ===
        [gate-background-watch] heartbeat run_id=20261017T000000Z-00000000 pid=1 elapsed=10.0s remaining=21600.0s
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        [gate-background-watch] heartbeat run_id=20261017T000000Z-00000000 pid=1 elapsed=12.0s remaining=21600.0s
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        [gate-background-watch] heartbeat run_id=20261017T000000Z-00000000 pid=1 elapsed=18.0s remaining=21600.0s
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        [gate-background-watch] heartbeat run_id=20261017T000000Z-00000000 pid=1 elapsed=20.0s remaining=21600.0s
        === GATE: PASSED ===
        """,
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        """
        [gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        [gate-background-watch] heartbeat run_id=20261017T010000Z-11111111 pid=1 elapsed=30.0s remaining=21600.0s
        """,
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 20, tzinfo=UTC).timestamp()
    import os

    os.utime(historical, (hist_start, hist_end))
    report = estimate_progress(
        current,
        [historical],
        now=datetime(2026, 10, 17, 1, 0, 30, tzinfo=UTC).timestamp(),
    )
    assert report.elapsed_seconds == pytest.approx(30.0)
    assert report.current_label == "unit-1a1:batch-002"
    assert 0 < report.percent_complete < 100


def test_estimate_progress_wall_time_beats_stale_heartbeat(tmp_path: Path) -> None:
    historical = _write_log(
        tmp_path,
        "gate-20261017000000-00000000.log",
        """
        [gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-002 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=2 rc=0
        SHARD-PASS shard=unit-1a1 rc=0
        === GATE: FAILED ===
        """,
    )
    current = _write_log(
        tmp_path,
        "gate-20261017010000-11111111.log",
        """
        [gate-background] launcher run_id=20261017T010000Z-11111111 namespace=gludd
        [gate-background-watch] heartbeat run_id=20261017T010000Z-11111111 pid=1 elapsed=30.0s remaining=21600.0s
        === GATE PHASE: preflights ===
        === GATE PHASE: test ===
        === GATE TEST SHARD 1/1: unit-1a1 files=2 batches=2 max-files=32 ===
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:setup result=0
        OWNED-PYTEST-RESULT label=unit-1a1:batch-001 rc=0
        SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0
        SERIAL-SHARD-PHASE phase=unit-1a1:batch-002:setup result=0
        """,
    )
    hist_start = datetime(2026, 10, 17, 0, 0, 0, tzinfo=UTC).timestamp()
    hist_end = datetime(2026, 10, 17, 0, 0, 20, tzinfo=UTC).timestamp()
    import os

    os.utime(historical, (hist_start, hist_end))
    report = estimate_progress(
        current,
        [historical],
        now=datetime(2026, 10, 17, 1, 10, 0, tzinfo=UTC).timestamp(),
    )
    assert report.elapsed_seconds == pytest.approx(600.0)
    assert 0 < report.percent_complete < 100
