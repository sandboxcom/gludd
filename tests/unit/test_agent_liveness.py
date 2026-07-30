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
    # (line 170: `if override:`) and globs an empty dir -> returns [].  Without
    # this the dev machine's real ~/.claude transcript tree leaks in and
    # inflates counts (238 real transcripts != the 1 the tests expect).
    wf = tmp_path / "workflows"
    wf.mkdir()
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(wf))
    # Isolate from the real opencode SQLite DB so tests are hermetic.
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    # Disable caching to avoid stale-cache pollution between tests.
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
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
    import json
    f = tasks_dir / "agent1.output"
    _write(f, json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "make test"}}))
    # File is freshly written — mtime is essentially now.
    live, total, resolved = agent_liveness.live_count(window=120.0)
    assert total == 1
    assert resolved == str(tasks_dir)
    assert live == 1, "a recently-written transcript must count as live"


def test_window_boundary_includes_just_inside(tasks_dir: Path) -> None:
    """A file aged just inside the window boundary is still live."""
    import json
    f = tasks_dir / "borderline.output"
    _write(f, json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "test.py"}}))
    _age_file(f, 60.0)  # within 120s window
    live, _, _ = agent_liveness.live_count(window=120.0)
    assert live == 1


# --- 2. stale (frozen + old) file is not counted ----------------------------

def test_stale_file_not_counted(tasks_dir: Path) -> None:
    import json
    f = tasks_dir / "done.output"
    _write(f, json.dumps({"type": "assistant", "message": {"content": "completed work"}}) + "\n")
    # Stale assistant-with-text IS terminal (no tool_use), so both mtime
    # and terminal-detection agree: not live.
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
    import json
    # live_f and stale_f: assistant-with-text is always terminal -> excluded by _is_terminal
    live_f = tasks_dir / "live.output"
    stale_f = tasks_dir / "stale.output"
    quiet_f = tasks_dir / "quiet.output"
    _write(live_f, json.dumps({"type": "assistant", "message": {"content": "working"}}))
    _age_file(live_f, 10_000)
    _write(stale_f, json.dumps({"type": "assistant", "message": {"content": "done"}}))
    _age_file(stale_f, 10_000)
    # quiet_f is non-terminal (tool_use) AND within the window -> counted live
    _write(quiet_f, json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "test"}}))
    _age_file(quiet_f, 3.0)

    live, total, _ = agent_liveness.live_count(window=30.0)
    assert total == 3
    assert live == 1, "only the quiet-within-window non-terminal file should be live"


# --- 3. DETERMINISM — two consecutive calls return the same count -----------

def test_consecutive_count_calls_are_identical(tasks_dir: Path) -> None:
    """Two back-to-back --count calls on the same fixture set must return the
    same integer. This is the regression guard for the 6/13/18/21 wobble caused
    by the old probe-sleep approach."""
    import json
    # Live files: recent + non-terminal (tool_use)
    for i in range(3):
        f = tasks_dir / f"live{i}.output"
        _write(f, json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": f"run {i}"}}))
        _age_file(f, 5.0)

    # Stale files: old mtime (or terminal assistant) -> excluded
    for i in range(2):
        f = tasks_dir / f"stale{i}.output"
        _write(f, json.dumps({"type": "assistant", "message": {"content": f"done {i}"}}))
        _age_file(f, 10_000)

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
    import json
    monkeypatch.setenv("GLUDD_LIVENESS_WINDOW_SEC", "120.0")
    for i in range(4):
        f = tasks_dir / f"agent{i}.output"
        # Non-terminal tool_use so _is_terminal returns False
        _write(f, json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": f"work {i}"}}))
        _age_file(f, 10.0)

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
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    rc = agent_liveness.main(["--count"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "0"


def test_main_failsafe_on_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """If live_count raises, main() must print 0 and exit 0, never traceback."""
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)

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
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)

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
    """If last line is not valid JSON, terminal detection fails open (agent assumed running).
    First line must pass _is_agent_transcript; last line is binary garbage which
    _is_terminal cannot parse -> fail-open non-terminal -> live."""
    import json
    f = tasks_dir / "corrupt.output"
    f.write_bytes(
        json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "work"}}).encode()
        + b"\n"
        + b"partial line or binary garbage\x00\xff\n"
    )
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 1, "unparseable last line -> fail-open, count as live"


