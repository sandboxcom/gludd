"""Unit tests for SelfImproveApprovalManager (F8 — approve/reject gate)."""

from __future__ import annotations

import pytest

from general_ludd.schemas.todo import Todo, TodoStatus
from general_ludd.self_improve.approval import (
    ApprovalError,
    SelfImproveApprovalManager,
)


def _held_todo() -> Todo:
    """A self-improve todo parked in APPROVAL_REQUIRED."""
    return Todo(
        title="Self-improvement: add tests for foo.py",
        work_type="test",
        status=TodoStatus.APPROVAL_REQUIRED,
        created_by="self_improve_harness",
    )


class TestApprove:
    def test_approve_releases_to_queued(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _held_todo()
        result = mgr.approve(todo)
        assert result.status == TodoStatus.QUEUED
        assert result is todo  # mutated in place

    def test_is_pending_approval_true_when_held(self) -> None:
        assert SelfImproveApprovalManager().is_pending_approval(_held_todo())

    def test_approve_non_pending_raises(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = Todo(title="already queued", status=TodoStatus.QUEUED)
        with pytest.raises(ApprovalError, match="not awaiting approval"):
            mgr.approve(todo)

    def test_approve_completed_raises(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = Todo(title="done", status=TodoStatus.COMPLETE)
        with pytest.raises(ApprovalError):
            mgr.approve(todo)


class TestReject:
    def test_reject_cancels(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _held_todo()
        result = mgr.reject(todo, reason="not worth it")
        assert result.status == TodoStatus.CANCELLED
        assert result.manual_hold_reason == "not worth it"

    def test_reject_non_pending_raises(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = Todo(title="active", status=TodoStatus.ACTIVE)
        with pytest.raises(ApprovalError, match="not awaiting approval"):
            mgr.reject(todo)

    def test_reject_without_reason_leaves_reason_unset(self) -> None:
        mgr = SelfImproveApprovalManager()
        todo = _held_todo()
        mgr.reject(todo)
        assert todo.status == TodoStatus.CANCELLED
        assert todo.manual_hold_reason is None
