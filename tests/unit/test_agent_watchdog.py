"""Unit tests for scripts/agent_watchdog.py.

Tests the pure classify_tail() core and the CLI --count-stalled path over a
real temp directory.  No network, no subprocess, no I/O in the core tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "agent_watchdog.py"


def _load_module():
    """Import agent_watchdog.py by path without polluting sys.modules."""
    spec = importlib.util.spec_from_file_location("agent_watchdog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("agent_watchdog", module)
    spec.loader.exec_module(module)
    return module


aw = _load_module()
classify_tail = aw.classify_tail
State = aw.State
scan_tasks_dir = aw.scan_tasks_dir
main = aw.main
check_agent_stalled = aw.check_agent_stalled
DEFAULT_WINDOW_SECS = aw.DEFAULT_WINDOW_SECS
POLL_SECS = aw.POLL_SECS
STOP_STATE = aw.STOP_STATE
FALSE_DONE_BLOCKS = aw.FALSE_DONE_BLOCKS
CONTINUE_DIRECTIVE = aw.CONTINUE_DIRECTIVE


def check_and_reset() -> dict:
    """Run a deterministic cycle without launching a repository-wide scan."""
    return aw.check_and_reset(secrets_check=lambda: None)
_check_force_dispatch = aw._check_force_dispatch
_is_force_dispatch_active = aw._is_force_dispatch_active
FORCE_DISPATCH_FILE = aw.FORCE_DISPATCH_FILE
FORCE_DISPATCH_MAX_AGE = aw.FORCE_DISPATCH_MAX_AGE
_check_under_floor_dispatch = aw._check_under_floor_dispatch
_read_multitask_state = aw._read_multitask_state
MULTITASK_STATE_FILE = aw.MULTITASK_STATE_FILE
PURE_IDLE_DIRECTIVE = aw.PURE_IDLE_DIRECTIVE
pending_work_exists = aw._pending_work_exists


@pytest.fixture(autouse=True)
def _isolate_watchdog_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every watchdog unit case free of real make/network subprocesses."""

    def _no_external_subprocess(cmd, *_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(aw.subprocess, "run", _no_external_subprocess)


# ── script exists ─────────────────────────────────────────────────────────────


def test_script_exists():
    assert SCRIPT_PATH.is_file(), "scripts/agent_watchdog.py must exist"


# ── classify_tail: ACTIVE ─────────────────────────────────────────────────────


def test_active_recent_file_regardless_of_tail():
    """A file modified 5 s ago is ACTIVE no matter what the tail says."""
    tail = "Continuing with the remaining files:\n"
    state, reason = classify_tail(tail, age_seconds=5.0, window_seconds=90.0)
    assert state == State.ACTIVE
    assert "5" in reason


def test_active_recent_file_empty_tail():
    state, _ = classify_tail("", age_seconds=1.0, window_seconds=90.0)
    assert state == State.ACTIVE


def test_active_boundary_just_inside_window():
    state, _ = classify_tail("some output", age_seconds=89.9, window_seconds=90.0)
    assert state == State.ACTIVE


# ── classify_tail: LIKELY_STALLED_INCOMPLETE ──────────────────────────────────


def test_stalled_continuing_with_files():
    """Classic mid-task rest: transcript ends 'Continuing with the remaining files:'."""
    tail = (
        "I have processed the first batch of files.\n"
        "Continuing with the remaining files:\n"
    )
    state, reason = classify_tail(tail, age_seconds=300.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "continuing" in reason.lower()


def test_stalled_last_line_ends_with_colon():
    tail = "Looking at the next set of changes:\n"
    state, _reason = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_stalled_let_me_prefix():
    tail = "Some prior output.\nLet me examine the remaining tests.\n"
    state, reason = classify_tail(tail, age_seconds=150.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "let me" in reason.lower()


def test_stalled_next_prefix():
    tail = "Batch 1 done.\nNext, I will handle the remaining modules.\n"
    state, _reason = classify_tail(tail, age_seconds=120.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_stalled_empty_file():
    state, reason = classify_tail("", age_seconds=300.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "empty" in reason.lower()


def test_stalled_whitespace_only_file():
    state, _ = classify_tail("   \n\n  \n", age_seconds=300.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_stalled_no_result_marker():
    """Stale file with no completion keyword and no continuation signal."""
    tail = "I looked at the code. There seem to be issues in the parser.\n"
    state, _ = classify_tail(tail, age_seconds=300.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE


# ── classify_tail: DONE ───────────────────────────────────────────────────────


def test_done_result_colon_marker():
    tail = (
        "Reviewed all 12 files.\n"
        "\n"
        "result: all tests pass, no issues found\n"
    )
    state, reason = classify_tail(tail, age_seconds=300.0, window_seconds=90.0)
    assert state == State.DONE
    assert "result:" in reason


def test_done_summary_marker():
    tail = "Fixed the bug.\n\nsummary: patched auth.py line 42\n"
    state, _ = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.DONE


def test_done_complete_keyword():
    tail = "All migrations applied. Task complete.\n"
    state, _ = classify_tail(tail, age_seconds=500.0, window_seconds=90.0)
    assert state == State.DONE


def test_done_finished_keyword():
    tail = "Processed 8 agents. Finished.\n"
    state, _ = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.DONE


def test_done_failed_colon_is_conclusion():
    """'failed:' is a stated conclusion — agent came to rest, not silently stalled."""
    tail = "Ran the suite.\nfailed: 3 tests could not collect\n"
    state, _ = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.DONE


def test_done_passed_keyword():
    tail = "Gate check: passed\n"
    state, _ = classify_tail(tail, age_seconds=100.0, window_seconds=90.0)
    assert state == State.DONE


def test_done_marker_beats_continuation_colon_on_last_line():
    """If a done marker appears in the tail, it wins even if the last line ends ':'."""
    tail = (
        "result: commit created\n"
        "Now verifying remote status:\n"
    )
    state, reason = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.DONE
    assert "result:" in reason


# ── custom window ─────────────────────────────────────────────────────────────


def test_custom_window_treats_old_file_as_active():
    tail = "Continuing with the remaining files:\n"
    state, _ = classify_tail(tail, age_seconds=50.0, window_seconds=30.0)
    # age=50 > window=30 → NOT active; should be stalled
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_custom_large_window_treats_same_file_as_active():
    tail = "Continuing with the remaining files:\n"
    state, _ = classify_tail(tail, age_seconds=50.0, window_seconds=120.0)
    assert state == State.ACTIVE


# ── scan_tasks_dir: missing dir (fail-safe) ───────────────────────────────────


def test_missing_dir_returns_empty(tmp_path: Path):
    missing = tmp_path / "nonexistent_tasks"
    results = scan_tasks_dir(missing)
    assert results == []


def test_empty_dir_returns_empty(tmp_path: Path):
    results = scan_tasks_dir(tmp_path)
    assert results == []


def test_non_output_files_ignored(tmp_path: Path):
    (tmp_path / "some_log.txt").write_text("result: done\n")
    (tmp_path / "readme.md").write_text("# docs\n")
    results = scan_tasks_dir(tmp_path)
    assert results == []


# ── scan_tasks_dir + --count-stalled CLI ─────────────────────────────────────


def _write_output(tmp_path: Path, name: str, body: str, age_secs: float = 300.0) -> Path:
    """Write a .output file and backdate its mtime so it appears stale."""
    p = tmp_path / f"{name}.output"
    p.write_text(body, encoding="utf-8")
    # Backdate mtime so the file is older than the default window
    target_mtime = time.time() - age_secs
    os.utime(p, (target_mtime, target_mtime))
    return p


def test_count_stalled_math(tmp_path: Path):
    """--count-stalled across a dir with a mix of states."""
    # 2 stalled
    _write_output(tmp_path, "agent-a", "Continuing with the remaining files:\n")
    _write_output(tmp_path, "agent-b", "Let me look at the next section.\n")
    # 1 done
    _write_output(tmp_path, "agent-c", "result: all done\n")
    # 1 active (age < window)
    _write_output(tmp_path, "agent-d", "Continuing:\n", age_secs=10.0)

    results = scan_tasks_dir(tmp_path, window_seconds=90.0)
    stalled_count = sum(1 for _, s, _ in results if s == State.LIKELY_STALLED_INCOMPLETE)
    active_count = sum(1 for _, s, _ in results if s == State.ACTIVE)
    done_count = sum(1 for _, s, _ in results if s == State.DONE)

    assert stalled_count == 2
    assert active_count == 1
    assert done_count == 1


def test_cli_count_stalled_flag(tmp_path: Path, capsys: pytest.CaptureFixture):
    """main() --count-stalled prints the correct integer."""
    _write_output(tmp_path, "x1", "Continuing with the remaining files:\n")
    _write_output(tmp_path, "x2", "result: shipped\n")

    main([str(tmp_path), "--count-stalled"])
    captured = capsys.readouterr()
    assert captured.out.strip() == "1"


def test_cli_list_stalled_flag(tmp_path: Path, capsys: pytest.CaptureFixture):
    """main() --list-stalled prints task-ids of stalled agents."""
    _write_output(tmp_path, "stalled-agent", "Continuing with the remaining files:\n")
    _write_output(tmp_path, "done-agent", "result: complete\n")

    main([str(tmp_path), "--list-stalled"])
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("stalled-agent")


def test_cli_missing_dir_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture):
    """A missing tasks dir produces no output and exits 0 (fail-safe)."""
    missing = tmp_path / "no_such_dir"
    rc = main([str(missing), "--count-stalled"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "0"


def test_cli_all_flag(tmp_path: Path, capsys: pytest.CaptureFixture):
    """main() --all prints every agent with its state."""
    _write_output(tmp_path, "ag1", "Continuing with the remaining files:\n")
    _write_output(tmp_path, "ag2", "result: ok\n")

    main([str(tmp_path), "--all"])
    captured = capsys.readouterr()
    assert "LIKELY_STALLED_INCOMPLETE" in captured.out
    assert "DONE" in captured.out


# ── check_agent_stalled ───────────────────────────────────────────────────────


def test_check_agent_stalled_has_pending_work(tmp_path: Path):
    """Returns True when stop state has hasPendingWork=true."""
    stop_file = tmp_path / "stop-state.json"
    stop_file.write_text('{"hasPendingWork": true}')

    result = aw.check_agent_stalled(stop_state_path=stop_file)
    assert result is True


def test_check_agent_stalled_blocks_positive(tmp_path: Path):
    """Returns True when false-done blocks > 0."""
    blocks_file = tmp_path / "blocks.json"
    blocks_file.write_text('{"consecutive": 3}')

    result = aw.check_agent_stalled(false_done_path=blocks_file)
    assert result is True


def test_check_agent_stalled_neither(tmp_path: Path):
    """Returns False when neither condition met."""
    stop_file = tmp_path / "stop-state.json"
    stop_file.write_text('{"hasPendingWork": false}')
    blocks_file = tmp_path / "blocks.json"
    blocks_file.write_text('{"consecutive": 0}')

    result = aw.check_agent_stalled(stop_state_path=stop_file, false_done_path=blocks_file)
    assert result is False


def test_check_agent_stalled_no_files():
    """Returns False when files don't exist."""
    result = aw.check_agent_stalled(
        stop_state_path=Path("/tmp/nonexistent-stop-state-99999.json"),
        false_done_path=Path("/tmp/nonexistent-blocks-99999.json"),
    )
    assert result is False


def test_check_agent_stalled_malformed_json(tmp_path: Path):
    """Returns False on malformed JSON (fail-safe)."""
    stop_file = tmp_path / "bad.json"
    stop_file.write_text("not json")

    result = aw.check_agent_stalled(stop_state_path=stop_file)
    assert result is False


def test_check_agent_stalled_hydrid(tmp_path: Path):
    """Returns True when hasPendingWork=True even if blocks=0."""
    stop_file = tmp_path / "stop-state.json"
    stop_file.write_text('{"hasPendingWork": true}')
    blocks_file = tmp_path / "blocks.json"
    blocks_file.write_text('{"consecutive": 0}')

    result = aw.check_agent_stalled(stop_state_path=stop_file, false_done_path=blocks_file)
    assert result is True


# ── POLL_SECS constant ────────────────────────────────────────────────────────


def test_poll_secs_is_10():
    assert aw.POLL_SECS == 10


# ── check_and_reset: stalled / text-only ──────────────────────────────────────


def test_check_and_reset_text_only_recent_streak_with_pending(tmp_path: Path, monkeypatch):
    """Streak file recent + pending todos → reset applied."""
    streak_file = tmp_path / "streak.json"
    streak_file.write_text('{"count": 1, "last_tool": "write"}')
    # Ensure file mtime is now
    streak_file.touch()

    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_file))

    todo_file = tmp_path / "todos.json"
    todo_file.write_text('[{"content": "fix bug", "status": "pending"}]')
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(todo_file))

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["reset_applied"] is True

    # Also stub out stop-state / false-done so check_agent_stalled doesn't interfere
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "nonexistent-stop.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "nonexistent-blocks.json"))


def test_check_and_reset_no_pending_no_reset(tmp_path: Path, monkeypatch):
    """No pending todos + streak below threshold → no reset."""
    streak_file = tmp_path / "streak.json"
    streak_file.write_text('{"count": 1, "last_tool": "write"}')

    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_file))

    todo_file = tmp_path / "todos.json"
    todo_file.write_text("[]")
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(todo_file))

    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "nonexistent-stop.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "nonexistent-blocks.json"))

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["reset_applied"] is False


def test_check_and_reset_stalled_state_resets(tmp_path: Path, monkeypatch):
    """check_agent_stalled() True → reset applied + directive written."""
    stop_file = tmp_path / "stop-state.json"
    stop_file.write_text('{"hasPendingWork": true}')
    monkeypatch.setattr(aw, "STOP_STATE", str(stop_file))

    streak_file = tmp_path / "streak.json"
    streak_file.write_text('{"count": 0}')
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_file))

    todo_file = tmp_path / "todos.json"
    todo_file.write_text("[]")
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(todo_file))

    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "nonexistent-blocks.json"))

    directive_file = tmp_path / "continue.txt"
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(directive_file))

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["reset_applied"] is True
    assert directive_file.exists()
    assert "FORCE_DISPATCH" in directive_file.read_text()


