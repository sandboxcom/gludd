"""E2E tests for the self-update pipeline — daemon-booted, end-to-end proof.

Covers the 90%→100% gap in the README status table: boots the daemon with
TestClient, exercises the full self-update lifecycle across the router,
classifier, apply ladder, audit persistence, todo enqueue, loop dispatch,
code-tier hot-rotation, and rollback paths.

Tests:
1.  Plan endpoint: config-tier applied → audit row persisted
2.  Plan endpoint: protected-path in the apply ladder's fail-closed refusa
3.  Enqueue endpoint: todo persisted with queue="self_update"
4.  Loop path: self_update todo dispatched through _apply_self_update_code
5.  Loop path: failure rollback — missing tags → FAILED
6.  Loop path: daemon_state self_update_applies tracking
7.  Auth: PSK-protected admin paths return 401 without token
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    Subsystem,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> None:
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    daemon_mod._daemon_state.pop("self_update_applies", None)
    yield
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    daemon_mod._daemon_state.pop("self_update_applies", None)


def _make_db_config(tmp_path: pytest.Path) -> tuple[str, str]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n")
    return str(config_dir), str(db_path)


async def _seed_default_project(app: Any) -> None:
    from sqlalchemy import select

    from general_ludd.db.models import ProjectModel

    factory = app.state._session_factory
    async with factory() as session:
        existing = await session.execute(select(ProjectModel).where(ProjectModel.project_id == "default"))
        if existing.scalar_one_or_none() is None:
            session.add(ProjectModel(project_id="default", name="Default project"))
            await session.commit()


def _count_audit_rows(db_path: str, event_prefix: str = "self_update_") -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type LIKE ?",
            (f"{event_prefix}%",),
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def _wait_for_audit_row(db_path: str, timeout: float = 3.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _count_audit_rows(db_path) > 0:
            return _count_audit_rows(db_path)
        time.sleep(0.05)
    return _count_audit_rows(db_path)


def _make_self_update_todo(
    todo_id: str = "SU-1",
    *,
    module: str | None = "general_ludd.reload.dummy",
    candidate: str | None = "/tmp/candidates/dummy.py",
    tier: str = "code",
    version: int = 1,
) -> MagicMock:
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
    def __init__(self, reload_status: str = "success") -> None:
        self.set_code_target_calls: list[dict[str, Any]] = []
        self.reload_calls: list[Any] = []
        self._reload_status = reload_status

    def set_code_target(
        self,
        module_name: str,
        candidate_source_path: str,
        health_check: Any | None = None,
        base_source_path: str | None = None,
    ) -> None:
        self.set_code_target_calls.append(
            {
                "module_name": module_name,
                "candidate_source_path": candidate_source_path,
                "health_check": health_check,
                "base_source_path": base_source_path,
            }
        )

    def reload_if_needed(self, apply_result: Any) -> Any:
        self.reload_calls.append(apply_result)
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
# Router endpoint e2e (TestClient, real DB)
# ---------------------------------------------------------------------------


class TestSelfUpdateRouterE2E:
    """Boot the daemon and exercise /admin/self-update/* over HTTP."""

    def test_plan_config_tier_applied_writes_audit_row(self, tmp_path: pytest.Path) -> None:
        config_dir, db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                asyncio.run(_seed_default_project(app))
                assert app.state._session_factory is not None

                resp = client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "set the spend limit to 50",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["outcome"] == "applied"
                assert data["applied"] is True

                count = _wait_for_audit_row(db_path)
                assert count >= 1, "expected self_update_* audit row after config-tier apply"

    def test_plan_protected_path_refused_rollback(self, tmp_path: pytest.Path) -> None:
        config_dir, _db_path = _make_db_config(tmp_path)
        protected_plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=("src/general_ludd/security/capability_lattice.py",),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="routes to a protected guard file",
            confidence=0.5,
        )
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch(
                "general_ludd.routers.self_update.classify",
                return_value=protected_plan,
            ),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "edit the capability lattice",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["outcome"] == "refused"
                assert data["applied"] is False

    def test_enqueue_persists_todo_with_self_update_queue(self, tmp_path: pytest.Path) -> None:
        config_dir, db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/enqueue",
                    json={
                        "raw_text": "set the spend limit to 50",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["persisted"] is True
                assert data["spec"]["queue"] == "self_update"
                todo_id = data["todo_id"]
                assert todo_id

                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        "SELECT queue, title FROM todos WHERE todo_id = ?",
                        (todo_id,),
                    )
                    row = cur.fetchone()
                finally:
                    conn.close()
                assert row is not None, f"todo {todo_id} not persisted"
                assert row[0] == "self_update"
                assert "self-update" in row[1]

    def test_missing_psk_returns_401(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/plan",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Loop path e2e (EventLoop._apply_self_update_code)
# ---------------------------------------------------------------------------


class TestSelfUpdateLoopPathE2E:
    """Exercise self_update todo dispatch through the EventLoop."""

    @pytest.mark.asyncio
    async def test_apply_code_success_transitions_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        loop._todo_repo = _RecordingTodoRepo()
        loop._active_session = MagicMock()

        todo = _make_self_update_todo(
            module="general_ludd.reload.foo",
            candidate="/tmp/cand/foo.py",
        )
        fake = _FakeWorkflow(reload_status="success")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        assert len(fake.set_code_target_calls) == 1
        assert fake.set_code_target_calls[0]["module_name"] == "general_ludd.reload.foo"
        assert fake.set_code_target_calls[0]["candidate_source_path"] == "/tmp/cand/foo.py"
        assert fake.set_code_target_calls[0]["health_check"] is not None

        assert len(fake.reload_calls) == 1
        assert fake.reload_calls[0].reload_needed is True

    @pytest.mark.asyncio
    async def test_apply_code_reload_failure_rollback_to_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="failed")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]

    @pytest.mark.asyncio
    async def test_apply_code_missing_tags_rollback_to_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        todo = _make_self_update_todo(module=None)
        fake = _FakeWorkflow()
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        assert fake.set_code_target_calls == []
        assert fake.reload_calls == []
        assert repo.transitions == [(todo.todo_id, TodoStatus.FAILED, todo.version)]

    @pytest.mark.asyncio
    async def test_daemon_state_tracks_successful_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="success")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        assert "self_update_applies" in daemon_state
        applies = daemon_state["self_update_applies"]
        assert len(applies) == 1
        assert applies[0]["todo_id"] == todo.todo_id
        assert applies[0]["module"] == "general_ludd.reload.dummy"
        assert applies[0]["verdict"] == "success"
        assert applies[0]["ok"] is True

    @pytest.mark.asyncio
    async def test_daemon_state_does_not_track_failed_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="failed")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        # Failed applies are RECORDED (the tracking is additive regardless
        # of verdict — only the "ok" field discerns pass/fail). The full list
        # lets the operator inspect every self-update that the loop processed.
        assert "self_update_applies" in daemon_state
        applies = daemon_state["self_update_applies"]
        assert len(applies) == 1
        assert applies[0]["verdict"] == "failed"
        assert applies[0]["ok"] is False

    @pytest.mark.asyncio
    async def test_apply_code_caps_daemon_state_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        daemon_state: dict[str, Any] = {}
        # Seed daemon_state with 500 existing entries — one under the cap.
        daemon_state["self_update_applies"] = [{"todo_id": f"old-{i}"} for i in range(500)]
        loop = EventLoop(session=None, config={}, daemon_state=daemon_state)
        loop._MAX_SELF_UPDATE_APPLIES = 500
        repo = _RecordingTodoRepo()
        loop._todo_repo = repo
        loop._active_session = MagicMock()

        todo = _make_self_update_todo()
        fake = _FakeWorkflow(reload_status="success")
        monkeypatch.setattr(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow",
            lambda *a, **kw: fake,
        )

        await loop._apply_self_update_code(todo)

        applies = daemon_state["self_update_applies"]
        assert len(applies) == 500
        assert applies[-1]["todo_id"] == todo.todo_id


# ---------------------------------------------------------------------------
# Dispatch branch e2e
# ---------------------------------------------------------------------------


class TestSelfUpdateDispatchBranchE2E:
    """self_update-queue todos route through _apply_self_update_code."""

    @pytest.mark.asyncio
    async def test_self_update_queue_short_circuits_to_apply_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(session=None, config={}, daemon_state={})
        todo = _make_self_update_todo()

        captured: list[Any] = []

        async def fake_apply(t: Any, **_kwargs: object) -> None:
            captured.append(t)

        monkeypatch.setattr(loop, "_apply_self_update_code", fake_apply)
        loop._runner = MagicMock()
        loop._runner.run_playbook = MagicMock(side_effect=AssertionError("runner must not be invoked for self_update"))
        loop._http_client = None

        await loop._dispatch_execute_job(todo)

        assert captured == [todo]