def test_empty_output_file_failopen(tasks_dir: Path) -> None:
    """A file with only a non-terminal tool_use line should be counted live."""
    import json
    f = tasks_dir / "running.output"
    _write(f, json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "work"}}) + "\n")
    live, total, _ = agent_liveness.live_count(window=25.0)
    assert total == 1
    assert live == 1, "non-terminal tool_use -> counted as live"


# --- 7. harness detection ---------------------------------------------------

def test_detect_harness_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE_PROJECT_DIR set -> harness is claude."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/some/project")
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
    assert agent_liveness._detect_harness() == "claude"


def test_detect_harness_opencode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENCODE_SESSION_ID set -> harness is opencode."""
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_test")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert agent_liveness._detect_harness() == "opencode"


def test_detect_harness_claude_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No env vars set, but ~/.claude/projects/ exists -> claude."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
    claude_dir = tmp_path / ".claude" / "projects"
    claude_dir.mkdir(parents=True)
    monkeypatch.setattr(agent_liveness.os.path, "expanduser",
                       lambda p: str(tmp_path / p.replace("~/", "")) if p.startswith("~/") else p)
    assert agent_liveness._detect_harness() == "claude"


def test_detect_harness_opencode_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No env vars set, but ~/.local/share/opencode/ exists -> opencode."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
    oc_dir = tmp_path / ".local" / "share" / "opencode"
    oc_dir.mkdir(parents=True)
    monkeypatch.setattr(agent_liveness.os.path, "expanduser",
                       lambda p: str(tmp_path / p.replace("~/", "")) if p.startswith("~/") else p)
    assert agent_liveness._detect_harness() == "opencode"


def test_detect_harness_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """No signals present -> unknown."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
    # Ensure no ~/.claude/projects/ or ~/.local/share/opencode/ dir.
    with monkeypatch.context() as m:
        m.setattr(agent_liveness.os.path, "isdir", lambda p: False)
        assert agent_liveness._detect_harness() == "unknown"


# --- 8. opencode SQLite backend ----------------------------------------------

def test_opencode_db_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OPENCODE_DB_PATH env var takes precedence."""
    db = tmp_path / "test.db"
    db.write_text("")  # create an empty file
    monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
    assert agent_liveness._opencode_db_path() == str(db)


def test_opencode_db_path_env_override_nonexistent(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENCODE_DB_PATH pointing to nonexistent file returns None."""
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent/nope.db")
    assert agent_liveness._opencode_db_path() is None


def test_opencode_db_path_default_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var, default path doesn't exist -> None."""
    monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
    with monkeypatch.context() as m:
        m.setattr(agent_liveness.os.path, "expanduser", lambda p: "/nonexistent/opencode.db")
        assert agent_liveness._opencode_db_path() is None


def test_count_opencode_live_missing_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """No opencode DB -> count is 0."""
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent/nope.db")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    assert agent_liveness._count_opencode_live(window=60.0) == 0


def test_count_opencode_live_with_mock_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mock opencode DB with live and stale subagent sessions."""
    import sqlite3
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER
        )
    """)
    now_ms = int(time.time() * 1000)
    # A live subagent session (parent IS NOT NULL, recent time_updated)
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) "
        "VALUES ('sub1', 'proj1', 'parent1', 'slug1', '/dir', 'title', '1', ?, ?)",
        (now_ms - 5000, now_ms - 2000),
    )
    # A stale subagent session (time_updated outside window)
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) "
        "VALUES ('sub2', 'proj1', 'parent1', 'slug2', '/dir', 'title', '1', ?, ?)",
        (now_ms - 200_000, now_ms - 100_000),
    )
    # A top-level session (parent_id IS NULL) — should NOT count
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) "
        "VALUES ('top1', 'proj1', NULL, 'slug3', '/dir', 'title', '1', ?, ?)",
        (now_ms - 5000, now_ms - 1000),
    )
    con.commit()
    con.close()

    monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    count = agent_liveness._count_opencode_live(window=60.0)
    assert count == 1, "only the recent subagent with parent_id should be live; stale + top-level excluded"