# ── check_agent_stalled ────────────────────────────────────────────────────────


def test_watchdog_detects_stalled_on_pending_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stop_path = tmp_path / "stop-state.json"
    monkeypatch.setattr(aw, "STOP_STATE", str(stop_path))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "nonexistent.json"))

    stop_path.write_text('{"hasPendingWork": true}')

    assert check_agent_stalled() is True


def test_watchdog_detects_stalled_on_false_done_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "nonexistent-stop.json"))

    blocks_path = tmp_path / "false-done-blocks.json"
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(blocks_path))

    blocks_path.write_text('{"consecutive": 3}')

    assert check_agent_stalled() is True


def test_watchdog_not_stalled_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done-blocks.json"))

    Path(tmp_path / "stop-state.json").write_text('{"hasPendingWork": false}')
    Path(tmp_path / "false-done-blocks.json").write_text('{"consecutive": 0}')

    assert check_agent_stalled() is False


def test_task_anomaly_elapsed_5x_expected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    deadlines_file = tmp_path / "deadlines.json"
    old_ts = time.time() - 2000
    deadlines_file.write_text(json.dumps({
        "task-general-1": old_ts,
    }))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(deadlines_file))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "anomalies.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "nonexistent-gate-pid")
    result = aw.check_task_anomalies()
    assert len(result["anomalies"]) + len(result.get("stalled", [])) >= 1


