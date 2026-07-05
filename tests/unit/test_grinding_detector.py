"""Tests for grinding_detector — auto-detection of broken-agent patterns."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.self_update.grinding_detector import detect_and_create_todos


def _write_json(path: str, data: object) -> None:
    with open(path, "w") as fh:
        json.dump(data, fh)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Ensure state files are patched so tests never touch real /tmp state."""
    pass


@pytest.fixture
def tmp_streak_file() -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "gludd-floor-streak.json"


@pytest.fixture
def tmp_stop_file() -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "gludd-stop-state.json"


@pytest.fixture
def tmp_deadline_file() -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "gludd-task-deadlines.json"


@pytest.fixture
def recent_ts() -> float:
    return time.time() - 10


# ── High streak → self_improve todo created ──────────────────────────────────


def test_high_streak_creates_grinding_todo(tmp_streak_file: Path, recent_ts: float):
    _write_json(str(tmp_streak_file), {
        "streak": 15,
        "timestamp": recent_ts,
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    assert len(grinding) == 1
    assert "streak" in grinding[0]["description"].lower()
    assert grinding[0]["evidence"]["max_streak"] == 15


def test_high_streak_from_entries(tmp_streak_file: Path, recent_ts: float):
    _write_json(str(tmp_streak_file), {
        "entries": [
            {"streak": 12, "timestamp": recent_ts},
            {"streak": 3, "timestamp": recent_ts},
        ],
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    assert len(grinding) == 1
    assert grinding[0]["evidence"]["max_streak"] == 12


# ── Normal streak → no todo ─────────────────────────────────────────────────


def test_normal_streak_no_todo(tmp_streak_file: Path, recent_ts: float):
    _write_json(str(tmp_streak_file), {
        "streak": 3,
        "timestamp": recent_ts,
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    assert len(grinding) == 0


def test_old_streak_no_todo(tmp_streak_file: Path):
    _write_json(str(tmp_streak_file), {
        "streak": 20,
        "timestamp": time.time() - 600,  # 10 min ago, outside 5 min window
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    assert len(grinding) == 0


# ── Frequent blocks → self_improve todo created ──────────────────────────────


def test_frequent_blocks_creates_stop_todo(tmp_stop_file: Path, recent_ts: float):
    _write_json(str(tmp_stop_file), {
        "entries": [
            {"blocked": True, "timestamp": recent_ts},
            {"blocked": True, "timestamp": recent_ts + 1},
            {"blocked": True, "timestamp": recent_ts + 2},
        ],
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", "/nonexistent"):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", str(tmp_stop_file)):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    stop_todos = [t for t in todos if t.get("gap_type") == "stop_false_positives"]
    assert len(stop_todos) == 1
    assert stop_todos[0]["evidence"]["block_count"] == 3
    assert "enforce-stop" in stop_todos[0]["description"].lower()


# ── No blocks → no todo ─────────────────────────────────────────────────────


def test_no_blocks_no_todo(tmp_stop_file: Path, recent_ts: float):
    _write_json(str(tmp_stop_file), {
        "entries": [
            {"blocked": False, "timestamp": recent_ts},
            {"blocked": False, "timestamp": recent_ts + 1},
        ],
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", "/nonexistent"):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", str(tmp_stop_file)):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    stop_todos = [t for t in todos if t.get("gap_type") == "stop_false_positives"]
    assert len(stop_todos) == 0


# ── Combined high streak + frequent blocks → multiple todos ─────────────────


def test_combined_high_streak_and_blocks(
    tmp_streak_file: Path, tmp_stop_file: Path, recent_ts: float
):
    _write_json(str(tmp_streak_file), {
        "streak": 14,
        "timestamp": recent_ts,
    })
    _write_json(str(tmp_stop_file), {
        "entries": [
            {"blocked": True, "timestamp": recent_ts},
            {"blocked": True, "timestamp": recent_ts + 1},
            {"blocked": True, "timestamp": recent_ts + 2},
            {"blocked": True, "timestamp": recent_ts + 3},
        ],
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", str(tmp_stop_file)):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    stop_todos = [t for t in todos if t.get("gap_type") == "stop_false_positives"]
    assert len(grinding) == 1
    assert len(stop_todos) == 1
    assert len(todos) >= 2


# ── Task deadline violations → self_improve todo created ────────────────────


def test_deadline_violations_creates_todo(tmp_deadline_file: Path, recent_ts: float):
    _write_json(str(tmp_deadline_file), {
        "entries": [
            {"deadline_exceeded": True, "timestamp": recent_ts},
            {"deadline_exceeded": True, "timestamp": recent_ts + 1},
            {"deadline_exceeded": True, "timestamp": recent_ts + 2},
            {"deadline_exceeded": True, "timestamp": recent_ts + 3},
        ],
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", "/nonexistent"):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", str(tmp_deadline_file)):
                todos = detect_and_create_todos()
    deadline_todos = [t for t in todos if t.get("gap_type") == "task_deadlines"]
    assert len(deadline_todos) == 1
    assert deadline_todos[0]["evidence"]["deadline_count"] == 4


# ── Missing files → no todos (graceful) ─────────────────────────────────────


def test_missing_state_files_no_todos():
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", "/nonexistent"):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()
    assert todos == []


# ── Returned todos match SelfImprovementHarness.generate_fix_todos shape ────


def test_todo_shape_compatible_with_harness(tmp_streak_file: Path, recent_ts: float):
    """Todos from grinding_detector must be directly passable to
    SelfImprovementHarness.generate_fix_todos and EventLoop._persist_self_improve_todos.
    """
    _write_json(str(tmp_streak_file), {
        "streak": 15,
        "timestamp": recent_ts,
    })
    with patch("general_ludd.self_update.grinding_detector._STREAK_FILE", str(tmp_streak_file)):
        with patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", "/nonexistent"):
            with patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", "/nonexistent"):
                todos = detect_and_create_todos()

    from general_ludd.self_improve.harness import SelfImprovementHarness
    harness = SelfImprovementHarness(repo_root="/tmp/test")
    # generate_fix_todos should accept these dicts without error
    fixed = harness.generate_fix_todos(todos)
    assert len(fixed) >= 1
    for todo in fixed:
        assert todo["title"]
        assert todo["description"]
