"""Unit tests for the remediation chronic-blocker reporter."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from general_ludd.remediation.blocker_detector import (
    BlockerDetector,
    ChronicBlocker,
    RemediationConfig,
)
from general_ludd.remediation.reporter import chronic_blocker_report


def make_chronic_blocker(
    task_type: str = "bug_fix",
    blocker_kind: str = "human_input",
    incident_count: int = 7,
) -> ChronicBlocker:
    now = datetime.now(UTC)
    return ChronicBlocker(
        task_type=task_type,
        blocker_kind=blocker_kind,
        incident_count=incident_count,
        first_seen=now,
        last_seen=now,
        recent_todo_ids=["todo-1", "todo-2"],
    )


class TestChronicBlockerReport:
    async def test_empty_report(self):
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig()
        detector.chronic_blockers = AsyncMock(return_value=[])

        report = await chronic_blocker_report(detector)

        assert report["total"] == 0
        assert report["chronic_blockers"] == []
        assert "generated_at" in report
        assert report["lookback_days"] == 7
        assert report["project_id"] is None
        detector.chronic_blockers.assert_awaited_once_with(lookback_days=None)

    async def test_single_blocker(self):
        cb = make_chronic_blocker()
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig()
        detector.chronic_blockers = AsyncMock(return_value=[cb])

        report = await chronic_blocker_report(detector)

        assert report["total"] == 1
        assert len(report["chronic_blockers"]) == 1
        entry = report["chronic_blockers"][0]
        assert entry["task_type"] == "bug_fix"
        assert entry["blocker_kind"] == "human_input"
        assert entry["incident_count"] == 7
        assert entry["recent_todo_ids"] == ["todo-1", "todo-2"]
        assert "T" in entry["first_seen"]
        assert "T" in entry["last_seen"]

    async def test_multiple_blockers(self):
        cb1 = make_chronic_blocker(task_type="bug_fix", blocker_kind="human_input")
        cb2 = make_chronic_blocker(
            task_type="feature", blocker_kind="permission_escalation"
        )
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig()
        detector.chronic_blockers = AsyncMock(return_value=[cb1, cb2])

        report = await chronic_blocker_report(detector)

        assert report["total"] == 2
        types = [b["task_type"] for b in report["chronic_blockers"]]
        assert "bug_fix" in types
        assert "feature" in types

    async def test_override_lookback(self):
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig(chronic_lookback_days=7)
        detector.chronic_blockers = AsyncMock(return_value=[])

        report = await chronic_blocker_report(detector, lookback_days=14)

        assert report["lookback_days"] == 14
        detector.chronic_blockers.assert_awaited_once_with(lookback_days=14)

    async def test_project_id_present(self):
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig()
        detector.chronic_blockers = AsyncMock(return_value=[])

        report = await chronic_blocker_report(detector, project_id="proj-1")

        assert report["project_id"] == "proj-1"

    async def test_generated_at_is_iso_utc(self):
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig()
        detector.chronic_blockers = AsyncMock(return_value=[])

        report = await chronic_blocker_report(detector)

        iso = report["generated_at"]
        assert iso.endswith("+00:00") or iso.endswith("Z")
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    async def test_config_lookback_days_used_when_none(self):
        detector = MagicMock(spec=BlockerDetector)
        detector.config = RemediationConfig(chronic_lookback_days=30)
        detector.chronic_blockers = AsyncMock(return_value=[])

        report = await chronic_blocker_report(detector, lookback_days=None)

        assert report["lookback_days"] == 30
