"""Deep unit tests for event_loop/scheduler.py — TodoScheduler and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.scheduler import (
    TodoScheduler,
    _build_child_data,
    _next_cron_dt,
)
from general_ludd.schemas.todo import TodoStatus

# ——— _next_cron_dt —————————————————————————————————————————————————————————


class TestNextCronDt:
    def test_daily_cron_returns_next_midnight_utc(self):
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 0 * * *", now, "UTC")
        expected = datetime(2026, 8, 11, 0, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_hourly_cron_returns_next_hour(self):
        now = datetime(2026, 8, 10, 12, 30, 0, tzinfo=UTC)
        result = _next_cron_dt("0 * * * *", now, "UTC")
        expected = datetime(2026, 8, 10, 13, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_minutely_cron_returns_next_minute(self):
        now = datetime(2026, 8, 10, 12, 0, 30, tzinfo=UTC)
        result = _next_cron_dt("* * * * *", now, "UTC")
        expected = datetime(2026, 8, 10, 12, 1, 0, tzinfo=UTC)
        assert result == expected

    def test_unknown_timezone_raises_value_error(self):
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="Unknown timezone"):
            _next_cron_dt("0 0 * * *", now, "Mars/Inferno")

    def test_returns_utc_times(self):
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 0 * * *", now, "America/New_York")
        assert result.tzinfo == UTC

    def test_fixed_specific_time_cron(self):
        now = datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("30 14 * * *", now, "UTC")
        expected = datetime(2026, 8, 10, 14, 30, 0, tzinfo=UTC)
        assert result == expected

    def test_future_time_beyond_current_day(self):
        now = datetime(2026, 8, 10, 15, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("30 14 * * *", now, "UTC")
        expected = datetime(2026, 8, 11, 14, 30, 0, tzinfo=UTC)
        assert result == expected

    def test_timezone_with_offset_yields_correct_utc(self):
        now = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 0 * * *", now, "Asia/Tokyo")
        expected = datetime(2026, 8, 10, 15, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_dst_spring_forward_handled_by_croniter(self):
        now = datetime(2026, 3, 8, 1, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 2 * * *", now, "America/New_York")
        assert result.tzinfo == UTC
        assert result > now


# ——— _build_child_data —————————————————————————————————————————————————————


class TestBuildChildData:
    def test_creates_unique_todo_id(self):
        template = MagicMock()
        template.todo_id = "TODO-PARENT01"
        child1 = _build_child_data(template)
        child2 = _build_child_data(template)
        assert child1["todo_id"] != child2["todo_id"]
        assert child1["todo_id"].startswith("TODO-")

    def test_sets_status_to_queued(self):
        template = MagicMock()
        template.todo_id = "TODO-PARENT01"
        child = _build_child_data(template)
        assert child["status"] == "queued"

    def test_sets_parent_todo_id(self):
        template = MagicMock()
        template.todo_id = "TODO-PARENT01"
        child = _build_child_data(template)
        assert child["parent_todo_id"] == "TODO-PARENT01"

    def test_clones_execution_fields(self):
        template = MagicMock()
        template.todo_id = "TODO-TPL"
        template.title = "Recurring task"
        template.description = "A repeating job"
        template.work_type = "code"
        template.queue = "core"
        template.priority = 5
        template.tags = ["tag1", "tag2"]
        template.project_id = "P-01"
        template.assigned_agent = "agent-01"
        child = _build_child_data(template)
        assert child["title"] == "Recurring task"
        assert child["description"] == "A repeating job"
        assert child["work_type"] == "code"
        assert child["queue"] == "core"
        assert child["priority"] == 5
        assert child["tags"] == ["tag1", "tag2"]
        assert child["project_id"] == "P-01"
        assert child["assigned_agent"] == "agent-01"

    def test_omits_none_values(self):
        template = MagicMock()
        template.todo_id = "TODO-TPL"
        template.title = "Minimal"
        template.description = None
        template.work_type = None
        child = _build_child_data(template)
        assert "description" not in child
        assert "work_type" not in child

    def test_excludes_scheduling_fields(self):
        template = MagicMock()
        template.todo_id = "TODO-TPL"
        template.scheduled_at = datetime(2026, 8, 10, tzinfo=UTC)
        template.cron = "0 * * * *"
        template.next_run_at = datetime(2026, 8, 11, tzinfo=UTC)
        template.max_runs = 10
        template.run_count = 5
        template.last_run_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        template.schedule_paused = False
        template.schedule_timezone = "UTC"
        child = _build_child_data(template)
        assert "scheduled_at" not in child
        assert "cron" not in child
        assert "next_run_at" not in child
        assert "max_runs" not in child
        assert "run_count" not in child
        assert "last_run_at" not in child
        assert "schedule_paused" not in child
        assert "schedule_timezone" not in child


# ——— TodoScheduler.__init__ ———————————————————————————————————————————————


class TestTodoSchedulerInit:
    def test_default_clock_returns_current_time(self):
        repo = MagicMock()
        scheduler = TodoScheduler(repo)
        t1 = scheduler._clock()
        assert isinstance(t1, datetime)
        assert t1.tzinfo == UTC

    def test_custom_clock_injected(self):
        repo = MagicMock()
        fixed = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        scheduler = TodoScheduler(repo, clock=lambda: fixed)
        assert scheduler._clock() == fixed

    def test_stores_repo_reference(self):
        repo = MagicMock()
        scheduler = TodoScheduler(repo)
        assert scheduler._repo is repo


# ——— TodoScheduler.tick ———————————————————————————————————————————————————


@pytest.fixture
def _repo():
    return AsyncMock()


@pytest.fixture
def _now():
    return datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def _scheduler(_repo):
    return TodoScheduler(_repo, clock=lambda: datetime.now(UTC))


def _make_due_todo(todo_id="TODO-01", cron=None, paused=False, run_count=0, max_runs=None, version=1):
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.version = version
    todo.cron = cron
    todo.schedule_paused = paused
    todo.run_count = run_count
    todo.max_runs = max_runs
    todo.schedule_timezone = "UTC"
    return todo


class TestTickNoWork:
    async def test_no_due_todos_returns_zero_zero(self, _repo, _now, _scheduler):
        _repo.list_due_scheduled.return_value = []
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 0
        assert spawned == 0

    async def test_calls_list_due_scheduled_with_now(self, _repo, _now, _scheduler):
        _repo.list_due_scheduled.return_value = []
        await _scheduler.tick(now=_now)
        _repo.list_due_scheduled.assert_called_once_with(_now)

    async def test_uses_clock_when_now_not_provided(self, _repo, _now):
        scheduler = TodoScheduler(_repo, clock=lambda: _now)
        _repo.list_due_scheduled.return_value = []
        await scheduler.tick()
        _repo.list_due_scheduled.assert_called_once_with(_now)


class TestOneShotPromotion:
    async def test_promotes_one_shot_todo(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-01")
        _repo.list_due_scheduled.return_value = [todo]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 1
        assert spawned == 0
        _repo.transition.assert_called_once_with("TODO-01", TodoStatus.QUEUED, 1)

    async def test_promotes_multiple_one_shots(self, _repo, _now, _scheduler):
        todo1 = _make_due_todo("TODO-01")
        todo2 = _make_due_todo("TODO-02")
        _repo.list_due_scheduled.return_value = [todo1, todo2]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 2
        assert spawned == 0

    async def test_promotion_failure_logged_and_continues(self, _repo, _now, _scheduler):
        todo1 = _make_due_todo("TODO-FAIL")
        todo2 = _make_due_todo("TODO-OK")
        _repo.transition.side_effect = [RuntimeError("boom"), None]
        _repo.list_due_scheduled.return_value = [todo1, todo2]
        promoted, _spawned = await _scheduler.tick(now=_now)
        assert promoted == 1
        assert _repo.transition.call_count == 2


class TestPausedSkipping:
    async def test_skips_paused_todo(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-PAUSED", paused=True)
        _repo.list_due_scheduled.return_value = [todo]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 0
        assert spawned == 0
        _repo.transition.assert_not_called()


class TestCronSpawn:
    async def test_advances_template_and_spawns_child(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=3)
        _repo.list_due_scheduled.return_value = [todo]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 0
        assert spawned == 1
        _repo.update.assert_called_once()
        update_args = _repo.update.call_args
        assert update_args[0][0] == "TODO-CRON"
        updates = update_args[0][1]
        assert updates["run_count"] == 4
        assert updates["last_run_at"] == _now
        assert "next_run_at" in updates
        _repo.create.assert_called_once()
        child = _repo.create.call_args[0][0]
        assert child["status"] == "queued"
        assert child["parent_todo_id"] == "TODO-CRON"

    async def test_max_runs_reached_cancels_template(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=5, max_runs=5)
        _repo.list_due_scheduled.return_value = [todo]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 0
        assert spawned == 0
        _repo.transition.assert_called_once_with("TODO-CRON", TodoStatus.CANCELLED, 1)

    async def test_run_count_exceeds_max_runs(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=7, max_runs=3)
        _repo.list_due_scheduled.return_value = [todo]
        _promoted, _spawned = await _scheduler.tick(now=_now)
        _repo.transition.assert_called_once_with("TODO-CRON", TodoStatus.CANCELLED, 1)

    async def test_invalid_cron_skipped(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-BAD", cron="not valid at all", run_count=0)
        _repo.list_due_scheduled.return_value = [todo]
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 0
        _repo.update.assert_not_called()

    async def test_advance_failure_skips_spawn(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=0)
        _repo.list_due_scheduled.return_value = [todo]
        _repo.update.side_effect = RuntimeError("advance failed")
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 0
        _repo.create.assert_not_called()

    async def test_child_create_fails_after_advance(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=0)
        _repo.list_due_scheduled.return_value = [todo]
        _repo.create.side_effect = RuntimeError("create failed")
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 0
        assert _repo.update.called

    async def test_max_runs_none_allows_unlimited(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=99, max_runs=None)
        _repo.list_due_scheduled.return_value = [todo]
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 1
        _repo.transition.assert_not_called()

    async def test_max_runs_cancel_failure_logged(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=5, max_runs=5)
        _repo.list_due_scheduled.return_value = [todo]
        _repo.transition.side_effect = RuntimeError("transition failed")
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 0

    async def test_default_timezone_is_utc(self, _repo, _now, _scheduler):
        todo = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=0)
        del todo.schedule_timezone
        type(todo).schedule_timezone = property(lambda self: None)
        _repo.list_due_scheduled.return_value = [todo]
        _promoted, spawned = await _scheduler.tick(now=_now)
        assert spawned == 1


class TestMixedWorkloads:
    async def test_one_shot_and_cron_in_same_tick(self, _repo, _now, _scheduler):
        one_shot = _make_due_todo("TODO-SHOT")
        cron_tpl = _make_due_todo("TODO-CRON", cron="0 * * * *", run_count=1)
        _repo.list_due_scheduled.return_value = [one_shot, cron_tpl]
        promoted, spawned = await _scheduler.tick(now=_now)
        assert promoted == 1
        assert spawned == 1
        _repo.transition.assert_called_once()
        _repo.create.assert_called_once()