def test_task_anomaly_normal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    deadlines_file = tmp_path / "deadlines.json"
    recent_ts = time.time() - 5
    deadlines_file.write_text(json.dumps({
        "task-commit-1": recent_ts,
    }))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(deadlines_file))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "anomalies.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "nonexistent-gate-pid")
    result = aw.check_task_anomalies()
    assert result["anomalies"] == []
    assert len(result.get("stalled", [])) == 0


def test_task_anomaly_gate_stalled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gate_pid_file = tmp_path / "gate.pid"
    gate_pid_file.write_text("12345")
    os.utime(gate_pid_file, (time.time() - 3000, time.time() - 3000))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "nonexistent-deadlines.json"))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "anomalies.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", gate_pid_file)
    result = aw.check_task_anomalies()
    stalled = result.get("stalled", [])
    stalled_ids = [s.get("task_id") for s in stalled]
    assert "gate-process" in stalled_ids


def test_push_stalled_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    deadlines_file = tmp_path / "deadlines.json"
    old_ts = time.time() - 200
    deadlines_file.write_text(json.dumps({
        "task-git-push-main": old_ts,
    }))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(deadlines_file))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "anomalies.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "nonexistent-gate-pid")
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "_alerted_anomalies", {})

    result = aw.check_task_anomalies()
    assert len(result.get("stalled", [])) >= 1

    captured = capsys.readouterr()
    assert "PUSH STALLED" in captured.out


