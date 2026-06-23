"""Phase-2 Step 6: ``set_code_target`` arming + ``reload_if_needed`` wiring.

``EventLoop._apply_self_update_code`` is the critical gap blocking end-to-end
self-updates: it reads ``candidate:<path>`` and ``module:<name>`` tags from a
``self_update``-queue todo, arms a ``SelfImprovementWorkflow.set_code_target``
hot-rotation, calls ``reload_if_needed`` with an ``ApplyResult``, and
transitions the todo COMPLETE or FAILED based on the reload verdict.

The dispatch branch in ``_dispatch_execute_job`` routes any
``todo.queue == "self_update"`` todo through this method instead of the
normal Ansible/HTTP execute path, so Step-5 scheduler serialization feeds
directly into Step-6 code-tier application.

The health probe is in-process (reads ``daemon_state["_degraded"]``) rather
than HTTP — mirroring ``app.state._degraded`` semantics described in
``reload/hot_reloader.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop(daemon_state: dict[str, Any] | None = None) -> Any:
    """EventLoop with the minimal attribute surface Step 6 needs."""
    from general_ludd.event_loop.loop import EventLoop

    loop = EventLoop(
        session=None,
        config={},
        daemon_state=daemon_state if daemon_state is not None else {},
    )
    loop._session_factory = None
    loop._runner = None
    loop._http_client = None
    loop._budget_guard = None
    loop._mcp_tool_registry = None
    loop._adaptive_router = None
    loop._prompt_registry = None
    loop._skill_registry = None
    loop._variable_repo = None
    loop._task_return_repo = None
    loop._active_session = None
    loop._todo_repo = None
    return loop


def _make_self_update_todo(
    todo_id: str = "SU-1",
    *,
    module: str | None = "general_ludd.reload.dummy",
    candidate: str | None = "/tmp/candidates/dummy.py",
    tier: str = "code",
    version: int = 1,
) -> MagicMock:
    """A self_update-queue todo with the tags Step 6 reads."""
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = "self_update"
    todo.work_type = "infra"
    todo.priority = "high"
    todo.title = f"su-{todo_id}"
    todo.description = ""
    todo.prompt_profile = None
    todo.model_profile = None
    todo.plan_artifact = None
    todo.version = version
    todo.status = "active"
    tags = ["self-update", f"tier:{tier}"]
    if module is not None:
        tags.append(f"module:{module}")
    if candidate is not None:
        tags.append(f"candidate:{candidate}")
    todo.tags = tags
    type(todo).project_id = property(lambda self: None)
    return todo


class _FakeWorkflow:
    """Test double for SelfImprovementWorkflow that records calls."""

    def __init__(self, reload_status: str = "success") -> None:
        self.set_code_target_calls: list[dict[str, Any]] = []
        self.reload_calls: list[Any] = []
        self._reload_status = reload_status

    def set_code_target(
        self,
        module_name: str,
        candidate_source_path: str,
        health_check: Any | None = None,
    ) -> None:
        self.set_code_target_calls.append({
            "module_name": module_name,
            "candidate_source_path": candidate_source_path,
            "health_check": health_check,
        })

    def reload_if_needed(self, apply_result: Any) -> Any:
        self.reload_calls.append(apply_result)
        # Build a manager-style ReloadResult stand-in. reload.manager.ReloadResult
        # is a dataclass; we use the real one so status fields line up.
        from general_ludd.reload.manager import ReloadResult, ReloadType

        status = self._reload_status
        message = "ok" if status == "success" else "reload failed"
        return ReloadResult(
            reload_id="test-id",
            reload_type=ReloadType.WORKER_CODE,
            status=status,
            message=message,
        )


class _RecordingTodoRepo:
    """Captures transition() calls without touching a DB session."""

    def __init__(self) -> None:
        self.transitions: list[tuple[str, Any, int]] = []

    async def transition(
        self,
        todo_id: str,
        new_status: Any,
        expected_version: int,
        project_id: str | None = None,
    ) -> Any:
        self.transitions.append((todo_id, new_status, expected_version))
        result = MagicMock()
        result.todo_id = todo_id
        result.status = new_status.value if hasattr(new_status, "value") else new_status
        return result


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------


class TestApplySelfUpdateCodeReadsTags:
    """``_apply_self_update_code`` reads ``candidate:`` and ``module:`` tags."""

    @pytest.mark.asyncio
    async def test_reads_candidate_and_module_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop = _make_loop()
        todo = _make_self_update_todo(
            module="general_ludd.reload.foo",
            candidate="/tmp/cand/foo.py",
        )
        fake = _FakeWorkflow(reload_status="success")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        assert len(fake.set_code_target_calls) == 1
        call = fake.set_code_target_calls[0]
        assert call["module_name"] == "general_ludd.reload.foo"
        assert call["candidate_source_path"] == "/tmp/cand/foo.py"

    @pytest.mark.asyncio
    async def test_health_check_passed_to_set_code_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop = _make_loop(daemon_state={})
        todo = _make_self_update_todo()
        fake = _FakeWorkflow()
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        loop._todo_repo = _RecordingTodoRepo()
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        assert fake.set_code_target_calls[0]["health_check"] is not None

    @pytest.mark.asyncio
    async def test_missing_module_tag_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``module:`` tag → no reload attempted, todo → FAILED."""
        loop = _make_loop()
        todo = _make_self_update_todo(module=None)
        fake = _FakeWorkflow()
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        assert fake.set_code_target_calls == []
        assert fake.reload_calls == []
        # Must fail CLOSED: status -> FAILED.
        from general_ludd.schemas.todo import TodoStatus

        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]

    @pytest.mark.asyncio
    async def test_missing_candidate_tag_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``candidate:`` tag → no reload attempted, todo → FAILED."""
        loop = _make_loop()
        todo = _make_self_update_todo(candidate=None)
        fake = _FakeWorkflow()
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        assert fake.set_code_target_calls == []
        assert fake.reload_calls == []
        from general_ludd.schemas.todo import TodoStatus

        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]


# ---------------------------------------------------------------------------
# Reload verdict -> todo transition
# ---------------------------------------------------------------------------


class TestApplySelfUpdateCodeReloadTransition:
    """``reload_if_needed`` verdict maps onto the todo state transition."""

    @pytest.mark.asyncio
    async def test_successful_reload_transitions_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop = _make_loop()
        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="success")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        from general_ludd.schemas.todo import TodoStatus

        assert repo.transitions == [(todo.todo_id, TodoStatus.COMPLETE, todo.version)]
        # Confirm reload_if_needed was actually called with a reload_needed=True result.
        assert len(fake.reload_calls) == 1
        assert fake.reload_calls[0].reload_needed is True

    @pytest.mark.asyncio
    async def test_failed_reload_transitions_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop = _make_loop()
        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="failed")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        from general_ludd.schemas.todo import TodoStatus

        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]

    @pytest.mark.asyncio
    async def test_no_op_reload_transitions_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``no_op`` reload (manager fallback) is NOT success — fail closed.

        Mirrors ReloadManager.execute_reload's BUG#2 fix: an in-memory no-op
        cannot be treated as a verified live swap. Step 6 must mark the todo
        FAILED so a self-update is never silently "applied" without effect.
        """
        loop = _make_loop()
        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="no_op")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        from general_ludd.schemas.todo import TodoStatus

        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


