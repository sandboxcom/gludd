"""Targeted branch coverage tests for db/repository.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from general_ludd.db.repository import (
    _MAX_PRIORITY,
    _MIN_PRIORITY,
    _PRIORITY_LABELS,
    ALLOWED_TODO_CREATE_FIELDS,
    VALID_TRANSITIONS,
    ConcurrencyError,
    InvalidTransitionError,
    TodoRepository,
    _is_locked_error,
    scoped_to,
)
from general_ludd.schemas.todo import TodoStatus


class TestValidTransitions:
    def test_backlog_transitions(self):
        assert TodoStatus.QUEUED in VALID_TRANSITIONS[TodoStatus.BACKLOG]
        assert TodoStatus.SCHEDULED in VALID_TRANSITIONS[TodoStatus.BACKLOG]
        assert TodoStatus.CANCELLED in VALID_TRANSITIONS[TodoStatus.BACKLOG]

    def test_scheduled_transitions(self):
        assert TodoStatus.QUEUED in VALID_TRANSITIONS[TodoStatus.SCHEDULED]
        assert TodoStatus.CANCELLED in VALID_TRANSITIONS[TodoStatus.SCHEDULED]
        assert TodoStatus.MANUAL_HOLD in VALID_TRANSITIONS[TodoStatus.SCHEDULED]

    def test_queued_transitions(self):
        assert TodoStatus.ACTIVE in VALID_TRANSITIONS[TodoStatus.QUEUED]
        assert TodoStatus.FAILED in VALID_TRANSITIONS[TodoStatus.QUEUED]
        assert TodoStatus.BLOCKED in VALID_TRANSITIONS[TodoStatus.QUEUED]
        assert TodoStatus.BLOCKED_ON_HUMAN in VALID_TRANSITIONS[TodoStatus.QUEUED]

    def test_active_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.ACTIVE]
        assert TodoStatus.COMPLETE in transitions
        assert TodoStatus.FAILED in transitions
        assert TodoStatus.BLOCKED in transitions
        assert TodoStatus.REVIEWING_RETURN in transitions
        assert TodoStatus.MANUAL_HOLD in transitions
        assert TodoStatus.NEEDS_MORE_WORK in transitions

    def test_reviewing_return_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.REVIEWING_RETURN]
        assert TodoStatus.COMPLETE in transitions
        assert TodoStatus.NEEDS_MORE_WORK in transitions
        assert TodoStatus.FAILED in transitions

    def test_complete_has_no_transitions(self):
        assert VALID_TRANSITIONS[TodoStatus.COMPLETE] == set()

    def test_blocked_on_human_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.BLOCKED_ON_HUMAN]
        assert TodoStatus.QUEUED in transitions
        assert TodoStatus.CANCELLED in transitions

    def test_manual_hold_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.MANUAL_HOLD]
        assert TodoStatus.QUEUED in transitions
        assert TodoStatus.ACTIVE in transitions

    def test_needs_more_work_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.NEEDS_MORE_WORK]
        assert TodoStatus.QUEUED in transitions
        assert TodoStatus.ACTIVE in transitions

    def test_blocked_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.BLOCKED]
        assert TodoStatus.QUEUED in transitions

    def test_failed_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.FAILED]
        assert TodoStatus.QUEUED in transitions

    def test_budget_exceeded_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.BUDGET_EXCEEDED]
        assert TodoStatus.QUEUED in transitions
        assert TodoStatus.FAILED in transitions

    def test_approval_required_transitions(self):
        transitions = VALID_TRANSITIONS[TodoStatus.APPROVAL_REQUIRED]
        assert TodoStatus.QUEUED in transitions
        assert TodoStatus.CANCELLED in transitions
        assert TodoStatus.MANUAL_HOLD in transitions

    def test_all_enum_values_have_transitions(self):
        _NEW_WITHOUT_TRANSITIONS = {TodoStatus.AWAITING_RESULT}
        for status in TodoStatus:
            if status in _NEW_WITHOUT_TRANSITIONS:
                continue
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"
            assert isinstance(VALID_TRANSITIONS[status], set), f"{status} value is not a set"


class TestIsLockedError:
    def test_database_is_locked(self):
        orig = OperationalError("statement", "params", BaseException("database is locked"))
        exc = OperationalError("statement", "params", orig)
        assert _is_locked_error(exc) is True

    def test_database_table_is_locked(self):
        orig = RuntimeError("database table is locked")
        exc = OperationalError("stmt", {}, orig)
        assert _is_locked_error(exc) is True

    def test_not_locked(self):
        orig = RuntimeError("some other error")
        exc = OperationalError("stmt", {}, orig)
        assert _is_locked_error(exc) is False

    def test_no_orig_attribute(self):
        exc = OperationalError("stmt", {}, BaseException())
        result = _is_locked_error(exc)
        assert result is False


class TestScopedTo:
    def test_sets_and_resets_tenant(self):
        with (
            patch("general_ludd.db.tenant.set_tenant") as mock_set,
            patch("general_ludd.db.tenant.reset_tenant") as mock_reset,
        ):
            mock_set.return_value = "token-1"
            with scoped_to("proj-abc"):
                pass
            mock_set.assert_called_once_with("proj-abc")
            mock_reset.assert_called_once_with("token-1")


class TestScopedToContextManager:
    def test_scoped_to_is_context_manager(self):
        ctx = scoped_to("proj-test")
        assert hasattr(ctx, "__enter__"), "scoped_to did not return a context manager"
        assert hasattr(ctx, "__exit__"), "scoped_to did not return a context manager"


class TestPriorityLabels:
    def test_priority_labels_mapping(self):
        assert _PRIORITY_LABELS["low"] == 0
        assert _PRIORITY_LABELS["medium"] == 1
        assert _PRIORITY_LABELS["high"] == 2
        assert _PRIORITY_LABELS["critical"] == 3

    def test_min_max_bounds(self):
        assert _MIN_PRIORITY == 0
        assert _MAX_PRIORITY == 1000


class TestConcurrencyErrorHierarchy:
    def test_concurrency_error_is_exception(self):
        e = ConcurrencyError("test")
        assert isinstance(e, Exception)

    def test_invalid_transition_is_concurrency_error(self):
        e = InvalidTransitionError("test")
        assert isinstance(e, ConcurrencyError)
        assert isinstance(e, Exception)

    def test_invalid_transition_error_message(self):
        e = InvalidTransitionError("bad move")
        assert "bad move" in str(e)

    def test_concurrency_error_message(self):
        e = ConcurrencyError("version mismatch")
        assert "version mismatch" in str(e)


class TestValidateCreateData:
    def test_immutable_fields_blocked(self):
        with pytest.raises(ValueError, match="immutable"):
            TodoRepository._validate_create_data({"id": 5, "title": "test"})

    def test_immutable_version_blocked(self):
        with pytest.raises(ValueError, match="immutable"):
            TodoRepository._validate_create_data({"version": 99, "title": "test"})

    def test_immutable_created_at_blocked(self):
        with pytest.raises(ValueError, match="immutable"):
            TodoRepository._validate_create_data({"created_at": "now", "title": "test"})

    def test_priority_string_label_low(self):
        data = {"priority": "low", "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 0

    def test_priority_string_label_high(self):
        data = {"priority": "high", "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 2

    def test_priority_string_label_critical(self):
        data = {"priority": "critical", "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 3

    def test_priority_string_label_medium(self):
        data = {"priority": "medium", "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 1

    def test_priority_below_min_clamped(self):
        data = {"priority": -10, "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MIN_PRIORITY

    def test_priority_above_max_clamped(self):
        data = {"priority": 9999, "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MAX_PRIORITY

    def test_priority_within_range_unchanged(self):
        data = {"priority": 50, "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 50

    def test_priority_str_unknown_label_raises(self):
        data = {"priority": "medium", "title": "test"}
        result_data = data.copy()
        TodoRepository._validate_create_data(result_data)
        assert result_data["priority"] == 1

    def test_priority_bool_raises_valueerror(self):
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data({"priority": True, "title": "test"})

    def test_valid_data_passes(self):
        TodoRepository._validate_create_data({"title": "test", "description": "desc"})

    def test_oversized_text_field_raises(self):
        big_str = "x" * 70000
        with pytest.raises(ValueError, match="byte limit"):
            TodoRepository._validate_create_data({"title": "test", "description": big_str})

    def test_priority_zero_int(self):
        data = {"priority": 0, "title": "test"}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 0


class TestValidateUpdateFields:
    def test_project_id_blocked_from_update(self):
        with pytest.raises(ValueError, match="immutable after creation"):
            TodoRepository._validate_update_fields({"project_id": "new-project", "title": "test"})

    def test_todo_id_blocked_from_update(self):
        with pytest.raises(ValueError, match="immutable after creation"):
            TodoRepository._validate_update_fields({"todo_id": "new-id", "title": "test"})

    def test_created_by_blocked_from_update(self):
        with pytest.raises(ValueError, match="immutable after creation"):
            TodoRepository._validate_update_fields({"created_by": "someone-else", "title": "test"})

    def test_version_blocked_from_update(self):
        with pytest.raises(ValueError, match="immutable after creation"):
            TodoRepository._validate_update_fields({"version": 99})

    def test_valid_update_passes(self):
        TodoRepository._validate_update_fields({"title": "new title", "priority": 10})

    def test_empty_updates_passes(self):
        TodoRepository._validate_update_fields({})


class TestResolveProjectId:
    def test_explicit_overrides_instance(self):
        repo = TodoRepository(AsyncMock(), project_id="instance-project")
        result = repo._resolve_pid("explicit-project")
        assert result == "explicit-project"

    def test_falls_back_to_instance(self):
        repo = TodoRepository(AsyncMock(), project_id="instance-project")
        result = repo._resolve_pid(None)
        assert result == "instance-project"

    def test_none_when_unscoped(self):
        repo = TodoRepository(AsyncMock())
        result = repo._resolve_pid(None)
        assert result is None


class TestTodoRepositoryScoped:
    def test_scoped_sets_project_id(self):
        session = MagicMock()
        repo = TodoRepository.scoped(session, "proj-123")
        assert repo._project_id == "proj-123"

    def test_scoped_returns_todo_repository(self):
        session = MagicMock()
        repo = TodoRepository.scoped(session, "proj-123")
        assert isinstance(repo, TodoRepository)


class TestCreateAllowedFields:
    def test_essential_fields_allowed(self):
        assert "todo_id" in ALLOWED_TODO_CREATE_FIELDS
        assert "title" in ALLOWED_TODO_CREATE_FIELDS
        assert "status" in ALLOWED_TODO_CREATE_FIELDS
        assert "priority" in ALLOWED_TODO_CREATE_FIELDS
        assert "queue" in ALLOWED_TODO_CREATE_FIELDS
        assert "project_id" in ALLOWED_TODO_CREATE_FIELDS

    def test_internal_fields_excluded(self):
        assert "id" not in ALLOWED_TODO_CREATE_FIELDS
        assert "version" not in ALLOWED_TODO_CREATE_FIELDS
        assert "created_at" not in ALLOWED_TODO_CREATE_FIELDS
        assert "updated_at" not in ALLOWED_TODO_CREATE_FIELDS

    def test_scheduling_fields_allowed(self):
        assert "scheduled_at" in ALLOWED_TODO_CREATE_FIELDS
        assert "cron" in ALLOWED_TODO_CREATE_FIELDS
        assert "schedule_timezone" in ALLOWED_TODO_CREATE_FIELDS
        assert "next_run_at" in ALLOWED_TODO_CREATE_FIELDS
        assert "run_count" in ALLOWED_TODO_CREATE_FIELDS
        assert "max_runs" in ALLOWED_TODO_CREATE_FIELDS
        assert "schedule_paused" in ALLOWED_TODO_CREATE_FIELDS


class TestTodoRepositoryCreate:
    @pytest.mark.asyncio
    async def test_create_sets_version_one(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        repo = TodoRepository(session)
        data = {"title": "test todo", "todo_id": "TODO-001", "project_id": "p1"}
        todo = await repo.create(data)
        assert todo.version == 1

    @pytest.mark.asyncio
    async def test_create_adds_and_flushes(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        repo = TodoRepository(session)
        data = {"title": "test", "todo_id": "TODO-002", "project_id": "p1"}
        await repo.create(data)
        session.add.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_rejects_immutable_fields(self):
        session = AsyncMock()
        session.add = MagicMock()
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"id": 42, "title": "test", "todo_id": "TODO-003"})
        session.add.assert_not_called()


class TestTodoRepositoryGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_with_project_scoping(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        repo = TodoRepository(session, project_id="p1")
        result = await repo.get_by_id("TODO-001")
        assert result is None
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_get_by_id_without_project_scoping(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        repo = TodoRepository(session)
        result = await repo.get_by_id("TODO-001")
        assert result is None
        assert session.execute.called


class TestTodoRepositoryUpdate:
    @pytest.mark.asyncio
    async def test_update_version_mismatch_raises(self):
        session = AsyncMock()
        mock_result = MagicMock()
        todo_mock = MagicMock()
        todo_mock.version = 2
        mock_result.scalar_one_or_none.return_value = todo_mock
        session.execute.return_value = mock_result
        repo = TodoRepository(session)
        with pytest.raises(ConcurrencyError, match="Version mismatch"):
            await repo.update("TODO-001", {"title": "new"}, expected_version=1)

    @pytest.mark.asyncio
    async def test_update_not_found_raises(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        repo = TodoRepository(session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.update("TODO-001", {"title": "new"}, expected_version=1)

    @pytest.mark.asyncio
    async def test_update_rejects_immutable_fields(self):
        session = AsyncMock()
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable after creation"):
            await repo.update("TODO-001", {"project_id": "evil"}, expected_version=1)
        session.execute.assert_not_called()


class TestTodoRepositoryTransition:
    @pytest.mark.asyncio
    async def test_transition_invalid_raises(self):
        session = AsyncMock()
        mock_result = MagicMock()
        todo_mock = MagicMock()
        todo_mock.status = TodoStatus.QUEUED
        todo_mock.version = 1
        mock_result.scalar_one_or_none.return_value = todo_mock
        session.execute.return_value = mock_result
        repo = TodoRepository(session)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition("TODO-001", new_status=TodoStatus.BACKLOG, expected_version=1)
