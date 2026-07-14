"""C13: Self-improve gate bypasses — tests.

Three bypasses fixed:
1. auto_queue removed (no config-driven bypass)
2. allow_auto_promote backdoor removed
3. Human-approval path reachable end-to-end
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.approval import (
    SELF_IMPROVE_WORK_TYPE,
    ApprovalError,
    SelfImproveApprovalManager,
)
from general_ludd.self_improve.gate import GateDecision, SelfImproveGate


class TestAutoQueueRemoved:
    def test_auto_queue_is_not_a_parameter(self) -> None:
        import inspect

        sig = inspect.signature(SelfImproveGate.__init__)
        param_names = list(sig.parameters.keys())
        assert "auto_queue" not in param_names, (
            "auto_queue parameter must be removed from SelfImproveGate (C13 bypass)"
        )

    def test_constructing_with_auto_queue_raises(self) -> None:
        with pytest.raises(TypeError):
            SelfImproveGate(auto_queue=True)  # type: ignore[call-arg]

    def test_auto_queue_from_config_is_not_consumed(self) -> None:
        gate = SelfImproveGate()
        assert not hasattr(gate, "auto_queue"), (
            "SelfImproveGate must not have auto_queue attribute (C13 bypass)"
        )


class TestAutoPromoteBackdoorRemoved:
    def test_auto_promote_is_not_a_parameter(self) -> None:
        import inspect

        sig = inspect.signature(SelfImproveGate.__init__)
        param_names = list(sig.parameters.keys())
        assert "allow_auto_promote" not in param_names, (
            "allow_auto_promote parameter must be removed from SelfImproveGate"
        )

    def test_constructing_with_auto_promote_raises(self) -> None:
        with pytest.raises(TypeError):
            SelfImproveGate(allow_auto_promote=True)  # type: ignore[call-arg]

    def test_auto_promote_from_config_is_not_consumed(self) -> None:
        gate = SelfImproveGate()
        assert not hasattr(gate, "allow_auto_promote"), (
            "SelfImproveGate must not have allow_auto_promote attribute"
        )


class TestAdminRunGoesThroughGate:
    def test_run_persist_uses_gate(self) -> None:
        import asyncio

        from general_ludd.routers.self_improve import (
            _persist_gated_self_improve_todos,
        )

        repo = MagicMock()
        repo.list_by_work_type = AsyncMock(return_value=[])
        repo.create = AsyncMock()
        fake_todo = MagicMock()
        fake_todo.todo_id = "test-1"
        repo.create.return_value = fake_todo

        todos: list[dict[str, object]] = [{"title": "Fix thing", "description": "desc"}]

        ids = asyncio.run(_persist_gated_self_improve_todos(repo, todos))

        repo.create.assert_called_once()
        call_kwargs = repo.create.call_args[0][0]
        assert call_kwargs["status"] == TodoStatus.APPROVAL_REQUIRED.value
        assert call_kwargs["work_type"] == SELF_IMPROVE_WORK_TYPE
        assert ids == ["test-1"]

    def test_persist_gated_creates_approval_required(self) -> None:
        import asyncio

        from general_ludd.routers.self_improve import (
            _persist_gated_self_improve_todos,
        )

        repo = MagicMock()
        repo.list_by_work_type = AsyncMock(return_value=[])
        repo.create = AsyncMock()
        fake_todo = MagicMock()
        fake_todo.todo_id = "gate-test-1"
        repo.create.return_value = fake_todo

        todos: list[dict[str, object]] = [{"title": "Test", "description": "desc"}]

        ids = asyncio.run(_persist_gated_self_improve_todos(repo, todos))

        assert len(ids) == 1, f"Expected 1 persisted id, got {ids}"
        call_kwargs = repo.create.call_args[0][0]
        assert call_kwargs["status"] == TodoStatus.APPROVAL_REQUIRED.value
        assert call_kwargs["work_type"] == SELF_IMPROVE_WORK_TYPE


class TestHumanApprovalPathReachable:
    def test_approval_manager_accepts_pending_todo(self) -> None:
        manager = SelfImproveApprovalManager()
        fake_todo = MagicMock()
        fake_todo.status = TodoStatus.APPROVAL_REQUIRED
        assert manager.is_pending_approval(fake_todo) is True

    def test_approval_manager_rejects_non_pending_todo(self) -> None:
        manager = SelfImproveApprovalManager()
        fake_todo = MagicMock()
        fake_todo.status = TodoStatus.QUEUED
        assert manager.is_pending_approval(fake_todo) is False

    def test_approval_manager_rejects_non_self_improve(self) -> None:
        import asyncio

        from general_ludd.schemas.todo import Todo, WorkType

        manager = SelfImproveApprovalManager()
        todo = Todo(
            todo_id="test-1",
            title="Test",
            status=TodoStatus.APPROVAL_REQUIRED,
            work_type=WorkType.CODE,
        )
        fake_store = MagicMock()
        fake_store.get_by_id = AsyncMock(return_value=todo)
        fake_store.transition = AsyncMock()

        with pytest.raises(ApprovalError, match="not a self-improve todo"):
            asyncio.run(manager.approve_by_id(fake_store, "test-1"))

    def test_approval_routes_registered_on_app(self) -> None:
        app = FastAPI()
        from general_ludd.routers.self_improve import register

        register(app, {})

        route_paths = {
            r.path
            for r in app.routes
            if hasattr(r, "path")
        }
        assert "/admin/self-improve/approvals" in route_paths
        assert any(
            p.startswith("/admin/self-improve/approvals/") and "approve" in p
            for p in route_paths
        )
        assert any(
            p.startswith("/admin/self-improve/approvals/") and "reject" in p
            for p in route_paths
        )


class TestSingleChokePoint:
    def test_all_callers_use_gate_evaluate(self) -> None:
        import inspect

        import general_ludd.event_loop.loop as loop_module
        import general_ludd.routers.self_improve as router_module

        router_source = inspect.getsource(router_module._persist_gated_self_improve_todos)
        assert "SelfImproveGate" in router_source
        assert "gate.evaluate" in router_source

        loop_source = inspect.getsource(loop_module.EventLoop._persist_self_improve_todos)
        assert "SelfImproveGate" in loop_source
        assert "gate.evaluate" in loop_source

    def test_gate_decision_is_always_used(self) -> None:
        gate = SelfImproveGate()
        decision = gate.evaluate({"title": "test"}, open_count=0)
        assert isinstance(decision, GateDecision)
        assert decision.admitted is True
        assert decision.initial_status == TodoStatus.APPROVAL_REQUIRED.value, (
            "Gate must always return APPROVAL_REQUIRED (auto_queue removed, C13)"
        )
