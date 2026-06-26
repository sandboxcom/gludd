"""Unit tests for TodoScheduler (integrated cron / one-shot todo scheduling).

NOTE: distinct from tests/unit/test_scheduler.py, which covers the unrelated
concurrency batch-planner in general_ludd.scheduling.scheduler.

Covers:
  - one-shot: not promoted before due; promoted exactly once when due
  - cron: spawns a QUEUED child clone + advances template state
  - max_runs cap cancels the template; below cap still spawns
  - schedule_paused is skipped even when returned by the query
  - timezone-aware next-run computation via _next_cron_dt
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.scheduler import TodoScheduler, _next_cron_dt
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_todo(**overrides: object) -> MagicMock:
    """Build a minimal mock TodoModel for scheduler tests."""
    todo = MagicMock()
    defaults: dict[str, object] = {
        "todo_id": "TODO-AAAA0001",
        "status": TodoStatus.SCHEDULED.value,
        "schedule_paused": False,
        "cron": None,
        "scheduled_at": None,
        "next_run_at": None,
        "last_run_at": None,
        "run_count": 0,
        "max_runs": None,
        "schedule_timezone": "UTC",
        "version": 1,
        "title": "Scheduled test todo",
        "description": "",
        "work_type": "code",
        "queue": "core",
        "priority": 0,
        "tags": [],
        "risk_level": "low",
        "resource_profile": "low_resource",
        "project_id": None,
        "created_by": "agent",
        "acceptance_criteria": [],
        "test_commands": [],
        "molecule_scenarios": [],
        "molecule_evidence_refs": [],
        "dependencies": [],
        "coverage_requirements": None,
        "model_profile": None,
        "prompt_profile": None,
        "worktree": None,
        "branch_name": None,
        "plan_artifact": None,
        "confidence": None,
        "approval_policy": "none",
        "assigned_agent": None,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(todo, k, v)
    return todo


def _make_repo(due_todos: list[MagicMock] | None = None) -> AsyncMock:
    repo = AsyncMock()
    repo.list_due_scheduled = AsyncMock(return_value=due_todos or [])
    repo.transition = AsyncMock(return_value=MagicMock())
    repo.create = AsyncMock(return_value=MagicMock())
    repo.update = AsyncMock(return_value=MagicMock())
    return repo


# ---------------------------------------------------------------------------
# One-shot tests
# ---------------------------------------------------------------------------

class TestOneShotSchedule:
    @pytest.mark.asyncio
    async def test_one_shot_not_promoted_when_not_due(self) -> None:
        """list_due_scheduled returns nothing for a future fire time → no-op."""
        repo = _make_repo(due_todos=[])
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))
        assert promoted == 0
        assert spawned == 0
        repo.transition.assert_not_called()
        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_shot_promoted_to_queued_when_due(self) -> None:
        """A due one-shot todo is transitioned SCHEDULED→QUEUED exactly once."""
        todo = _make_todo(
            todo_id="TODO-SHOT001",
            cron=None,
            scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
            version=3,
        )
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        now = datetime.now(UTC)
        promoted, spawned = await scheduler.tick(now)
        assert promoted == 1
        assert spawned == 0
        repo.transition.assert_called_once_with(
            "TODO-SHOT001", TodoStatus.QUEUED, 3
        )
        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_shot_not_promoted_twice(self) -> None:
        """On the second tick the due list is empty → promotion does not repeat."""
        todo = _make_todo(version=1)
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        now = datetime.now(UTC)
        await scheduler.tick(now)
        assert repo.transition.call_count == 1
        # Second tick: already promoted, nothing due
        repo.list_due_scheduled.return_value = []
        promoted2, spawned2 = await scheduler.tick(now)
        assert promoted2 == 0
        assert spawned2 == 0
        assert repo.transition.call_count == 1  # no additional calls


# ---------------------------------------------------------------------------
# Cron / recurring template tests
# ---------------------------------------------------------------------------

class TestCronSchedule:
    @pytest.mark.asyncio
    async def test_cron_spawns_queued_child_and_advances_template(self) -> None:
        """Due cron template spawns one QUEUED child and advances next_run_at."""
        todo = _make_todo(
            todo_id="TODO-TMPL001",
            cron="* * * * *",
            run_count=0,
            max_runs=None,
            schedule_timezone="UTC",
            version=2,
        )
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)
        promoted, spawned = await scheduler.tick(now)

        assert promoted == 0
        assert spawned == 1
        repo.transition.assert_not_called()

        # Child was created
        repo.create.assert_called_once()
        child_data: dict[str, object] = repo.create.call_args.args[0]
        assert child_data["status"] == TodoStatus.QUEUED.value
        assert child_data["parent_todo_id"] == "TODO-TMPL001"
        # Scheduling fields must NOT be on the child
        assert "cron" not in child_data
        assert "scheduled_at" not in child_data
        assert "next_run_at" not in child_data
        assert "schedule_paused" not in child_data

        # Template was advanced
        repo.update.assert_called_once()
        updates: dict[str, object] = repo.update.call_args.args[1]
        assert updates["run_count"] == 1
        assert updates["last_run_at"] == now
        next_run = updates["next_run_at"]
        assert isinstance(next_run, datetime)
        assert next_run.tzinfo is not None
        assert next_run > now

    @pytest.mark.asyncio
    async def test_cron_max_runs_cancels_template_at_cap(self) -> None:
        """When run_count == max_runs the template transitions to CANCELLED."""
        todo = _make_todo(
            todo_id="TODO-CAP001",
            cron="0 * * * *",
            run_count=5,
            max_runs=5,
            version=7,
        )
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))

        assert promoted == 0
        assert spawned == 0
        repo.transition.assert_called_once_with(
            "TODO-CAP001", TodoStatus.CANCELLED, 7
        )
        repo.create.assert_not_called()
        repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_cron_below_max_runs_still_spawns(self) -> None:
        """run_count < max_runs: child is spawned, template is NOT cancelled."""
        todo = _make_todo(
            cron="0 * * * *",
            run_count=2,
            max_runs=5,
            version=3,
        )
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))

        assert spawned == 1
        assert promoted == 0
        repo.transition.assert_not_called()
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_cron_error_on_child_create_advances_template_then_misses(
        self,
    ) -> None:
        """DEFECT 2: the template is advanced BEFORE the child is created, so a
        child-create failure yields a safe MISSED occurrence (no spawn) rather
        than a DUPLICATE child. The old ordering (create-then-advance) would
        re-fire the template every tick because next_run_at never advanced."""
        todo = _make_todo(cron="* * * * *", version=1)
        repo = _make_repo(due_todos=[todo])
        repo.create.side_effect = RuntimeError("DB error")
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))

        assert spawned == 0  # create raised → occurrence missed, not duplicated
        assert promoted == 0
        repo.update.assert_called_once()  # template advanced first (no re-fire)
        repo.create.assert_called_once()  # spawn was attempted and failed


# ---------------------------------------------------------------------------
# Paused schedule tests
# ---------------------------------------------------------------------------

class TestPausedSchedule:
    @pytest.mark.asyncio
    async def test_paused_one_shot_is_skipped(self) -> None:
        """A paused one-shot todo returned by the query is silently skipped."""
        todo = _make_todo(schedule_paused=True, cron=None)
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))
        assert promoted == 0
        assert spawned == 0
        repo.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_paused_cron_template_is_skipped(self) -> None:
        """A paused cron template returned by the query spawns nothing."""
        todo = _make_todo(schedule_paused=True, cron="0 * * * *")
        repo = _make_repo(due_todos=[todo])
        scheduler = TodoScheduler(repo=repo)
        promoted, spawned = await scheduler.tick(datetime.now(UTC))
        assert promoted == 0
        assert spawned == 0
        repo.create.assert_not_called()


# ---------------------------------------------------------------------------
# _next_cron_dt (pure function) tests
# ---------------------------------------------------------------------------

class TestNextCronDt:
    def test_every_minute_cron_advances_by_at_most_one_minute(self) -> None:
        """'* * * * *' fires within the next 60 s after the reference time."""
        now = datetime(2026, 6, 25, 10, 0, 30, tzinfo=UTC)
        nxt = _next_cron_dt("* * * * *", now, "UTC")
        assert nxt.tzinfo is not None
        assert nxt > now
        assert nxt <= now + timedelta(minutes=2)

    def test_daily_cron_returns_future_datetime(self) -> None:
        """Daily cron at midnight returns a datetime later than the reference."""
        now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)
        nxt = _next_cron_dt("0 0 * * *", now, "UTC")
        assert nxt > now

    def test_timezone_aware_next_run(self) -> None:
        """next_run_at is returned in UTC regardless of schedule_timezone."""
        # "0 6 * * *" = every day at 06:00 in America/New_York (EDT = UTC-4 in summer).
        # 6am EDT = 10:00 UTC. After 11:00 UTC the next fire is 10:00 UTC next day.
        now = datetime(2026, 6, 25, 11, 0, 0, tzinfo=UTC)  # 11:00 UTC = 7:00 EDT
        nxt = _next_cron_dt("0 6 * * *", now, "America/New_York")
        assert nxt.tzinfo is not None
        assert nxt > now
        # Result must be in UTC (offset = UTC).
        offset = nxt.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0

    def test_invalid_timezone_raises_value_error(self) -> None:
        """Unknown timezone name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown timezone"):
            _next_cron_dt("* * * * *", datetime.now(UTC), "Fake/Zone")
