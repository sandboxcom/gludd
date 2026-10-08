"""Branch coverage for grinding-detector state parsing and fail-closed paths."""

from __future__ import annotations

import time
from typing import Any, cast

import pytest

import general_ludd.self_update.grinding_detector as detector


def test_recent_count_filters_invalid_stale_and_false_entries() -> None:
    """Only recent, mapping-shaped, truthy evidence contributes to a count."""
    now = time.time()
    record: dict[str, Any] = {
        "entries": [
            "invalid",
            {"timestamp": "bad", "blocked": True},
            {"timestamp": now - 100, "blocked": True},
            {"timestamp": now, "blocked": False},
            {"timestamp": now, "blocked": True},
        ]
    }
    assert detector._recent_count(record, 10, "blocked") == 1
    assert detector._recent_count({"entries": "invalid"}, 10, "blocked") == 0


def test_recent_single_value_covers_timestamp_and_value_failures() -> None:
    """Scalar state rejects malformed timestamps, stale data, and bad values."""
    now = time.time()
    assert detector._recent_single_value({"timestamp": "bad"}, 10, "value") == 0.0
    assert (
        detector._recent_single_value(
            {"timestamp": now - 100, "value": 9},
            10,
            "value",
        )
        == 0.0
    )
    assert (
        detector._recent_single_value(
            {"timestamp": now, "value": "bad"},
            10,
            "value",
        )
        == 0.0
    )
    assert detector._recent_single_value({"timestamp": now, "value": 3}, 10, "value") == 3.0


def test_recent_max_streak_covers_scalar_and_history_errors() -> None:
    """Streak parsing ignores malformed samples while retaining the recent maximum."""
    now = time.time()
    assert detector._recent_max_streak({"timestamp": "bad", "entries": "bad"}, 10) == 0
    assert detector._recent_max_streak({"timestamp": now, "streak": 4}, 10) == 4
    record: dict[str, Any] = {
        "timestamp": now,
        "streak": "bad",
        "entries": [
            "invalid",
            {"timestamp": "bad", "streak": 9},
            {"timestamp": now - 100, "streak": 9},
            {"timestamp": now, "streak": "bad"},
            {"timestamp": now, "streak": 2},
            {"timestamp": now, "streak": 5},
        ],
    }
    assert detector._recent_max_streak(record, 10) == 5


def test_recent_dispatch_count_filters_malformed_history() -> None:
    """Dispatch counting requires a recent positive integer signal."""
    now = time.time()
    record: dict[str, Any] = {
        "history": [
            "invalid",
            {"timestamp": "bad", "dispatched": 1},
            {"timestamp": now - 100, "dispatched": 1},
            {"timestamp": now, "dispatched": "bad"},
            {"timestamp": now, "dispatched": 0},
            {"timestamp": now, "dispatch_count": 1},
        ]
    }
    assert detector._recent_dispatch_count(record, 10) == 1
    assert detector._recent_dispatch_count({"entries": "invalid"}, 10) == 0


def test_low_dispatch_activity_generates_remediation_todo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recent inline activity without dispatches produces an actionable todo."""
    now = time.time()
    streak_state: dict[str, Any] = {
        "entries": [
            "invalid",
            {"timestamp": "bad"},
            *({"timestamp": now, "dispatched": 0} for _ in range(6)),
        ]
    }
    states = iter([streak_state, {}, {}])
    monkeypatch.setattr(detector, "_read_json", lambda path: next(states))

    todos = detector.detect_and_create_todos()
    assert [todo["gap_type"] for todo in todos] == ["low_dispatch"]
    assert todos[0]["evidence"]["recent_entries"] == 6


def test_session_detectors_ignore_non_mapping_records() -> None:
    """Malformed session records never become grinding or stop episodes."""
    malformed_calls = cast(list[dict[str, Any]], [object()])
    malformed_responses = cast(list[dict[str, Any]], [object()])
    instance = detector.GrindingDetector()
    assert instance.detect_grinding(malformed_calls) == []
    assert instance.detect_premature_stop(malformed_responses) == []


def test_report_write_failure_preserves_in_memory_evidence(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable state store is logged without discarding the report."""
    instance = detector.GrindingDetector()
    monkeypatch.setattr(instance, "_REPORT_PATH", "/unavailable/report.json")

    def fail_write(path: str, content: str) -> None:
        del path, content
        raise OSError("disk unavailable")

    monkeypatch.setattr(detector, "secure_write_text", fail_write)
    report = instance.generate_remediation_report()
    assert report["grinding_episodes"] == []
    assert "Failed to write grinding report" in caplog.text
