"""Structural tests for self_improve/approval.py — human-gated self-improve todo approval."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from general_ludd.schemas.todo import Todo, TodoStatus
from general_ludd.self_improve.approval import (
    SELF_IMPROVE_WORK_TYPE,
    ApprovalError,
    SelfImproveApprovalManager,
)


def _make_todo(
    todo_id: str = "t1",
    title: str = "test task",
    status: TodoStatus = TodoStatus.APPROVAL_REQUIRED,
) -> Todo:
    todo = Todo(
        todo_id=todo_id,
        title=title,
        resource_profile="low_resource",
        plan_artifact=json.dumps(
            {
                "capability_required": "config_write",
                "change_content": "enabled: true\n",
                "kind": "config",
                "reason": "unit test",
                "target_paths": ["config/test.yml"],
            }
        ),
    )
    todo.status = status
    return todo


class TestConstants:
    def test_self_improve_work_type(self) -> None:
        assert isinstance(SELF_IMPROVE_WORK_TYPE, str)
        assert SELF_IMPROVE_WORK_TYPE == "self_improve"


class TestApprovalError:
    def test_is_exception(self) -> None:
        err = ApprovalError("test message")
        assert isinstance(err, Exception)

    def test_message_preserved(self) -> None:
        err = ApprovalError("cannot approve")
        assert str(err) == "cannot approve"


class TestSelfImproveApprovalManagerConstruction:
    def test_constructs(self) -> None:
        mgr = SelfImproveApprovalManager()
        assert isinstance(mgr, SelfImproveApprovalManager)


class TestIsPendingApproval:
    def test_pending_returns_true(self) -> None:
        mgr = SelfImproveApprovalManager()
        assert mgr.is_pending_approval(_make_todo("t1", status=TodoStatus.APPROVAL_REQUIRED)) is True

    def test_queued_returns_false(self) -> None:
        mgr = SelfImproveApprovalManager()
        assert mgr.is_pending_approval(_make_todo("t2", status=TodoStatus.QUEUED)) is False

    def test_active_returns_false(self) -> None:
        mgr = SelfImproveApprovalManager()
        assert mgr.is_pending_approval(_make_todo("t3", status=TodoStatus.ACTIVE)) is False


class TestApprove:
    def test_approve_pending_todo(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t1", status=TodoStatus.APPROVAL_REQUIRED)
        result = mgr.approve(todo)
        assert result.status == TodoStatus.APPROVED
        assert result.approved_artifact_digest is not None
        assert result is todo

    def test_approve_non_pending_raises(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t2", status=TodoStatus.QUEUED)
        with pytest.raises(ApprovalError, match="not awaiting approval"):
            mgr.approve(todo)


class TestReject:
    def test_reject_pending_todo(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t1", status=TodoStatus.APPROVAL_REQUIRED)
        result = mgr.reject(todo, reason="not needed")
        assert result.status == TodoStatus.CANCELLED

    def test_reject_with_reason(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t1", status=TodoStatus.APPROVAL_REQUIRED)
        result = mgr.reject(todo, reason="duplicate work")
        assert result.manual_hold_reason == "duplicate work"

    def test_reject_without_reason(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t1", status=TodoStatus.APPROVAL_REQUIRED)
        result = mgr.reject(todo)
        assert result.status == TodoStatus.CANCELLED

    def test_reject_non_pending_raises(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _make_todo("t2", status=TodoStatus.BACKLOG)
        with pytest.raises(ApprovalError, match="not awaiting approval"):
            mgr.reject(todo)


class TestRepositoryBackedListPending:
    def test_list_pending_filters_self_improve(self) -> None:
        mgr = SelfImproveApprovalManager()

        class FakeRow:
            pass

        r1 = FakeRow()
        r1.work_type = "self_improve"
        r2 = FakeRow()
        r2.work_type = "code_review"

        store = AsyncMock()
        store.list_by_status.return_value = [r1, r2]

        async def _run() -> None:
            rows = await mgr.list_pending(store)
            assert len(rows) == 1
            assert rows[0].work_type == "self_improve"

        import asyncio
        asyncio.run(_run())


class TestApproveById:
    def test_approve_by_id_success(self) -> None:
        mgr = SelfImproveApprovalManager()

        class FakeRow:
            pass

        row = FakeRow()
        row.status = "approval_required"
        row.work_type = "self_improve"
        row.todo_id = "t1"
        row.project_id = None
        row.approval_policy = "none"
        row.plan_artifact = _make_todo().plan_artifact
        row.version = 1

        digested = FakeRow()
        digested.status = row.status
        digested.work_type = row.work_type
        digested.todo_id = row.todo_id
        digested.project_id = row.project_id
        digested.approval_policy = row.approval_policy
        digested.plan_artifact = row.plan_artifact
        digested.version = 2

        store = AsyncMock()
        store.get_by_id.return_value = row
        store.update.return_value = digested
        store.transition.return_value = row

        async def _run() -> None:
            result = await mgr.approve_by_id(store, "t1")
            assert result is row

        import asyncio
        asyncio.run(_run())

    def test_approve_by_id_not_found(self) -> None:
        mgr = SelfImproveApprovalManager()
        store = AsyncMock()
        store.get_by_id.return_value = None

        async def _run() -> None:
            with pytest.raises(ApprovalError, match="not found"):
                await mgr.approve_by_id(store, "t-missing")

        import asyncio
        asyncio.run(_run())

    def test_approve_by_id_wrong_work_type(self) -> None:
        mgr = SelfImproveApprovalManager()

        class FakeRow:
            pass

        row = FakeRow()
        row.status = "approval_required"
        row.work_type = "code_review"
        row.version = 1

        store = AsyncMock()
        store.get_by_id.return_value = row

        async def _run() -> None:
            with pytest.raises(ApprovalError, match="not a self-improve todo"):
                await mgr.approve_by_id(store, "t1")

        import asyncio
        asyncio.run(_run())


class TestRejectById:
    def test_reject_by_id_success(self) -> None:
        mgr = SelfImproveApprovalManager()

        class FakeRow:
            pass

        row = FakeRow()
        row.status = "approval_required"
        row.work_type = "self_improve"
        row.version = 1

        store = AsyncMock()
        store.get_by_id.return_value = row
        store.transition.return_value = row

        async def _run() -> None:
            result = await mgr.reject_by_id(store, "t1")
            assert result is row

        import asyncio
        asyncio.run(_run())

    def test_reject_by_id_with_reason(self) -> None:
        mgr = SelfImproveApprovalManager()

        class FakeRow:
            pass

        row = FakeRow()
        row.status = "approval_required"
        row.work_type = "self_improve"
        row.version = 1

        row2 = FakeRow()
        row2.version = 2
        row2.manual_hold_reason = "not needed"

        store = AsyncMock()
        store.get_by_id.return_value = row
        store.transition.return_value = row
        store.update.return_value = row2

        async def _run() -> None:
            result = await mgr.reject_by_id(store, "t1", reason="not needed")
            assert result is row2

        import asyncio
        asyncio.run(_run())