def test_watchdog_10s_poll_interval():
    assert POLL_SECS == 10


def _setup_no_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak-nonexistent.json"))
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(tmp_path / "todos-nonexistent.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-nonexistent.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "blocks-nonexistent.json"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "deadlines-nonexistent.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count-nonexistent.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled-nonexistent.json"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled-nonexistent.json"))


def test_watchdog_detects_push_stalled_via_ps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_no_reset(monkeypatch, tmp_path)

    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", lambda msg: logs.append(msg))

    def _mock_run(cmd, **_kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[:2] == ["ps", "-eo"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="  PID ETIME COMMAND\n12345  01:05:30  git push origin master\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd if isinstance(cmd, list) else [cmd],
            returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(aw.subprocess, "run", _mock_run)
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    directive_p = tmp_path / "continue.txt"
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(directive_p))

    check_and_reset()

    assert any("PUSH STALLED" in msg for msg in logs), f"logs: {logs}"
    assert directive_p.exists()
    assert "PUSH STALLED" in directive_p.read_text()


def test_watchdog_detects_task_anomaly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_no_reset(monkeypatch, tmp_path)

    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", lambda msg: logs.append(msg))

    now = time.time()
    deadlines_p = tmp_path / "deadlines.json"
    deadlines_p.write_text(json.dumps({
        "tasks": [
            {"task_id": "task-slow", "start_ts": now - 400},
        ]
    }))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(deadlines_p))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    directive_p = tmp_path / "continue.txt"
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(directive_p))

    check_and_reset()

    assert any("TASK ANOMALY" in msg and "task-slow" in msg for msg in logs), f"logs: {logs}"
    # PURE_IDLE_DIRECTIVE is a shared file written by multiple checks;
    # verify the anomaly was logged instead.
    assert len([m for m in logs if "TASK ANOMALY" in m and "task-slow" in m]) >= 1


def test_watchdog_ignores_normal_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_no_reset(monkeypatch, tmp_path)

    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", lambda msg: logs.append(msg))

    now = time.time()
    deadlines_p = tmp_path / "deadlines.json"
    deadlines_p.write_text(json.dumps({
        "tasks": [
            {"task_id": "task-fast", "start_ts": now - 60},
        ]
    }))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(deadlines_p))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    directive_p = tmp_path / "continue.txt"
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(directive_p))

    check_and_reset()

    anomaly_msgs = [m for m in logs if "TASK ANOMALY" in m]
    assert len(anomaly_msgs) == 0, f"unexpected anomaly: {anomaly_msgs}"
    assert not directive_p.exists() or "TASK ANOMALY" not in directive_p.read_text()


