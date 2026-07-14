"""Unit tests for remediation reporter (chronic-blocker report shape).

Tests the async ``chronic_blocker_report()`` function in
src/general_ludd/remediation/reporter.py — verifying the contract shape,
field types, and integration with BlockerDetector.chronic_blockers().
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.remediation.blocker_detector import (
    ChronicBlocker,
    RemediationConfig,
)
from general_ludd.remediation.reporter import chronic_blocker_report

# --- Helpers ----------------------------------------------------------------


def _make_chronic_blocker(**overrides: object) -> ChronicBlocker:
    defaults: dict[str, object] = {
        "task_type": "code",
        "blocker_kind": "permission_escalation",
        "incident_count": 7,
        "first_seen": datetime(2026, 1, 1, tzinfo=UTC),
        "last_seen": datetime(2026, 6, 15, tzinfo=UTC),
        "recent_todo_ids": ["todo-1", "todo-2"],
    }
    defaults.update(overrides)
    return ChronicBlocker(**defaults)  # type: ignore[arg-type]


def _make_mock_detector(
    blockers: list[ChronicBlocker] | None = None,
    lookback_days: int = 7,
) -> MagicMock:
    detector = MagicMock()
    resolved = blockers if blockers is not None else [_make_chronic_blocker()]
    detector.chronic_blockers = AsyncMock(return_value=resolved)
    detector.config = RemediationConfig(chronic_lookback_days=lookback_days)
    return detector


# --- Tests ------------------------------------------------------------------


class TestChronicBlockerReport:
    @pytest.mark.asyncio
    async def test_return_shape_keys(self) -> None:
        detector = _make_mock_detector()
        result = await chronic_blocker_report(detector, project_id="proj-a")
        assert set(result.keys()) == {
            "generated_at",
            "lookback_days",
            "project_id",
            "chronic_blockers",
            "total",
        }

    @pytest.mark.asyncio
    async def test_generated_at_is_iso_utc(self) -> None:
        detector = _make_mock_detector()
        result = await chronic_blocker_report(detector)
        ts = result["generated_at"]
        assert isinstance(ts, str)
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_project_id_passthrough(self) -> None:
        detector = _make_mock_detector()
        result = await chronic_blocker_report(detector, project_id="org/proj-x")
        assert result["project_id"] == "org/proj-x"

    @pytest.mark.asyncio
    async def test_project_id_none(self) -> None:
        detector = _make_mock_detector()
        result = await chronic_blocker_report(detector, project_id=None)
        assert result["project_id"] is None

    @pytest.mark.asyncio
    async def test_lookback_days_from_config_when_not_passed(self) -> None:
        detector = _make_mock_detector(lookback_days=14)
        result = await chronic_blocker_report(detector, lookback_days=None)
        assert result["lookback_days"] == 14

    @pytest.mark.asyncio
    async def test_lookback_days_explicit_overrides_config(self) -> None:
        detector = _make_mock_detector(lookback_days=14)
        result = await chronic_blocker_report(detector, lookback_days=30)
        assert result["lookback_days"] == 30

    @pytest.mark.asyncio
    async def test_lookback_days_zero_falls_back_to_config(self) -> None:
        detector = _make_mock_detector(lookback_days=7)
        result = await chronic_blocker_report(detector, lookback_days=0)
        assert result["lookback_days"] == 7

    @pytest.mark.asyncio
    async def test_total_matches_blocker_count(self) -> None:
        blockers = [
            _make_chronic_blocker(task_type="a"),
            _make_chronic_blocker(task_type="b"),
            _make_chronic_blocker(task_type="c"),
        ]
        detector = _make_mock_detector(blockers=blockers)
        result = await chronic_blocker_report(detector)
        assert result["total"] == 3

    @pytest.mark.asyncio
    async def test_empty_blockers(self) -> None:
        detector = _make_mock_detector(blockers=[])
        result = await chronic_blocker_report(detector)
        assert result["total"] == 0
        assert result["chronic_blockers"] == []

    @pytest.mark.asyncio
    async def test_chronic_blocker_field_types(self) -> None:
        detector = _make_mock_detector()
        result = await chronic_blocker_report(detector)
        blocker = result["chronic_blockers"][0]
        assert isinstance(blocker["task_type"], str)
        assert isinstance(blocker["blocker_kind"], str)
        assert isinstance(blocker["incident_count"], int)
        assert isinstance(blocker["first_seen"], str)
        assert isinstance(blocker["last_seen"], str)
        assert isinstance(blocker["recent_todo_ids"], list)

    @pytest.mark.asyncio
    async def test_chronic_blocker_field_values(self) -> None:
        b = _make_chronic_blocker(
            task_type="deploy",
            blocker_kind="human_input",
            incident_count=12,
            first_seen=datetime(2026, 2, 1, tzinfo=UTC),
            last_seen=datetime(2026, 7, 10, tzinfo=UTC),
            recent_todo_ids=["abc", "def"],
        )
        detector = _make_mock_detector(blockers=[b])
        result = await chronic_blocker_report(detector)
        item = result["chronic_blockers"][0]
        assert item["task_type"] == "deploy"
        assert item["blocker_kind"] == "human_input"
        assert item["incident_count"] == 12
        assert item["first_seen"] == "2026-02-01T00:00:00+00:00"
        assert item["last_seen"] == "2026-07-10T00:00:00+00:00"
        assert item["recent_todo_ids"] == ["abc", "def"]

    @pytest.mark.asyncio
    async def test_calls_detector_chronic_blockers_with_lookback(self) -> None:
        detector = _make_mock_detector()
        await chronic_blocker_report(detector, lookback_days=21)
        detector.chronic_blockers.assert_awaited_once_with(lookback_days=21)

    @pytest.mark.asyncio
    async def test_calls_detector_chronic_blockers_with_none(self) -> None:
        detector = _make_mock_detector()
        await chronic_blocker_report(detector, lookback_days=None)
        detector.chronic_blockers.assert_awaited_once_with(lookback_days=None)

    @pytest.mark.asyncio
    async def test_lookback_days_from_config_used_when_none_in_report(self) -> None:
        detector = _make_mock_detector(lookback_days=90)
        result = await chronic_blocker_report(detector, lookback_days=None)
        assert result["lookback_days"] == 90

    @pytest.mark.asyncio
    async def test_multiple_blockers_preserve_order(self) -> None:
        b1 = _make_chronic_blocker(task_type="first", blocker_kind="A")
        b2 = _make_chronic_blocker(task_type="second", blocker_kind="B")
        detector = _make_mock_detector(blockers=[b1, b2])
        result = await chronic_blocker_report(detector)
        assert result["total"] == 2
        assert result["chronic_blockers"][0]["task_type"] == "first"
        assert result["chronic_blockers"][1]["task_type"] == "second"

    @pytest.mark.asyncio
    async def test_recent_todo_ids_is_copy_not_reference(self) -> None:
        ids = ["id-a", "id-b"]
        b = _make_chronic_blocker(recent_todo_ids=ids)
        detector = _make_mock_detector(blockers=[b])
        result = await chronic_blocker_report(detector)
        result["chronic_blockers"][0]["recent_todo_ids"].append("id-c")
        assert b.recent_todo_ids == ["id-a", "id-b"]

    @pytest.mark.asyncio
    async def test_detector_config_default_used_when_no_lookback(self) -> None:
        cfg = RemediationConfig(chronic_lookback_days=42)
        detector = MagicMock()
        detector.chronic_blockers = AsyncMock(return_value=[])
        detector.config = cfg
        result = await chronic_blocker_report(detector)
        assert result["lookback_days"] == 42

    @pytest.mark.asyncio
    async def test_lookback_days_explicit_wins_over_config(self) -> None:
        cfg = RemediationConfig(chronic_lookback_days=42)
        detector = MagicMock()
        detector.chronic_blockers = AsyncMock(return_value=[])
        detector.config = cfg
        result = await chronic_blocker_report(detector, lookback_days=99)
        assert result["lookback_days"] == 99
