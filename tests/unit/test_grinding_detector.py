"""Tests for grinding_detector — auto-detection of broken-agent patterns."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from general_ludd.self_update.grinding_detector import (
    GrindingDetector,
    _read_json,
    detect_and_create_todos,
)


def _write_json(path: str, data: object) -> None:
    with open(path, "w") as fh:
        json.dump(data, fh)


def test_read_json_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    state_file = tmp_path / "invalid.json"
    state_file.write_text("{", encoding="utf-8")
    assert _read_json(str(state_file)) == {}


# ── Fixtures ────────────────────────────────────────────────────────────────


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


def _patch_state(*, streak: str = "/nonexistent", stop: str = "/nonexistent", deadline: str = "/nonexistent"):
    """Combine three file-path patches into a single context manager."""
    from contextlib import ExitStack
    from unittest.mock import patch as _patch
    stack = ExitStack()
    stack.enter_context(_patch("general_ludd.self_update.grinding_detector._STREAK_FILE", streak))
    stack.enter_context(_patch("general_ludd.self_update.grinding_detector._STOP_STATE_FILE", stop))
    stack.enter_context(_patch("general_ludd.self_update.grinding_detector._TASK_DEADLINE_FILE", deadline))
    return stack


# ── High streak → self_improve todo created ──────────────────────────────────


def test_high_streak_creates_grinding_todo(tmp_streak_file: Path, recent_ts: float):
    _write_json(str(tmp_streak_file), {
        "streak": 15,
        "timestamp": recent_ts,
    })
    with _patch_state(streak=str(tmp_streak_file)):
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
    with _patch_state(streak=str(tmp_streak_file)):
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
    with _patch_state(streak=str(tmp_streak_file)):
        todos = detect_and_create_todos()
    grinding = [t for t in todos if t.get("gap_type") == "agent_grinding"]
    assert len(grinding) == 0


def test_old_streak_no_todo(tmp_streak_file: Path):
    _write_json(str(tmp_streak_file), {
        "streak": 20,
        "timestamp": time.time() - 600,  # 10 min ago, outside 5 min window
    })
    with _patch_state(streak=str(tmp_streak_file)):
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
    with _patch_state(stop=str(tmp_stop_file)):
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
    with _patch_state(stop=str(tmp_stop_file)):
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
    with _patch_state(streak=str(tmp_streak_file), stop=str(tmp_stop_file)):
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
    with _patch_state(deadline=str(tmp_deadline_file)):
        todos = detect_and_create_todos()
    deadline_todos = [t for t in todos if t.get("gap_type") == "task_deadlines"]
    assert len(deadline_todos) == 1
    assert deadline_todos[0]["evidence"]["deadline_count"] == 4


# ── Missing files → no todos (graceful) ─────────────────────────────────────


def test_missing_state_files_no_todos():
    with _patch_state():
        todos = detect_and_create_todos()
    assert todos == []


# ── Returned todos match SelfImprovementHarness.generate_fix_todos shape ────


def test_todo_shape_compatible_with_harness(tmp_streak_file: Path, recent_ts: float):
    _write_json(str(tmp_streak_file), {
        "streak": 15,
        "timestamp": recent_ts,
    })
    with _patch_state(streak=str(tmp_streak_file)):
        todos = detect_and_create_todos()

    from general_ludd.self_improve.harness import SelfImprovementHarness
    harness = SelfImprovementHarness(repo_root="/tmp/test")
    fixed = harness.generate_fix_todos(todos)
    assert len(fixed) >= 1
    for todo in fixed:
        assert todo["title"]
        assert todo["description"]


# ── GrindingDetector class tests ──────────────────────────────────────────────


def _make_call(name: str, ts: float, dispatch: bool = False) -> dict:
    return {"tool_name": name, "timestamp": ts, "is_dispatch": dispatch}


def _make_response(has_tool_calls: bool, ts: float) -> dict:
    return {"has_tool_calls": has_tool_calls, "timestamp": ts}


class TestGrindingDetectorDetectGrinding:

    def test_detects_4_consecutive_reads(self):
        detector = GrindingDetector(streak_threshold=4)
        calls = [
            _make_call("read", 1.0),
            _make_call("read", 2.0),
            _make_call("read", 3.0),
            _make_call("read", 4.0),
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 1
        assert episodes[0].tool_count == 4
        assert episodes[0].tool_names == ["read", "read", "read", "read"]

    def test_dispatch_resets_streak(self):
        detector = GrindingDetector(streak_threshold=4)
        calls = [
            _make_call("read", 1.0),
            _make_call("read", 2.0),
            _make_call("read", 3.0),
            _make_call("task", 4.0, dispatch=True),
            _make_call("read", 5.0),
            _make_call("read", 6.0),
            _make_call("read", 7.0),
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 0

    def test_split_episodes(self):
        detector = GrindingDetector(streak_threshold=4)
        calls = [
            _make_call("read", 1.0),
            _make_call("read", 2.0),
            _make_call("read", 3.0),
            _make_call("read", 4.0),
            _make_call("read", 5.0),
            _make_call("task", 6.0, dispatch=True),
            _make_call("edit", 7.0),
            _make_call("edit", 8.0),
            _make_call("edit", 9.0),
            _make_call("edit", 10.0),
            _make_call("write", 11.0),
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 2
        assert episodes[0].tool_count == 5
        assert episodes[1].tool_count == 5

    def test_no_false_positive_on_normal_dispatch(self):
        detector = GrindingDetector(streak_threshold=4)
        calls = [
            _make_call("task", 1.0, dispatch=True),
            _make_call("task", 2.0, dispatch=True),
            _make_call("task", 3.0, dispatch=True),
            _make_call("task", 4.0, dispatch=True),
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 0

    def test_empty_calls_no_episodes(self):
        detector = GrindingDetector(streak_threshold=4)
        episodes = detector.detect_grinding([])
        assert len(episodes) == 0


class TestGrindingDetectorDetectPrematureStop:

    def test_detects_text_only_then_idle(self):
        detector = GrindingDetector(idle_threshold=30.0)
        now = time.time()
        responses = [
            _make_response(False, now - 60),  # text-only 60s ago
            _make_response(True, now - 20),   # tool call 20s ago
        ]
        episodes = detector.detect_premature_stop(responses)
        assert len(episodes) == 1
        assert episodes[0].idle_seconds == pytest.approx(40.0, abs=1.0)

    def test_text_only_trailing_against_now(self):
        detector = GrindingDetector(idle_threshold=30.0)
        responses = [
            _make_response(True, time.time() - 100),
            _make_response(False, time.time() - 60),  # last response text-only
        ]
        episodes = detector.detect_premature_stop(responses)
        assert len(episodes) == 1
        assert episodes[0].idle_seconds >= 30.0

    def test_no_stop_when_all_have_tool_calls(self):
        detector = GrindingDetector(idle_threshold=30.0)
        responses = [
            _make_response(True, 1.0),
            _make_response(True, 2.0),
        ]
        episodes = detector.detect_premature_stop(responses)
        assert len(episodes) == 0

    def test_empty_responses_no_stops(self):
        detector = GrindingDetector()
        episodes = detector.detect_premature_stop([])
        assert len(episodes) == 0


class TestGrindingDetectorReport:

    def test_generates_and_writes_report(self, tmp_path: Path):
        detector = GrindingDetector(streak_threshold=4)

        calls = [
            _make_call("read", 1.0),
            _make_call("read", 2.0),
            _make_call("read", 3.0),
            _make_call("read", 4.0),
        ]
        detector.detect_grinding(calls)

        responses = [
            _make_response(False, 1.0),
            _make_response(True, 60.0),
        ]
        detector.detect_premature_stop(responses)

        # Patch report path to use tmp_path
        report_file = tmp_path / "gludd-grinding-report.json"
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                detector, "_REPORT_PATH", str(report_file),
            )
            report = detector.generate_remediation_report()

        assert report["total_tool_calls_analyzed"] == 4
        assert len(report["grinding_episodes"]) == 1
        assert len(report["stop_episodes"]) == 1
        assert os.path.isfile(str(report_file))
