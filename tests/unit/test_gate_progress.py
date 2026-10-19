"""Tests for gate progress/ETA estimation."""

from __future__ import annotations

import os
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



def test_progress_without_logs_refuses_to_invent_gate_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    assert main([
        "--log-dir", str(log_dir), "--state-file", str(tmp_path / "cooldown.json"),
        "--cooldown-secs", "0",
    ]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "No gate logs found.\n"
    assert list(log_dir.iterdir()) == []
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / ".gate-background.pid").exists()


@pytest.mark.parametrize(
    "error_type", [None, ProcessLookupError, PermissionError],
    ids=["no-owner", "exited-owner", "permission-refused"],
)
def test_progress_keeps_missing_or_unobservable_owners_idle_without_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], error_type: type[OSError] | None,
) -> None:
    from scripts import gate_progress

    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    current = _write_log(
        log_dir, "gate-20261019000000-owned.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=_pytest-runtime-compatibility-admission\n",
    )
    if error_type is not None:
        (tmp_path / ".gate-background.pid").write_text("4242\n", encoding="utf-8")
    attempted: list[tuple[int, int]] = []

    def unavailable(pid: int, signal_number: int) -> None:
        attempted.append((pid, signal_number))
        assert error_type is not None
        raise error_type("fixture native PID observation unavailable")

    monkeypatch.setattr(os, "kill", unavailable)
    assert gate_progress.main([
        "--current", str(current), "--log-dir", str(log_dir), "--force",
        "--state-file", str(tmp_path / "cooldown.json"),
    ]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "IDLE (no running gate)" in captured.out
    assert "RUNNING (pid=" not in captured.out and "FINISHED: PASS" not in captured.out
    assert "0.0% of gate complete" in captured.out and "ETA unknown" in captured.out
    assert attempted == ([] if error_type is None else [(4242, 0)])


def test_phase_failure_fallback_reads_only_the_selected_existing_log(tmp_path: Path) -> None:
    from scripts.gate_progress import _first_fail_phase

    (tmp_path / ".gate-status").write_text("unrelated FAIL stale proof\n", encoding="utf-8")
    current = tmp_path / "gate-20261019000000-owned.log"
    assert _first_fail_phase(current) is None
    current.write_text(
        "=== GATE PHASE: admission ===\nwork-item: missing native focus\n",
        encoding="utf-8",
    )
    assert _first_fail_phase(current) is None
    selected_failure = "admission FAIL owner=disk-cleanup-preflight log=.gate-logs/owned.log"
    with current.open("a", encoding="utf-8") as handle:
        handle.write(selected_failure + "\n")
    assert _first_fail_phase(current) == selected_failure


@pytest.mark.parametrize("blocked", [False, True])
def test_admission_only_progress_has_no_completed_gate_proof(
    tmp_path: Path, blocked: bool,
) -> None:
    log = _write_log(
        tmp_path, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=_pytest-runtime-compatibility-admission\n"
        "[observed release-proof-runtime-admission] start run_id=runtime-123 log=.gate-logs/runtime-123.log\n"
        + (
            "FAILED tests/unit/test_fixture.py::test_actual_input\n"
            "E AssertionError: effective native input differs\n"
            "PROOF-ADMISSION-BLOCKED phase=_pytest-runtime-compatibility-admission rc=2\n"
            "admission FAIL owner=_pytest-runtime-compatibility-admission "
            "reason=owner-exit-2 log=.gate-logs/admission.log\n"
            "=== GATE: FAILED ===\n" if blocked else ""
        ),
    )
    run = parse_gate_log(log)
    assert run.admission is not None
    assert run.admission.owner == "_pytest-runtime-compatibility-admission"
    assert run.admission.state == ("blocked" if blocked else "running")
    assert run.admission.run_id == "runtime-123"
    assert run.admission.log_path == ".gate-logs/runtime-123.log"
    report = estimate_progress(log, [], now=run.start_time + 10)
    assert report.percent_complete == 0
    assert report.completed_segments == 0
    assert not report.remaining_known
    if blocked:
        assert "test_fixture.py::test_actual_input" in failure_summary(run)
        assert "effective native input differs" in failure_summary(run)


def test_admission_progress_uses_selected_log_not_unrelated_gate_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.gate_progress import main

    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    (tmp_path / ".gate-status").write_text("unrelated FAIL stale-private-input\n", encoding="utf-8")
    current = _write_log(
        log_dir, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=disk-cleanup-preflight\n"
        "ERROR: owned capacity is still blocked\n"
        "PROOF-ADMISSION-BLOCKED phase=disk-cleanup-preflight rc=1\n"
        "admission FAIL owner=disk-cleanup-preflight reason=owner-exit-1 log=.gate-logs/admission-owned.log\n"
        "=== GATE: FAILED ===\n",
    )
    assert main([
        "--current", str(current), "--log-dir", str(log_dir),
        "--state-file", str(tmp_path / "cooldown.json"), "--cooldown-secs", "0",
    ]) == 0
    output = capsys.readouterr().out
    assert "disk-cleanup-preflight" in output
    assert "owned capacity is still blocked" in output
    assert "admission-owned.log" in output
    assert "0.0% of gate complete" in output and "ETA unknown" in output
    assert "stale-private-input" not in output
    assert "then make gate-resume" in output



@pytest.mark.parametrize("terminal", ["", "=== GATE: FAILED ===\n"])
def test_admission_pass_alone_never_completes_gate_proof(tmp_path: Path, terminal: str) -> None:
    log = _write_log(
        tmp_path, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false\n"
        + terminal,
    )
    run = parse_gate_log(log)
    report = estimate_progress(log, [], now=run.start_time + 10)
    assert report.percent_complete == 0 and report.completed_segments == 0
    assert not report.remaining_known



@pytest.mark.parametrize(
    ("owner", "native_line", "reason"),
    [
        ("_pytest-runtime-compatibility-admission",
         "[observed release-proof-runtime-admission] result state=timed_out rc=124 elapsed=90.6s",
         "state=timed_out rc=124"),
        ("disk-cleanup-preflight",
         "phase=recheck status=failed pass=2 reason=pass-limit disk_percent=96.0 disk_limit=95.0 disk_limit_kind=hard",
         "reason=pass-limit"),
        ("_pytest-runtime-compatibility-admission",
         "observed-command: unchanged failed inputs: release-proof-runtime-admission/prior-run; "
         "change the failing input",
         "unchanged failed inputs"),
    ],
)
def test_admission_progress_preserves_native_producer_refusal(
    tmp_path: Path, owner: str, native_line: str, reason: str,
) -> None:
    log = _write_log(
        tmp_path, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        f"PROOF-ADMISSION-START phase={owner}\n"
        "[observed release-proof-runtime-admission] start run_id=native-run log=.gate-logs/native-run.log\n"
        f"{native_line}\n"
        f"PROOF-ADMISSION-BLOCKED phase={owner} rc=2\n"
        "=== GATE: FAILED ===\n",
    )
    run = parse_gate_log(log)
    detail = failure_summary(run)
    assert owner in detail and reason in detail
    assert "native-run" in detail and ".gate-logs/native-run.log" in detail
    report = estimate_progress(log, [], now=run.start_time + 10)
    assert report.percent_complete == 0 and not report.remaining_known


def test_selected_terminal_log_is_not_overridden_by_another_live_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import gate_progress

    logs = tmp_path / ".gate-logs"
    logs.mkdir()
    (tmp_path / ".gate-background.pid").write_text("12345\n", encoding="utf-8")
    monkeypatch.setattr(gate_progress, "_pid_alive", lambda pid: True)
    log = _write_log(
        logs, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=check-resource-ownership\n"
        "ERROR: selected historical input\n"
        "PROOF-ADMISSION-BLOCKED phase=check-resource-ownership rc=2\n"
        "=== GATE: FAILED ===\n",
    )
    assert gate_progress.main([
        "--current", str(log), "--log-dir", str(logs),
        "--state-file", str(tmp_path / "cooldown.json"), "--cooldown-secs", "0",
    ]) == 0
    output = capsys.readouterr().out
    assert "FINISHED: FAILED" in output and "selected historical input" in output
    assert "RUNNING (pid=12345)" not in output



@pytest.mark.parametrize(
    "native_line",
    [
        "work-item: runtime admission requires native focus or explicit WORK_ITEM_ID; "
        "use make work-item WORK_ITEM_ACTION=focus WORK_ITEM_ID=<active-task> WORK_ITEM_VALIDATE_ONLY=0",
        "work-item: primary must name an open canonical active task",
    ],
)
def test_admission_progress_reports_focus_refusal_without_inventing_an_observer(
    tmp_path: Path, native_line: str,
) -> None:
    log = _write_log(
        tmp_path, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=_pytest-runtime-compatibility-admission\n"
        + native_line + "\n"
        "PROOF-ADMISSION-BLOCKED phase=_pytest-runtime-compatibility-admission rc=2\n"
        "admission FAIL owner=_pytest-runtime-compatibility-admission log=.gate-logs/gate-admission-owned\n"
        "=== GATE: FAILED ===\n",
    )
    run = parse_gate_log(log)
    detail = failure_summary(run)
    assert native_line in detail
    assert ".gate-logs/gate-admission-owned" in detail
    assert "run_id=" not in detail and "observed" not in detail
    assert run.admission is not None and run.admission.run_id is None
    report = estimate_progress(log, [], now=run.start_time + 10)
    assert report.percent_complete == 0 and not report.remaining_known


def test_admission_pass_does_not_erase_later_gate_failure(tmp_path: Path) -> None:
    current = _write_log(
        tmp_path, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false\n"
        "=== GATE PHASE: test ===\n"
        "FAILED tests/unit/test_fixture.py::test_coverage_and_warnings\n"
        "=== GATE: FAILED ===\n",
    )
    run = parse_gate_log(current)
    assert run.admission is not None and run.admission.state == "passed"
    assert run.terminal == "FAILED"
    assert "test_coverage_and_warnings" in failure_summary(run)


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
    import os as _os
    import time

    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (tmp_path / ".gate-background.pid").write_text(str(_os.getpid()), encoding="utf-8")
    current = _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n=== GATE PHASE: preflights ===\n",
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
    assert "gate: RUNNING" in captured.out
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



def test_native_make_progress_uses_selected_runtime_and_repaired_source_without_a_local_venv(tmp_path: Path) -> None:
    import os
    import shlex
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    (tmp_path / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
    selected_uv = tmp_path / "selected-uv"
    argv_file = tmp_path / "selected-runtime-argv"
    selected_uv.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv_file))}\n"
        '[ "$1" = run ] || exit 91\nshift\n'
        '[ "$1" = python ] || exit 92\nshift\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8",
    )
    selected_uv.chmod(0o700)
    logs = tmp_path / ".gate-logs"
    logs.mkdir()
    current = _write_log(
        logs, "gate-20261019000000-12345678.log",
        "=== GATE PHASE: admission ===\n"
        "PROOF-ADMISSION-START phase=_pytest-runtime-compatibility-admission\n"
        "observed-command: unchanged failed inputs: release-proof-runtime-admission/prior-run; "
        "change the failing input\n"
        "PROOF-ADMISSION-BLOCKED phase=_pytest-runtime-compatibility-admission rc=2\n"
        "admission FAIL owner=_pytest-runtime-compatibility-admission log=.gate-logs/gate-admission-owned\n"
        "=== GATE: FAILED ===\n",
    )
    result = subprocess.run(
        ["make", "-I", str(root), "-f", str(root / "Makefile"), "gate-progress",
         f"UV={shlex.quote(str(selected_uv))}", "GATE_PROGRESS_MAX_HISTORICAL=20",
         "GATE_PROGRESS_COOLDOWN_SECS=0", "GATE_PROGRESS_SLEEP=0", "GATE_PROGRESS_FORCE=1",
         f"GATE_PROGRESS_ARGS={shlex.join(['--current', str(current)])}"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, check=False,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / ".venv").exists()
    assert argv_file.read_text().splitlines()[:3] == ["run", "python", str(root / "scripts/gate_progress.py")]
    assert "FINISHED: FAILED" in result.stdout
    assert "unchanged failed inputs" in result.stdout and "0.0% of gate complete" in result.stdout
    assert "ETA unknown" in result.stdout and "run_id=" not in result.stdout
    assert (logs / "gate-progress-state.json").exists()


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
        "typecheck FAIL 2\n"
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
    # Resuming skips already-completed batches; those batches count toward the
    # pipeline-wide percent without inflating the total pipeline projection.
    assert resumed.percent_complete > plain.percent_complete
    assert resumed.estimated_total_seconds == pytest.approx(plain.estimated_total_seconds)
    assert resumed.estimated_remaining_seconds == pytest.approx(plain.estimated_remaining_seconds)


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
    assert plain.startswith("gate: unit-1a1:batch-002 | 25.0% of gate complete")
    assert "(0.0% of segment)" in plain
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


def test_script_main_prints_status_before_sleep(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive: status line must be emitted before the sleep delay while RUNNING."""
    import os as _os

    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (tmp_path / ".gate-background.pid").write_text(str(_os.getpid()), encoding="utf-8")
    _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n=== GATE PHASE: preflights ===\n",
    )
    sleep_calls: list[float] = []
    output_before_sleep: str | None = None

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        nonlocal output_before_sleep
        if output_before_sleep is None:
            output_before_sleep = capsys.readouterr().out

    monkeypatch.setattr("scripts.gate_progress.time.sleep", fake_sleep)
    argv = [
        "--log-dir",
        str(log_dir),
        "--state-file",
        str(tmp_path / "state.json"),
        "--cooldown-secs",
        "0",
        "--sleep",
        "5",
    ]
    assert main(argv) == 0
    assert sleep_calls == [5.0]
    assert output_before_sleep is not None
    assert "gate: RUNNING" in output_before_sleep


def test_script_main_finished_returns_immediately_despite_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative: sleep must be skipped when the gate is not RUNNING."""
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.gate_progress.time.sleep", lambda s: sleep_calls.append(s))
    argv = [
        "--log-dir",
        str(log_dir),
        "--state-file",
        str(tmp_path / "state.json"),
        "--cooldown-secs",
        "0",
        "--sleep",
        "5",
    ]
    assert main(argv) == 0
    assert sleep_calls == []


def test_script_main_zero_sleep_does_not_sleep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative: a zero sleep argument must not invoke time.sleep at all."""
    from scripts.gate_progress import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(
        log_dir,
        "gate-20261017000000-00000000.log",
        "[gate-background] launcher run_id=20261017T000000Z-00000000 namespace=gludd\n"
        "=== GATE PHASE: preflights ===\n"
        "=== GATE: PASSED ===\n",
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.gate_progress.time.sleep", lambda s: sleep_calls.append(s))
    argv = [
        "--log-dir",
        str(log_dir),
        "--state-file",
        str(tmp_path / "state.json"),
        "--cooldown-secs",
        "0",
        "--sleep",
        "0",
    ]
    assert main(argv) == 0
    assert sleep_calls == []


def test_script_main_sleep_help_text_says_after_printing(capsys: pytest.CaptureFixture[str]) -> None:
    """The --sleep help text must describe the post-print behavior."""
    from scripts.gate_progress import main

    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "Sleep this many seconds after printing status" in out
    assert "before printing status" not in out


def test_render_progress_includes_segment_eta_and_percent() -> None:
    """Positive: render output exposes both segment and total ETAs/percent."""
    report = ProgressReport(
        current_label="unit-1a1:batch-002",
        elapsed_seconds=30.0,
        estimated_total_seconds=120.0,
        estimated_remaining_seconds=90.0,
        percent_complete=25.0,
        completed_segments=3,
        total_segments=12,
        remaining_known=True,
        segment_remaining_seconds=10.0,
        segment_percent_complete=50.0,
    )
    line = render_progress(report, status="RUNNING (pid=1)", next_poll_secs=120.0)
    assert "50.0% of segment" in line
    assert "segment ETA 10s" in line
    assert "total ~2m 0s" in line
    assert "25.0% of gate complete" in line
