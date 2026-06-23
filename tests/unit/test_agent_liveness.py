"""Unit tests for scripts/agent_liveness.py — the ground-truth live-subagent
counter.

These exercise the six guarantees the orchestrator depends on:
  1. A transcript written within the window is counted live (window-based signal),
  2. A stale (frozen, old) transcript is NOT counted,
  3. Two consecutive ``--count`` calls on the SAME fixture set return the SAME
     number (determinism — the regression this file guards),
  4. The FLOOR_LIVE_OVERRIDE test seam short-circuits all probing,
  5. Fail-safe: a missing/unresolvable tasks dir yields 0, never raises.
  6. A recently-written transcript ending with a terminal result marker is NOT
     counted live (terminal-detection prevents completed agents from being
     over-counted due to their fresh final-write mtime).

All filesystem cases drive the counter against a pytest tmp dir via the
GLUDD_TASKS_DIR env override, so the tests are hermetic (no real session dirs).

The old "grew during probe" tests are gone — the probe-sleep approach was the
source of the 6/13/18/21 wobble and has been replaced with a dual-filter:
  - short fixed-window mtime check (GLUDD_LIVENESS_WINDOW_SEC, default 25s)
  - terminal-detection: last-line JSON with type/subtype == "result" -> excluded
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

# Load scripts/agent_liveness.py by path (scripts/ is not an importable package).
_MODPATH = Path(__file__).resolve().parents[2] / "scripts" / "agent_liveness.py"
_spec = importlib.util.spec_from_file_location("agent_liveness", _MODPATH)
assert _spec and _spec.loader
agent_liveness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_liveness)


@pytest.fixture()
def tasks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp transcript dir wired in via GLUDD_TASKS_DIR; FLOOR_LIVE_OVERRIDE
    cleared so the real probe runs."""
    d = tmp_path / "tasks"
    d.mkdir()
    monkeypatch.setenv("GLUDD_TASKS_DIR", str(d))
    # Isolate workflow-transcript scanning: point GLUDD_WORKFLOW_DIRS at an
    # empty tmp dir so _workflow_transcript_files() takes the override branch
    # (line 170: `if override:`) and globs an empty dir → returns [].  Without
    # this the dev machine's real ~/.claude transcript tree leaks in and
    # inflates counts (238 real transcripts != the 1 the tests expect).
    wf = tmp_path / "workflows"
    wf.mkdir()
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(wf))
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    return d


def _write(path: Path, data: str) -> None:
    path.write_text(data)


def _age_file(path: Path, secs: float) -> None:
    """Backdate a file's mtime by ``secs`` seconds."""
    past = time.time() - secs
    os.utime(path, (past, past))


# --- 1. window-based live signal --------------------------------------------

def test_recent_file_counts_live(tasks_dir: Path) -> None:
    """A transcript written within the window is live."""
    f = tasks_dir / "agent1.output"
    _write(f, "running")
    # File is freshly written — mtime is essentially now.
    live, total, resolved = agent_liveness.live_count(window=120.0)
    assert total == 1
    assert resolved == str(tasks_dir)
    assert live == 1, "a recently-written transcript must count as live"


def test_window_boundary_includes_just_inside(tasks_dir: Path) -> None:
    """A file aged just inside the window boundary is still live."""
    f = tasks_dir / "borderline.output"
    _write(f, "x")
    _age_file(f, 60.0)  # within 120s window
    live, _, _ = agent_liveness.live_count(window=120.0)
    assert live == 1


# --- 2. stale (frozen + old) file is not counted ----------------------------

def test_stale_file_not_counted(tasks_dir: Path) -> None:
    f = tasks_dir / "done.output"
    _write(f, "completed transcript")
    _age_file(f, 10_000)  # far past any window
    live, total, _ = agent_liveness.live_count(window=120.0)
    assert total == 1
    assert live == 0, "a frozen, old transcript must not be counted live"


def test_tail_boundary_excludes_just_past(tasks_dir: Path) -> None:
    """A file just OUTSIDE the window decays out -> not live."""
    f = tasks_dir / "decayed.output"
    _write(f, "x")
    _age_file(f, 80.0)
    live, _, _ = agent_liveness.live_count(window=75.0)
    assert live == 0


def test_mixed_fleet_counts_only_live(tasks_dir: Path) -> None:
    live_f = tasks_dir / "live.output"
    stale_f = tasks_dir / "stale.output"
    quiet_f = tasks_dir / "quiet.output"
    _write(live_f, "s")
    _age_file(live_f, 10_000)   # outside window
    _write(stale_f, "done")
    _age_file(stale_f, 10_000)  # outside window
    _write(quiet_f, "recent")
    _age_file(quiet_f, 3.0)     # within 30s window

    live, total, _ = agent_liveness.live_count(window=30.0)
    assert total == 3
    assert live == 1, "only the quiet-within-window file is live; stale ones are not"


# --- 3. DETERMINISM — two consecutive calls return the same count -----------

def test_consecutive_count_calls_are_identical(tasks_dir: Path) -> None:
    """Two back-to-back --count calls on the same fixture set must return the
    same integer. This is the regression guard for the 6/13/18/21 wobble caused
    by the old probe-sleep approach."""
    # Mix of live and stale files to make the count non-trivial.
    for i in range(3):
        f = tasks_dir / f"live{i}.output"
        _write(f, f"running {i}")
        _age_file(f, 5.0)  # within 120s window

    for i in range(2):
        f = tasks_dir / f"stale{i}.output"
        _write(f, f"done {i}")
        _age_file(f, 10_000)  # outside window

    live1, total1, _ = agent_liveness.live_count(window=120.0)
    live2, total2, _ = agent_liveness.live_count(window=120.0)

    assert total1 == 5
    assert total2 == 5
    assert live1 == live2 == 3, (
        f"consecutive live_count calls must agree; got {live1} then {live2}"
    )