def test_count_opencode_live_archived_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Subagent sessions with time_archived set are excluded."""
    import sqlite3
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER
        )
    """)
    now_ms = int(time.time() * 1000)
    # Archived subagent — should NOT count
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, "
        "time_created, time_updated, time_archived) "
        "VALUES ('arch1', 'proj1', 'parent1', 'slug', '/dir', 'title', '1', ?, ?, ?)",
        (now_ms - 5000, now_ms - 1000, now_ms - 500),
    )
    # Compacting subagent — should NOT count
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, "
        "time_created, time_updated, time_compacting) "
        "VALUES ('comp1', 'proj1', 'parent1', 'slug', '/dir', 'title', '1', ?, ?, ?)",
        (now_ms - 5000, now_ms - 1000, now_ms - 500),
    )
    con.commit()
    con.close()

    monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    count = agent_liveness._count_opencode_live(window=300.0)
    assert count == 0, "archived and compacting sessions are excluded"


# --- 9. cache layer ---------------------------------------------------------
#
# NOTE (2026-07-10, defect #2 fix): the module-level ``CACHE_FILE`` global was
# removed in favor of ``_cache_file_for(tasks_dir)``, which derives a
# per-session cache path (keyed by a hash of the resolved tasks dir) so
# concurrent sessions never share/clobber each other's cached count.
# ``_read_cache``/``_write_cache`` now take the cache file path explicitly
# instead of reading the removed global.

def test_cache_write_and_read(tmp_path: Path) -> None:
    """Writing a cache entry then reading it back returns the same count."""
    cache_file = str(tmp_path / "cache.json")
    agent_liveness._write_cache(7, cache_file)
    cached = agent_liveness._read_cache(cache_file)
    assert cached is not None
    ts, count = cached
    assert count == 7
    assert abs(ts - time.time()) < 2.0


def test_cache_missing_file(tmp_path: Path) -> None:
    """Reading a nonexistent cache file returns None."""
    cache_file = str(tmp_path / "nonexistent_cache.json")
    assert agent_liveness._read_cache(cache_file) is None


def test_cache_corrupted_file(tmp_path: Path) -> None:
    """Reading a corrupted cache file returns None."""
    cache_file = tmp_path / "corrupt_cache.json"
    cache_file.write_text("not valid json")
    assert agent_liveness._read_cache(str(cache_file)) is None