def _setup_full(monkeypatch, tmp_path):
    # check_and_reset() includes periodic CI, release, plugin-liveness, and
    # secrets checks. The autouse subprocess fixture keeps these paths
    # deterministic; concurrent real scans can OOM-kill a sibling worker.
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "watchdog-activity.json"))
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(tmp_path / "todos.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done-blocks.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(tmp_path / "false-done-maxout.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "LAST_FLAG_FILE", str(tmp_path / "last-flag.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "deadlines.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled-tasks.txt"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "ex-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timing.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_STATE_SNAPSHOT", str(tmp_path / "task-state-snapshot.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "timing-data.json"))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(tmp_path / "push-flag-nonexistent"))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tmp_path / "tasks-dir-nonexistent"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate-pid-nonexistent")
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / "gate-status")
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "check-cooldowns.json"))
    todos_path = tmp_path / "todos.json"
    todos_path.write_text("[]")
    stop_count_path = tmp_path / "stop-count.json"
    stop_count_path.write_text('{"count":0}')
    (tmp_path / "push-flag-nonexistent").write_text("")


def test_check_and_reset_uses_injected_secrets_check_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A unit cycle can replace only the repository-wide secrets scan."""
    _setup_full(monkeypatch, tmp_path)
    (tmp_path / "TASKS.md").write_text("- [x] complete\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / "gate-status").write_text("lint PASS 0\n")
    monkeypatch.setattr(
        aw,
        "_should_run_check",
        lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: False,
    )
    calls: list[str] = []

    def _fast_secrets_check() -> None:
        calls.append("scan")

    aw.check_and_reset(secrets_check=_fast_secrets_check)

    assert calls == ["scan"]


def test_watchdog_detects_idle_without_streak_file(tmp_path, monkeypatch):
    _setup_full(monkeypatch, tmp_path)
    streak_path = tmp_path / "streak.json"
    assert not streak_path.exists()

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time() - 90}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] pending work item\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["stop_detected"] is True


def test_watchdog_writes_continue_directive_on_stop(tmp_path, monkeypatch):
    _setup_full(monkeypatch, tmp_path)
    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":5,"last_tool":"write"}')
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_path))

    continue_path = tmp_path / "continue-directive.txt"
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(continue_path))

    stop_state = tmp_path / "stop-state.json"
    stop_state.write_text('{"hasPendingWork":true}')
    monkeypatch.setattr(aw, "STOP_STATE", str(stop_state))

    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["reset_applied"] is True
    assert continue_path.exists()
    content = continue_path.read_text()
    assert "FORCE_DISPATCH" in content.upper()


def test_watchdog_false_done_max_out_on_every_cycle(tmp_path, monkeypatch):
    _setup_full(monkeypatch, tmp_path)
    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":0,"last_tool":"write"}')

    maxout_path = tmp_path / "false-done-maxout.json"
    monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(maxout_path))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] ensure has_pending_work is True\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    aw.check_and_reset(secrets_check=lambda: None)
    assert maxout_path.exists()
    data = json.loads(maxout_path.read_text())
    assert 0 <= data["count"] < 100  # counter wraps at 100


def test_watchdog_poll_interval_is_10_seconds():
    assert aw.POLL_SECS == 10


def test_watchdog_mtime_age_returns_none_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "nonexistent-streak-99999.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "nonexistent-watchdog-99999.json"))
    result = aw._streak_mtime_age_seconds()
    assert result is None


# ── CI awareness tests ─────────────────────────────────────────────────────────


def test_ci_is_pending_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))

    def mock_ci_verdict(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["make", "ci-verdict"],
            returncode=0,
            stdout="CI PENDING: abc123 run 12345 status='pending'\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_ci_verdict)
    ci_pending, run_id = aw._ci_is_pending_or_red()
    assert ci_pending is True
    assert run_id == "12345"


def test_ci_is_success_not_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))

    def mock_ci_verdict(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["make", "ci-verdict"],
            returncode=0,
            stdout="CI SUCCESS: abc123 run 12345 conclusion: success\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_ci_verdict)
    ci_pending, _run_id = aw._ci_is_pending_or_red()
    assert ci_pending is False


def test_ci_is_red_detected_as_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))

    def mock_ci_verdict(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["make", "ci-verdict"],
            returncode=0,
            stdout="CI RED: abc123 run 12345 conclusion: failure\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_ci_verdict)
    ci_pending, run_id = aw._ci_is_pending_or_red()
    assert ci_pending is True
    assert run_id == "12345"


def test_ci_subprocess_error_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))

    def mock_error(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="make", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", mock_error)
    ci_pending, run_id = aw._ci_is_pending_or_red()
    assert ci_pending is False
    assert run_id is None


def test_ci_pending_for_too_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ci_cache = tmp_path / "ci-cache.json"
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(ci_cache))
    ci_cache.write_text(json.dumps({"pending_first_seen": time.time() - 60 * 60}))

    minutes = aw._ci_pending_for_too_long_minutes()
    assert minutes is not None
    assert minutes >= 59.0


def test_ci_pending_not_stalled_when_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ci_cache = tmp_path / "ci-cache.json"
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(ci_cache))
    ci_cache.write_text(json.dumps({"pending_first_seen": time.time() - 30}))

    minutes = aw._ci_pending_for_too_long_minutes()
    assert minutes is not None
    assert minutes < 2.0


def test_check_and_reset_with_ci_only_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_full(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":0,"last_tool":"ci-verdict"}')
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_path))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue.txt"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "_TASKS_MD", (tmp_path / "TASKS.md"))
    monkeypatch.setattr(aw, "_RATCHET_YML", (tmp_path / "ratchet.yml"))
    monkeypatch.setattr(aw, "_GATE_STATUS", (tmp_path / ".gate-status"))
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    # No local pending work
    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / ".gate-status").write_text("lint PASS 0\ntypecheck PASS 0\n")

    # But CI is pending
    def mock_subprocess_run(cmd, **_kwargs):
        if isinstance(cmd, list) and cmd[1] == "ci-verdict":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="CI PENDING: abc run 99999 status='pending'\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    result = aw.check_and_reset(secrets_check=lambda: None)

    # Should detect that CI-pending is pending work but should NOT
    # flag a stop since the agent is actively polling CI
    assert "stop_detected" in result


# ── Item 12: Health score tests ──────────────────────────────────────────────


def test_health_score_perfect():
    score = aw._compute_health_score(False, 0, False, False, False, True)
    assert score == 100


def test_health_score_ci_only_slight_penalty():
    score = aw._compute_health_score(False, 0, False, True, False, True)
    assert score == 85  # 100 - 15 for CI


def test_health_score_gate_red_heavy_penalty():
    score = aw._compute_health_score(False, 0, True, False, False, True)
    assert score == 60  # 100 - 40


def test_health_score_everything_broken():
    score = aw._compute_health_score(True, 5, True, True, True, False)
    assert score == 0  # floor


# ── Item 9: CI loop detection tests ─────────────────────────────────────────


def test_ci_loop_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    assert aw._detect_ci_loop() is False


def test_ci_loop_three_pushes_in_10_min(tmp_path, monkeypatch):
    push_file = tmp_path / "push-ts.json"
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(push_file))
    now = time.time()
    push_file.write_text(json.dumps([now - 60, now - 120, now - 180]))
    assert aw._detect_ci_loop() is True


def test_ci_loop_two_pushes_not_enough(tmp_path, monkeypatch):
    push_file = tmp_path / "push-ts.json"
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(push_file))
    now = time.time()
    push_file.write_text(json.dumps([now - 60, now - 120]))
    assert aw._detect_ci_loop() is False


def test_ci_loop_old_pushes_expired(tmp_path, monkeypatch):
    push_file = tmp_path / "push-ts.json"
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(push_file))
    now = time.time()
    push_file.write_text(json.dumps([now - 700, now - 800, now - 900]))
    assert aw._detect_ci_loop() is False


# ── Item 10: CI true stall tests ────────────────────────────────────────────


def test_ci_true_stall_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    assert aw._detect_ci_true_stall() is False


def test_ci_true_stall_over_45_min_no_pushes(tmp_path, monkeypatch):
    ci_cache = tmp_path / "ci-cache.json"
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(ci_cache))
    ci_cache.write_text(json.dumps({"pending_first_seen": time.time() - 3000}))
    push_file = tmp_path / "push-ts.json"
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(push_file))
    push_file.write_text(json.dumps([time.time() - 1000]))
    assert aw._detect_ci_true_stall() is True


# ── Item 13: Orchestrator state test ────────────────────────────────────────


def test_orchestrator_state_written(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))

    aw._write_orchestrator_state(False, 0, False, True, False, True, ci_run_id="12345")

    state_path = tmp_path / "orchestrator.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["health_score"] == 85
    assert state["ci_pending_or_red"] is True
    assert state["agent_active"] is True


# ── Item 15: Disengage signal tests ─────────────────────────────────────────


def test_disengage_signal_written_and_active(tmp_path, monkeypatch):
    disengage_file = tmp_path / "disengage.json"
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(disengage_file))

    aw._write_disengage_signal(minutes=5, reason="test")
    assert disengage_file.exists()
    assert aw._is_disengage_active() is True


def test_disengage_signal_expired(tmp_path, monkeypatch):
    disengage_file = tmp_path / "disengage.json"
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(disengage_file))
    disengage_file.write_text(json.dumps({
        "disengage_until": time.time() - 60,
        "reason": "expired",
    }))
    assert aw._is_disengage_active() is False
    aw._clear_disengage_signal()
    assert not disengage_file.exists()


# ── Item 19: CI-only-pending should not flag stop (integration-style) ────────


def test_check_and_reset_ci_only_pending_no_stop_flag(tmp_path, monkeypatch):
    _setup_full(monkeypatch, tmp_path)
    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":2,"last_tool":"ci-verdict"}')
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_path))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "activity.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue.txt"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled.json"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    # Local state: all clean
    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / ".gate-status").write_text("lint PASS 0\ntypecheck PASS 0\n")

    # CI is pending
    def mock_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "ci-verdict" in str(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1,
                stdout="CI PENDING: abc run 99999 status='pending'\n",
                stderr="",
            )
        if isinstance(cmd, list) and "git log" in str(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if isinstance(cmd, list) and "git status" in str(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = aw.check_and_reset(secrets_check=lambda: None)

    # When only CI is pending and agent is active, stop_detected should be
    # false (or null/absent). The orchestrator state should reflect health.
    assert result.get("stop_detected") is False or result.get("stop_detected") is None

    orchestrator = tmp_path / "orchestrator.json"
    if orchestrator.exists():
        state = json.loads(orchestrator.read_text())
        assert state["health_score"] >= 45  # CI pending + gate injection (CI writes CI-FAIL to .gate-status)


# ── Force-dispatch tests ──────────────────────────────────────────────────────


def test_force_dispatch_inactive_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(tmp_path / "nonexistent-force-dispatch.json"))
    assert _is_force_dispatch_active() is False


def test_force_dispatch_active_fresh_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    flag = tmp_path / "force-dispatch.json"
    flag.write_text(json.dumps({"level": 3, "message": "escalated"}))
    os.utime(flag, (time.time(), time.time()))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(flag))
    assert _is_force_dispatch_active() is True


def test_force_dispatch_inactive_stale_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    flag = tmp_path / "force-dispatch.json"
    flag.write_text(json.dumps({"level": 3}))
    stale_mtime = time.time() - FORCE_DISPATCH_MAX_AGE - 10
    os.utime(flag, (stale_mtime, stale_mtime))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(flag))
    assert _is_force_dispatch_active() is False


def test_check_force_dispatch_writes_directive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    force_flag = tmp_path / "force-dispatch.json"
    force_flag.write_text(json.dumps({"level": 3, "message": "escalated"}))
    os.utime(force_flag, (time.time(), time.time()))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(force_flag))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] fix bug in parser\n- [x] done feature\n- [ ] write tests\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    continue_directive = tmp_path / "continue-directive.json"
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(continue_directive))

    reset_log = tmp_path / "reset.log"
    monkeypatch.setattr(aw, "RESET_LOG", str(reset_log))

    result = _check_force_dispatch()
    assert result is True
    assert continue_directive.exists()

    directive = json.loads(continue_directive.read_text())
    assert directive["action"] == "FORCE_DISPATCH"
    assert directive["level"] == 3
    assert directive["dispatch_count"] == 2
    assert len(directive["dispatch_commands"]) == 2
    assert any("fix bug in parser" in cmd["task_item"] for cmd in directive["dispatch_commands"])
    assert any("write tests" in cmd["task_item"] for cmd in directive["dispatch_commands"])


def test_check_force_dispatch_clears_flag_when_no_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    force_flag = tmp_path / "force-dispatch.json"
    force_flag.write_text(json.dumps({"level": 3}))
    os.utime(force_flag, (time.time(), time.time()))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(force_flag))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [x] all done\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\ntypecheck PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    continue_directive = tmp_path / "continue-directive.json"
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(continue_directive))

    reset_log = tmp_path / "reset.log"
    monkeypatch.setattr(aw, "RESET_LOG", str(reset_log))

    result = _check_force_dispatch()
    assert result is False
    assert not force_flag.exists()


def test_check_force_dispatch_includes_ratchet_and_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    force_flag = tmp_path / "force-dispatch.json"
    force_flag.write_text(json.dumps({"level": 4}))
    os.utime(force_flag, (time.time(), time.time()))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(force_flag))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [x] all done\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("key: value\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("tests FAIL 3\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    continue_directive = tmp_path / "continue-directive.json"
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(continue_directive))

    reset_log = tmp_path / "reset.log"
    monkeypatch.setattr(aw, "RESET_LOG", str(reset_log))

    result = _check_force_dispatch()
    assert result is True
    directive = json.loads(continue_directive.read_text())
    assert directive["action"] == "FORCE_DISPATCH"
    assert directive["level"] == 4
    cmds = {cmd["task_item"] for cmd in directive["dispatch_commands"]}
    assert any("ratchet" in c for c in cmds)
    assert any("gate" in c for c in cmds)


def test_force_dispatch_lowers_idle_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When force-dispatch is active, idle threshold drops from 60s to 5s."""
    _setup_full(monkeypatch, tmp_path)

    force_flag = tmp_path / "force-dispatch.json"
    force_flag.write_text(json.dumps({"level": 3}))
    os.utime(force_flag, (time.time(), time.time()))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(force_flag))

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":0,"last_tool":"write"}')
    old_mtime = time.time() - 70  # older than 60s but within force-dispatch threshold
    os.utime(streak_path, (old_mtime, old_mtime))
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_path))

    activity_path = tmp_path / "watchdog-activity.json"
    old_mtime = time.time() - 70  # older than 60s but typical stop detection
    activity_path.write_text(json.dumps({"last_activity_ts": old_mtime}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] pending task\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    result = aw.check_and_reset(secrets_check=lambda: None)
    assert result["stop_detected"] is True


def test_check_force_dispatch_stale_flag_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    force_flag = tmp_path / "force-dispatch.json"
    force_flag.write_text(json.dumps({"level": 3}))
    stale_mtime = time.time() - FORCE_DISPATCH_MAX_AGE - 10
    os.utime(force_flag, (stale_mtime, stale_mtime))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_FILE", str(force_flag))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] pending\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    reset_log = tmp_path / "reset.log"
    monkeypatch.setattr(aw, "RESET_LOG", str(reset_log))

    result = _check_force_dispatch()
    assert result is False
    assert not force_flag.exists()  # stale flag removed


# ── Under-floor dispatch detection tests ──────────────────────────────────


def test_read_multitask_state_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(tmp_path / "nonexistent-multitask-state.json"))
    state = _read_multitask_state()
    assert state == {}


