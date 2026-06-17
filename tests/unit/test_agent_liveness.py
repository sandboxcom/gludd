"""Unit tests for scripts/agent_liveness.py — the ground-truth live-subagent
counter.

These exercise the four guarantees the orchestrator depends on:
  1. grew-during-probe -> counted live (the definitely-live core signal),
  2. a stale (frozen, old) transcript is NOT counted,
  3. the FLOOR_LIVE_OVERRIDE test seam short-circuits all probing,
  4. fail-safe: a missing/unresolvable tasks dir yields 0, never raises.

All filesystem cases drive the counter against a pytest tmp dir via the
GLUDD_TASKS_DIR env override, so the tests are hermetic (no real session dirs).
"""
from __future__ import annotations

import importlib.util
import os
import threading
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
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    return d


def _write(path: Path, data: str) -> None:
    path.write_text(data)


def _age_file(path: Path, secs: float) -> None:
    """Backdate a file's mtime by ``secs`` seconds."""
    past = time.time() - secs
    os.utime(path, (past, past))


# --- 1. grew-during-probe counts live ---------------------------------------
def test_grew_during_probe_counts_live(tasks_dir: Path) -> None:
    f = tasks_dir / "agent1.output"
    _write(f, "start")
    # Backdate so the recent-tail term can't be what counts it — force the
    # "grew during probe" path to be the reason it's live.
    _age_file(f, 10_000)

    def appender() -> None:
        time.sleep(0.1)
        with f.open("a") as fh:
            fh.write("more tokens\n")
            fh.flush()
            os.fsync(fh.fileno())

    t = threading.Thread(target=appender)
    t.start()
    try:
        live, total, resolved = agent_liveness.live_count(probe=0.5, tail=1.0)
    finally:
        t.join()

    assert total == 1
    assert resolved == str(tasks_dir)
    assert live == 1, "a transcript that grew mid-probe must count as live"


# --- 2. stale (frozen + old) file is not counted ----------------------------
def test_stale_file_not_counted(tasks_dir: Path) -> None:
    f = tasks_dir / "done.output"
    _write(f, "completed transcript")
    _age_file(f, 10_000)  # far past any tail, and it will not grow during probe

    live, total, _ = agent_liveness.live_count(probe=0.2, tail=75.0)

    assert total == 1
    assert live == 0, "a frozen, old transcript must not be counted live"


def test_recent_tail_counts_quiet_agent(tasks_dir: Path) -> None:
    """A live-but-quiet agent (no growth during probe, but written recently) is
    kept alive by the tail term — the smoothing that prevents false breaches."""
    f = tasks_dir / "quiet.output"
    _write(f, "mid-think, no write this instant")
    _age_file(f, 5.0)  # within a 75s tail, will not grow during the probe

    live, total, _ = agent_liveness.live_count(probe=0.2, tail=75.0)

    assert total == 1
    assert live == 1


def test_tail_boundary_excludes_just_past(tasks_dir: Path) -> None:
    """A file just OUTSIDE the tail (and not growing) decays out -> not live."""
    f = tasks_dir / "decayed.output"
    _write(f, "x")
    _age_file(f, 80.0)

    live, _, _ = agent_liveness.live_count(probe=0.1, tail=75.0)
    assert live == 0


def test_mixed_fleet_counts_only_live(tasks_dir: Path) -> None:
    live_f = tasks_dir / "live.output"
    stale_f = tasks_dir / "stale.output"
    quiet_f = tasks_dir / "quiet.output"
    _write(live_f, "s")
    _age_file(live_f, 10_000)  # only the growth will count it
    _write(stale_f, "done")
    _age_file(stale_f, 10_000)
    _write(quiet_f, "recent")
    _age_file(quiet_f, 3.0)  # within tail

    def appender() -> None:
        time.sleep(0.1)
        with live_f.open("a") as fh:
            fh.write("grow\n")
            fh.flush()
            os.fsync(fh.fileno())

    t = threading.Thread(target=appender)
    t.start()
    try:
        live, total, _ = agent_liveness.live_count(probe=0.5, tail=30.0)
    finally:
        t.join()

    assert total == 3
    assert live == 2, "grew-file + quiet-within-tail are live; stale is not"


# --- 3. FLOOR_LIVE_OVERRIDE seam --------------------------------------------
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


# --- 4. fail-safe on missing / unresolvable dir -----------------------------
def test_missing_dir_resolves_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    assert agent_liveness._tasks_dir() is None


def test_missing_dir_live_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
    monkeypatch.delenv("FLOOR_LIVE_OVERRIDE", raising=False)
    live, total, resolved = agent_liveness.live_count(probe=0.05, tail=75.0)
    assert (live, total, resolved) == (0, 0, None)


def test_main_count_failsafe_zero(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("GLUDD_TASKS_DIR", "/nonexistent/path/does/not/exist")
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