def test_cache_fresh_entry_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fresh cache entry is used instead of probing."""
    import json
    cache_file = tmp_path / "fresh_cache.json"
    cache_file.write_text(json.dumps({"ts": time.time(), "count": 42}))
    monkeypatch.setenv("GLUDD_LIVENESS_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 30.0)
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent")
    count = agent_liveness._count_live_total(window=60.0)
    assert count == 42, "cached count should be returned without probing"


def test_cache_stale_entry_not_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stale cache entry is ignored and a fresh probe is run."""
    import json
    cache_file = tmp_path / "stale_cache.json"
    cache_file.write_text(json.dumps({"ts": time.time() - 100, "count": 99}))
    monkeypatch.setenv("GLUDD_LIVENESS_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 10.0)
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    # No claude transcripts exist either -> probe returns 0
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent")
    count = agent_liveness._count_live_total(window=60.0)
    assert count == 0, "stale cache should be ignored; probe returns 0"


# --- 11. per-session cache keying (defect #2 fix) ---------------------------

def test_cache_file_for_differs_by_tasks_dir(tmp_path: Path) -> None:
    """_cache_file_for derives a distinct, deterministic path per resolved
    tasks dir."""
    a = agent_liveness._cache_file_for(str(tmp_path / "a"))
    b = agent_liveness._cache_file_for(str(tmp_path / "b"))
    assert a != b
    assert a == agent_liveness._cache_file_for(str(tmp_path / "a")), "must be stable/deterministic"


def test_cache_file_for_hard_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = str(tmp_path / "explicit.json")
    monkeypatch.setenv("GLUDD_LIVENESS_CACHE_FILE", override)
    assert agent_liveness._cache_file_for(str(tmp_path / "whatever")) == override
    assert agent_liveness._cache_file_for(None) == override


def test_no_crosstalk_between_concurrent_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BUG FIXED (defect #2): two 'sessions' (distinct GLUDD_TASKS_DIR roots)
    must never read each other's cached live count. The old implementation
    used ONE machine-global cache file (/tmp/gludd-live-count-cache.json)
    shared by every concurrent Claude Code process on the box."""
    import json as json_mod

    monkeypatch.delenv("GLUDD_LIVENESS_CACHE_FILE", raising=False)
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 30.0)

    session_a_tasks = tmp_path / "session_a" / "tasks"
    session_a_tasks.mkdir(parents=True)
    session_a_wf = tmp_path / "session_a" / "wf"
    session_a_wf.mkdir(parents=True)
    for i in range(2):
        f = session_a_tasks / f"agent{i}.output"
        f.write_text(json_mod.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "x"}}))

    session_b_tasks = tmp_path / "session_b" / "tasks"
    session_b_tasks.mkdir(parents=True)
    session_b_wf = tmp_path / "session_b" / "wf"
    session_b_wf.mkdir(parents=True)
    # session_b has NO transcripts -> 0 live.

    monkeypatch.setenv("GLUDD_TASKS_DIR", str(session_a_tasks))
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(session_a_wf))
    count_a = agent_liveness._count_live_total(window=120.0)
    assert count_a == 2

    monkeypatch.setenv("GLUDD_TASKS_DIR", str(session_b_tasks))
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(session_b_wf))
    count_b = agent_liveness._count_live_total(window=120.0)
    assert count_b == 0, "session B must not read session A's cached count (no cross-talk)"

    # Session A's own cache must still be independently intact.
    monkeypatch.setenv("GLUDD_TASKS_DIR", str(session_a_tasks))
    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(session_a_wf))
    count_a_again = agent_liveness._count_live_total(window=120.0)
    assert count_a_again == 2


# --- 10. _count_live_total: max of both backends ----------------------------

def test_live_total_uses_opencode_when_claude_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When claude has 0 but opencode has live agents, _count_live_total reports opencode count."""
    import sqlite3
    db = tmp_path / "oc.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER
        )
    """)
    now_ms = int(time.time() * 1000)
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, "
        "time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sub1", "p1", "par1", "s", "/d", "t", "1", now_ms - 5000, now_ms - 2000),
    )
    con.commit()
    con.close()

    monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    count = agent_liveness._count_live_total(window=60.0)
    assert count == 1, "opencode has 1 live, claude has 0 -> max is 1"


def test_live_total_debug_returns_breakdown(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """_count_live_total_debug returns per-backend breakdown and harness detection."""
    monkeypatch.setenv("OPENCODE_DB_PATH", "/nonexistent")
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent")
    monkeypatch.setattr(agent_liveness, "CACHE_TTL_SEC", 0.0)
    total, claude_n, oc_n, harness, cached = agent_liveness._count_live_total_debug(window=60.0)
    assert total == 0
    assert claude_n == 0
    assert oc_n == 0
    assert isinstance(harness, str)
    assert cached is False


# --- 12. _tasks_dir activity-based ranking (defect #1 fix) ------------------
#
# These tests set ``GLUDD_CLAUDE_SESSIONS_BASE`` to a pytest-owned directory
# rather than ``GLUDD_TASKS_DIR``, because the latter short-circuits the
# session-discovery heuristic. The base override preserves the discovery path
# while keeping every filesystem write hermetic on Linux and macOS.


def test_claude_sessions_base_honors_isolated_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = tmp_path / "claude-sessions"
    monkeypatch.setenv("GLUDD_CLAUDE_SESSIONS_BASE", str(base))

    assert agent_liveness._claude_sessions_base() == str(base)


def test_activity_ranking_prefers_active_tasks_over_newer_empty_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A brand-new, near-empty session dir must NOT hijack the resolved tasks
    dir away from an older session whose tasks/ dir has genuine, fresh file
    activity. Reproduces the exact bug: ranking by the SESSION DIR's own mtime
    (which the empty new session wins) instead of by activity INSIDE tasks/."""
    base = str(tmp_path / "claude-sessions")
    monkeypatch.setenv("GLUDD_CLAUDE_SESSIONS_BASE", base)
    old_session_dir = os.path.join(base, "old-session")
    new_session_dir = os.path.join(base, "new-session")
    old_session_tasks = os.path.join(old_session_dir, "tasks")
    new_session_tasks = os.path.join(new_session_dir, "tasks")
    os.makedirs(old_session_tasks, exist_ok=True)
    os.makedirs(new_session_tasks, exist_ok=True)
    now = time.time()
    active_f = os.path.join(old_session_tasks, "agent1.output")
    with open(active_f, "w") as fh:
        fh.write("x")
    os.utime(active_f, (now - 1, now - 1))               # actively streaming
    os.utime(old_session_dir, (now - 1000, now - 1000))  # but dir itself is OLD

    os.utime(new_session_dir, (now - 5, now - 5))         # dir created "recently"
    # new_session_tasks stays EMPTY -> _dir_activity_mtime falls back to dir mtime.

    monkeypatch.delenv("GLUDD_TASKS_DIR", raising=False)
    monkeypatch.delenv("GLUDD_SESSION_ID", raising=False)

    resolved = agent_liveness._tasks_dir()
    assert resolved == old_session_tasks, (
        "the session with genuine tasks/ file activity must win over a "
        "nominally newer but empty session dir"
    )


def test_dir_activity_mtime_empty_dir_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty_tasks"
    empty.mkdir()
    assert agent_liveness._dir_activity_mtime(str(empty)) is None


def test_dir_activity_mtime_unreadable_returns_none(tmp_path: Path) -> None:
    assert agent_liveness._dir_activity_mtime(str(tmp_path / "does_not_exist")) is None


def test_dir_activity_mtime_returns_max_file_mtime(tmp_path: Path) -> None:
    d = tmp_path / "tasks"
    d.mkdir()
    older = d / "a.output"
    newer = d / "b.output"
    older.write_text("a")
    newer.write_text("b")
    now = time.time()
    os.utime(str(older), (now - 500, now - 500))
    os.utime(str(newer), (now - 1, now - 1))
    activity = agent_liveness._dir_activity_mtime(str(d))
    assert activity is not None
    assert abs(activity - (now - 1)) < 2.0


def test_session_id_env_resolves_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """GLUDD_SESSION_ID lets a caller (e.g. force_delegate_pretool.sh, which has
    the session id from the PreToolUse payload) resolve its OWN tasks dir
    deterministically, bypassing the activity-ranking heuristic entirely --
    even when another session LOOKS more active by the heuristic."""
    base = str(tmp_path / "claude-sessions")
    monkeypatch.setenv("GLUDD_CLAUDE_SESSIONS_BASE", base)
    target_session_id = "test-session-xyz"
    target_tasks = os.path.join(base, target_session_id, "tasks")
    other_tasks = os.path.join(base, "other-session", "tasks")
    os.makedirs(target_tasks, exist_ok=True)
    os.makedirs(other_tasks, exist_ok=True)
    now = time.time()
    # Make the OTHER session look more "active" than the target -- the
    # session-id override must win anyway.
    other_f = os.path.join(other_tasks, "agent.output")
    with open(other_f, "w") as fh:
        fh.write("y")
    os.utime(other_f, (now, now))

    target_f = os.path.join(target_tasks, "agent.output")
    with open(target_f, "w") as fh:
        fh.write("x")
    os.utime(target_f, (now - 5000, now - 5000))

    monkeypatch.delenv("GLUDD_TASKS_DIR", raising=False)
    monkeypatch.setenv("GLUDD_SESSION_ID", target_session_id)

    resolved = agent_liveness._tasks_dir()
    assert resolved == target_tasks


def test_session_id_env_falls_back_when_no_tasks_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If GLUDD_SESSION_ID points at a session with no tasks/ dir (yet), fall
    back to the activity-ranking heuristic rather than returning None."""
    base = str(tmp_path / "claude-sessions")
    monkeypatch.setenv("GLUDD_CLAUDE_SESSIONS_BASE", base)
    real_tasks = os.path.join(base, "real-session", "tasks")
    os.makedirs(real_tasks, exist_ok=True)
    f = os.path.join(real_tasks, "agent.output")
    with open(f, "w") as fh:
        fh.write("x")

    monkeypatch.delenv("GLUDD_TASKS_DIR", raising=False)
    monkeypatch.setenv("GLUDD_SESSION_ID", "nonexistent-session-id")

    resolved = agent_liveness._tasks_dir()
    assert resolved == real_tasks


# --- 13. _workflow_transcript_files window/session filtering (defect #3 fix) -

def test_stale_workflow_session_tree_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A session's workflow tree that is entirely stale (newest transcript
    older than the window) must be excluded from the returned files, while a
    fresh session's transcripts are still included."""
    monkeypatch.delenv("GLUDD_WORKFLOW_DIRS", raising=False)
    monkeypatch.delenv("GLUDD_SESSION_ID", raising=False)

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/shawnwilson/gludd")
    monkeypatch.setenv(
        "GLUDD_CLAUDE_SESSIONS_BASE",
        str(tmp_path / "empty-fallback"),
    )

    proj_dir = fake_home / ".claude" / "projects" / "-Users-shawnwilson-gludd"

    fresh_session = proj_dir / "fresh-session"
    fresh_wf_run = fresh_session / "subagents" / "workflows" / "run1"
    fresh_wf_run.mkdir(parents=True)
    fresh_agent = fresh_wf_run / "agent-1.jsonl"
    fresh_agent.write_text('{"type":"assistant"}\n')

    stale_session = proj_dir / "stale-session"
    stale_wf_run = stale_session / "subagents" / "workflows" / "run2"
    stale_wf_run.mkdir(parents=True)
    stale_agent = stale_wf_run / "agent-2.jsonl"
    stale_agent.write_text('{"type":"assistant"}\n')
    old_ts = time.time() - 10_000
    os.utime(str(stale_agent), (old_ts, old_ts))

    files = agent_liveness._workflow_transcript_files(window=120.0)
    assert str(fresh_agent) in files
    assert str(stale_agent) not in files, "an entirely-stale session's workflow tree must be excluded"


def test_workflow_session_id_match_always_included_regardless_of_age(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When GLUDD_SESSION_ID matches a session dir name exactly, its workflow
    transcripts are included even if their mtime is outside the window."""
    monkeypatch.delenv("GLUDD_WORKFLOW_DIRS", raising=False)

    fake_home = tmp_path / "home2"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/shawnwilson/gludd")
    monkeypatch.setenv(
        "GLUDD_CLAUDE_SESSIONS_BASE",
        str(tmp_path / "empty-fallback"),
    )

    proj_dir = fake_home / ".claude" / "projects" / "-Users-shawnwilson-gludd"
    matched_session = proj_dir / "matched-session"
    wf_run = matched_session / "subagents" / "workflows" / "run1"
    wf_run.mkdir(parents=True)
    agent_f = wf_run / "agent-1.jsonl"
    agent_f.write_text('{"type":"assistant"}\n')
    old_ts = time.time() - 10_000
    os.utime(str(agent_f), (old_ts, old_ts))

    monkeypatch.setenv("GLUDD_SESSION_ID", "matched-session")
    files = agent_liveness._workflow_transcript_files(window=120.0)
    assert str(agent_f) in files, "an exact session-id match must be included regardless of age"


def test_workflow_dirs_override_still_flat_and_unfiltered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The GLUDD_WORKFLOW_DIRS test override remains back-compat: files are
    returned regardless of age (no recency filtering applied to the override
    path), preserving the pre-existing test seam contract."""
    d = tmp_path / "wf_override"
    d.mkdir()
    f = d / "agent-old.jsonl"
    f.write_text('{"type":"assistant"}\n')
    old_ts = time.time() - 100_000
    os.utime(str(f), (old_ts, old_ts))

    monkeypatch.setenv("GLUDD_WORKFLOW_DIRS", str(d))
    files = agent_liveness._workflow_transcript_files(window=25.0)
    assert str(f) in files


# --- 14. _is_agent_transcript multi-line scan (defect #5 fix) --------------

def test_is_agent_transcript_tolerates_non_marker_first_line(tmp_path: Path) -> None:
    """A transcript whose FIRST non-empty line lacks agent marker fields, but a
    later line (within the first 5) does, must still be classified as an agent
    transcript. The OLD implementation decided on line 1 alone."""
    import json
    f = tmp_path / "agent_mixed.output"
    lines = [
        json.dumps({"foo": "bar"}),
        json.dumps({"type": "assistant", "agentId": "abc123", "parentUuid": None, "message": {}}),
    ]
    f.write_text("\n".join(lines) + "\n")
    assert agent_liveness._is_agent_transcript(str(f)) is True


def test_is_agent_transcript_false_when_no_marker_in_first_five_lines(tmp_path: Path) -> None:
    import json
    f = tmp_path / "plain_bash.output"
    lines = [json.dumps({"foo": i}) for i in range(6)]
    f.write_text("\n".join(lines) + "\n")
    assert agent_liveness._is_agent_transcript(str(f)) is False


def test_is_agent_transcript_marker_beyond_five_lines_not_counted(tmp_path: Path) -> None:
    """Only the first 5 non-empty lines are scanned; a marker appearing later
    is NOT enough (bounded scan, not unbounded)."""
    import json
    lines = [json.dumps({"foo": i}) for i in range(5)]
    lines.append(json.dumps({"type": "assistant", "agentId": "late", "parentUuid": None, "message": {}}))
    f = tmp_path / "late_marker.output"
    f.write_text("\n".join(lines) + "\n")
    assert agent_liveness._is_agent_transcript(str(f)) is False


def test_is_agent_transcript_malformed_line_skipped_not_fatal(tmp_path: Path) -> None:
    """A malformed (non-JSON) line among the first 5 is skipped, not treated as
    a verdict -- scanning continues to the next line."""
    import json
    f = tmp_path / "malformed_then_marker.output"
    f.write_bytes(
        b"not json at all\n"
        + json.dumps({"type": "assistant", "agentId": "x", "parentUuid": None, "message": {}}).encode()
        + b"\n"
    )
    assert agent_liveness._is_agent_transcript(str(f)) is True


def test_is_agent_transcript_plain_bash_output_excluded(tmp_path: Path) -> None:
    """A genuine plain-text bash .output file (no JSON at all) is still
    correctly excluded (regression guard for the 2026-06-24 fix)."""
    f = tmp_path / "bash.output"
    f.write_text("running tests...\nPASS\nOK\n")
    assert agent_liveness._is_agent_transcript(str(f)) is False