def test_consecutive_main_count_calls_are_identical(
    tasks_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two back-to-back main(['--count']) calls must print the same number.
    Uses GLUDD_LIVENESS_WINDOW_SEC to pin the window so the test is hermetic."""
    monkeypatch.setenv("GLUDD_LIVENESS_WINDOW_SEC", "120.0")
    for i in range(4):
        f = tasks_dir / f"agent{i}.output"
        _write(f, f"work {i}")
        _age_file(f, 10.0)  # fresh enough for 120s window

    agent_liveness.main(["--count"])
    out1 = capsys.readouterr().out.strip()

    agent_liveness.main(["--count"])
    out2 = capsys.readouterr().out.strip()

    assert out1 == out2 == "4", (
        f"--count must be deterministic; first={out1!r} second={out2!r}"
    )


# --- 4. FLOOR_LIVE_OVERRIDE seam --------------------------------------------

def test_override_seam_count(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("FLOOR_LIVE_OVERRIDE", "7")
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "7"


def test_override_seam_human_readable(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("FLOOR_LIVE_OVERRIDE", "3")
    rc = agent_liveness.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "FLOOR_LIVE_OVERRIDE=3" in out
    assert "test seam" in out


def test_override_non_digit_ignored(
    tasks_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A non-digit override is ignored (does not short-circuit); empty dir -> 0."""
    monkeypatch.setenv("FLOOR_LIVE_OVERRIDE", "notanumber")
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0"


# --- 5. fail-safe on missing / unresolvable dir -----------------------------

def test_missing_dir_resolves_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
    wf = tmp_path / "wf"
    wf.mkdir()
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(wf))
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    assert agent_liveness._tasks_dir() is None


def test_missing_dir_live_count_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
    wf = tmp_path / "wf"
    wf.mkdir()
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(wf))
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    live, total, resolved = agent_liveness.live_count(window=120.0)
    assert (live, total, resolved) == (0, 0, None)


def test_main_count_failsafe_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
    wf = tmp_path / "wf"
    wf.mkdir()
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(wf))
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0"


def test_main_failsafe_on_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """If live_count raises, main() must print 0 and exit 0, never traceback."""
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)

    def boom(*_a, **_k):
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(agent_liveness, "live_count", boom)
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0"


def test_main_failsafe_human_readable(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_liveness, "live_count", boom)
    rc = agent_liveness.main([])
    assert rc == 0
    assert "failing safe" in capsys.readouterr().out


def test_empty_dir_zero(tasks_dir: Path, capsys) -> None:
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0"


def test_override_env_dir_used_over_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """GLUDD_TASKS_DIR (when a real dir) is preferred over session autodetect."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("GLUDD_TASKS_DIR", d)
        assert agent_liveness._tasks_dir() == d


# --- 6. terminal-detection: completed transcript not counted ---

def test_completed_transcript_not_counted(tasks_dir: Path) -> None:
    """A recently-written transcript with a terminal result marker must NOT be counted live."""
    import json
    f = tasks_dir / "completed.output"
    lines = [
        json.dumps({"type": "assistant", "content": "working..."}),
        json.dumps({"type": "result", "subtype": "success", "result": "done"}),
    ]
    f.write_text("\n".join(lines) + "\n")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 0, "a recently-written but terminal transcript must not be counted live"


def test_running_transcript_counted(tasks_dir: Path) -> None:
    """A recently-written transcript ending with a non-terminal line IS counted live."""
    import json
    f = tasks_dir / "running.output"
    lines = [
        json.dumps({"type": "assistant", "content": "thinking..."}),
        json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "make test"}}),
    ]
    f.write_text("\n".join(lines) + "\n")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 1, "a recently-written non-terminal transcript must be counted live"


def test_completed_transcript_old_also_not_counted(tasks_dir: Path) -> None:
    """A stale terminal transcript is not counted (both filters agree)."""
    import json
    f = tasks_dir / "old_done.output"
    f.write_text(json.dumps({"type": "result", "subtype": "success"}) + "\n")
    _age_file(f, 10_000)
    live, _, _ = agent_liveness.live_count(window=25.0)
    assert live == 0


def test_terminal_detection_subtype_result(tasks_dir: Path) -> None:
    """Terminal detection works on last-line JSON with subtype=result."""
    import json
    f = tasks_dir / "subtype_done.output"
    f.write_text(json.dumps({"type": "system", "subtype": "result", "content": "ok"}) + "\n")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 0


def test_terminal_detection_unparseable_last_line_failopen(tasks_dir: Path) -> None:
    """If last line is not valid JSON, terminal detection fails open (agent assumed running)."""
    f = tasks_dir / "corrupt.output"
    f.write_bytes(b"partial line or binary garbage\x00\xff\n")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 1, "unparseable last line -> fail-open, count as live"


def test_empty_output_file_failopen(tasks_dir: Path) -> None:
    """An empty .output file (agent just started) is fail-open counted as live."""
    f = tasks_dir / "empty.output"
    f.write_text("")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 1, "empty file -> no terminal marker -> fail-open live"