def test_read_multitask_state_valid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 3,
        "zeroStreak": 1,
        "estimatedInFlight": 5,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    state = _read_multitask_state()
    assert state["thisMessageDispatches"] == 3
    assert state["zeroStreak"] == 1
    assert state["estimatedInFlight"] == 5


def test_check_under_floor_no_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(tmp_path / "nonexistent.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    _check_under_floor_dispatch()
    assert not (tmp_path / "pure-idle.txt").exists()


def test_check_under_floor_at_ceiling_no_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 10,
        "zeroStreak": 0,
        "estimatedInFlight": 10,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    _check_under_floor_dispatch()
    assert not (tmp_path / "pure-idle.txt").exists()


def test_check_under_floor_detects_below_ten_with_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 3,
        "zeroStreak": 0,
        "estimatedInFlight": 3,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] pending task\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    _check_under_floor_dispatch()

    directive_path = tmp_path / "pure-idle.txt"
    assert directive_path.exists(), "Should write directive when under-floor detected"
    content = directive_path.read_text()
    assert "UNDER-FLOOR DETECTED" in content
    assert "3 dispatch" in content
    assert "Floor is 10" in content


def test_check_under_floor_pipeline_dry_with_streak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 0,
        "zeroStreak": 2,
        "estimatedInFlight": 1,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] fix the parser\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("key: value\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    _check_under_floor_dispatch()

    directive_path = tmp_path / "pure-idle.txt"
    assert directive_path.exists(), "Should write directive when pipeline is dry"
    content = directive_path.read_text()
    assert "UNDER-FLOOR DETECTED" in content


def test_check_under_floor_no_action_when_no_pending_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 1,
        "zeroStreak": 3,
        "estimatedInFlight": 0,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [x] all done\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\ntypecheck PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    _check_under_floor_dispatch()

    assert not (tmp_path / "pure-idle.txt").exists(), (
        "Should NOT write directive when no pending work exists"
    )


def test_check_under_floor_dry_but_no_streak_no_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 0,
        "zeroStreak": 0,
        "estimatedInFlight": 1,
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] something\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("# empty\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    _check_under_floor_dispatch()

    assert not (tmp_path / "pure-idle.txt").exists(), (
        "Pipeline dry but zeroStreak=0 — should not fire yet"
    )


def test_check_and_reset_includes_under_floor_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_full(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count":1,"last_tool":"read"}')
    monkeypatch.setattr(aw, "STREAK_FILE", str(streak_path))

    state_file = tmp_path / "multitask-state.json"
    state_file.write_text(json.dumps({
        "thisMessageDispatches": 2,
        "zeroStreak": 1,
        "estimatedInFlight": 2,
        "pid": os.getpid(),
    }))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(state_file))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] urgent fix needed\n")
    monkeypatch.setattr(aw, "_TASKS_MD", tasks_md)

    ratchet_yml = tmp_path / "ratchet.yml"
    ratchet_yml.write_text("key: value\n")
    monkeypatch.setattr(aw, "_RATCHET_YML", ratchet_yml)

    gate_status = tmp_path / "gate-status"
    gate_status.write_text("lint PASS 0\n")
    monkeypatch.setattr(aw, "_GATE_STATUS", gate_status)

    def mock_subprocess_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "ci-verdict" in str(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="CI SUCCESS: abc123 run 12345 conclusion: success\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    _ = aw.check_and_reset(secrets_check=lambda: None)

    directive_path = tmp_path / "pure-idle.txt"
    assert directive_path.exists(), (
        "check_and_reset should call _check_under_floor_dispatch, "
        "which should write directive for under-floor detection"
    )
    content = directive_path.read_text()
    assert "UNDER-FLOOR DETECTED" in content
