"""Unit tests for scripts/agent_watchdog.py.

Tests the pure classify_tail() core and the CLI --count-stalled path over a
real temp directory.  No network, no subprocess, no I/O in the core tests.
"""
from __future__ import annotations

import importlib.util
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
DEFAULT_WINDOW_SECS = aw.DEFAULT_WINDOW_SECS


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
    state, reason = classify_tail(tail, age_seconds=200.0, window_seconds=90.0)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "ends with ':'" in reason


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
    import os

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