class TestHealthProbe:
    """The in-process health probe reads ``daemon_state["_degraded"]``."""

    def test_probe_returns_true_when_no_degraded_flag(self) -> None:
        loop = _make_loop(daemon_state={})
        probe = loop._make_daemon_health_probe()
        assert probe() is True

    def test_probe_returns_false_when_degraded_set(self) -> None:
        loop = _make_loop(daemon_state={"_degraded": "db connection lost"})
        probe = loop._make_daemon_health_probe()
        assert probe() is False

    def test_probe_returns_true_when_daemon_state_none(self) -> None:
        loop = _make_loop(daemon_state={})
        loop._daemon_state = None
        # No daemon state to observe — assume healthy (fail-OPEN on observability;
        # fail-CLOSED on the actual reload verdict, which is independent).
        probe = loop._make_daemon_health_probe()
        assert probe() is True

    @pytest.mark.asyncio
    async def test_probe_wired_into_set_code_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The health_check passed to set_code_target reflects daemon_state."""
        loop = _make_loop(daemon_state={"_degraded": "degraded"})
        todo = _make_self_update_todo()
        fake = _FakeWorkflow()
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )
        loop._todo_repo = _RecordingTodoRepo()
        loop._active_session = MagicMock()

        await loop._apply_self_update_code(todo)

        health_check = fake.set_code_target_calls[0]["health_check"]
        assert health_check is not None
        # The daemon is degraded → the probe reports unhealthy.
        assert health_check() is False


# ---------------------------------------------------------------------------
# Dispatch branch
# ---------------------------------------------------------------------------


class TestSelfUpdateDispatchBranch:
    """``_dispatch_execute_job`` routes ``self_update`` todos to Step 6."""

    @pytest.mark.asyncio
    async def test_self_update_queue_routes_to_apply_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``self_update``-queue todo MUST NOT go through the regular
        Ansible/HTTP execute path — it goes through ``_apply_self_update_code``.
        """
        loop = _make_loop()
        todo = _make_self_update_todo()

        called: list[Any] = []

        async def fake_apply(t: Any, **_kwargs: object) -> None:
            called.append(t)

        monkeypatch.setattr(loop, "_apply_self_update_code", fake_apply)
        # If the branch fails to short-circuit, _dispatch_execute_job would
        # try runner / http_client. Make any of those surfaces noisy alarms.
        loop._runner = MagicMock()
        loop._runner.run_playbook = MagicMock(
            side_effect=AssertionError("runner must not be invoked for self_update")
        )
        loop._http_client = None  # triggers early return instead of HTTP

        await loop._dispatch_execute_job(todo)

        assert called == [todo]

    @pytest.mark.asyncio
    async def test_core_queue_skips_apply_code_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A core-queue todo MUST NOT route through ``_apply_self_update_code``."""
        loop = _make_loop()
        todo = MagicMock()
        todo.todo_id = "C-1"
        todo.queue = "core"
        todo.work_type = "code"
        todo.priority = "medium"
        todo.title = "core todo"
        todo.description = ""
        todo.prompt_profile = None
        todo.model_profile = None
        todo.plan_artifact = None
        todo.tags = []
        todo.version = 1
        type(todo).project_id = property(lambda self: None)

        apply_called: list[Any] = []

        async def fake_apply(t: Any) -> None:
            apply_called.append(t)

        monkeypatch.setattr(loop, "_apply_self_update_code", fake_apply)
        loop._runner = None
        loop._http_client = None  # _dispatch_execute_job returns early; no dispatch

        await loop._dispatch_execute_job(todo)

        assert apply_called == []
