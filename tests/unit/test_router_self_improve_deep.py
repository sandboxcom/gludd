"""Deep unit tests for routers/self_improve.py internal functions.

Covers _ConfigTierCapabilityChecker, _persist_gated_self_improve_todos,
_enqueue_config_change, _apply_approved_config_change, _enqueue_non_config_change,
and _get_session_factory — all the helper functions the wiring-level tests
do NOT exercise directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import ProjectModel
from general_ludd.db.repository import TodoRepository
from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def inmemory_factory():
    """In-memory SQLite session factory with tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await ensure_tables(engine)
    factory = create_async_session_factory(engine)
    yield factory
    await engine.dispose()


async def _non_config_workspace(inmemory_factory, tmp_path: Path) -> tuple[Path, Path]:
    """Create one persisted project and an existing confined worktree."""
    workspace_root = tmp_path / "project-workspace"
    repo_root = workspace_root / "repo"
    worktree = repo_root / "worktrees" / "approved"
    worktree.mkdir(parents=True)
    async with inmemory_factory() as session:
        session.add(
            ProjectModel(
                project_id="project-1",
                name="Project one",
                workspace_path=str(workspace_root),
            )
        )
        await session.commit()
    return repo_root, worktree


# ---------------------------------------------------------------------------
# _get_session_factory
# ---------------------------------------------------------------------------


class TestGetSessionFactory:
    def test_returns_none_when_not_set(self):
        from general_ludd.routers.self_improve import _get_session_factory

        app = FastAPI()
        assert _get_session_factory(app) is None

    def test_returns_factory_when_set(self):
        from general_ludd.routers.self_improve import _get_session_factory

        app = FastAPI()
        fake = object()
        app.state._session_factory = fake
        assert _get_session_factory(app) is fake


# ---------------------------------------------------------------------------
# _ConfigTierCapabilityChecker
# ---------------------------------------------------------------------------


class TestConfigTierCapabilityChecker:
    def test_allows_config_write(self):
        from general_ludd.routers.self_improve import _ConfigTierCapabilityChecker

        checker = _ConfigTierCapabilityChecker()
        assert checker.allows("config_write") is True

    def test_denies_code_write(self):
        from general_ludd.routers.self_improve import _ConfigTierCapabilityChecker

        checker = _ConfigTierCapabilityChecker()
        assert checker.allows("code_write") is False

    def test_denies_arbitrary_capability(self):
        from general_ludd.routers.self_improve import _ConfigTierCapabilityChecker

        checker = _ConfigTierCapabilityChecker()
        assert checker.allows("root") is False

    def test_denies_empty_string(self):
        from general_ludd.routers.self_improve import _ConfigTierCapabilityChecker

        checker = _ConfigTierCapabilityChecker()
        assert checker.allows("") is False


# ---------------------------------------------------------------------------
# _coerce_priority — deep edge cases NOT in test_priority_validation.py
# ---------------------------------------------------------------------------


class TestCoercePriorityDeep:
    def test_none_returns_medium(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority(None) == 5

    def test_float_returns_medium(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority(3.14) == 5

    def test_list_returns_medium(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority([]) == 5

    def test_false_bool_treated_as_unset(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority(False) == 5

    def test_empty_string_returns_medium(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority("") == 5

    def test_case_insensitive_string(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority("HIGH") == 10
        assert _coerce_priority("LOW") == 0
        assert _coerce_priority("Critical") == 20

    def test_unknown_string_returns_medium(self):
        from general_ludd.routers.self_improve import _coerce_priority

        assert _coerce_priority("urgent") == 5

    def test_clamps_above_max(self):
        from general_ludd.routers.self_improve import _MAX_PRIORITY, _coerce_priority

        assert _coerce_priority(_MAX_PRIORITY + 1) == _MAX_PRIORITY
        assert _coerce_priority(99999) == _MAX_PRIORITY


# ---------------------------------------------------------------------------
# _persist_gated_self_improve_todos
# ---------------------------------------------------------------------------


class TestPersistGatedSelfImproveTodos:
    @pytest.mark.asyncio
    async def test_admits_todos_within_cap(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _persist_gated_self_improve_todos,
        )

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            todos = [
                {"title": "Fix X", "description": "desc", "priority": "high"},
                {"title": "Add Y", "description": "", "priority": "low"},
            ]
            ids = await _persist_gated_self_improve_todos(repo, todos)
            assert len(ids) == 2

            for tid in ids:
                row = await repo.get_by_id(tid)
                assert row is not None
                assert row.work_type == SELF_IMPROVE_WORK_TYPE
                assert row.status == TodoStatus.APPROVAL_REQUIRED.value

    @pytest.mark.asyncio
    async def test_rejects_todos_beyond_max_open(self, inmemory_factory):
        from general_ludd.routers.self_improve import _persist_gated_self_improve_todos

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            todos = [{"title": f"Task {i}"} for i in range(15)]
            ids = await _persist_gated_self_improve_todos(repo, todos)
            assert len(ids) == 10

    @pytest.mark.asyncio
    async def test_counts_open_todos_for_gate(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _persist_gated_self_improve_todos,
        )

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            for i in range(5):
                await repo.create(
                    {
                        "title": f"Pre-existing {i}",
                        "status": TodoStatus.APPROVAL_REQUIRED.value,
                        "work_type": SELF_IMPROVE_WORK_TYPE,
                        "priority": 5,
                        "created_by": "test",
                    }
                )
            todos = [{"title": f"New task {i}"} for i in range(10)]
            ids = await _persist_gated_self_improve_todos(repo, todos)
            assert len(ids) == 5

    @pytest.mark.asyncio
    async def test_terminal_statuses_not_counted_as_open(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _persist_gated_self_improve_todos,
        )

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            for status in [TodoStatus.COMPLETE.value, TodoStatus.FAILED.value, TodoStatus.CANCELLED.value]:
                await repo.create(
                    {
                        "title": f"Closed {status}",
                        "status": status,
                        "work_type": SELF_IMPROVE_WORK_TYPE,
                        "priority": 5,
                        "created_by": "test",
                    }
                )
            todos = [{"title": f"New {i}"} for i in range(10)]
            ids = await _persist_gated_self_improve_todos(repo, todos)
            assert len(ids) == 10

    @pytest.mark.asyncio
    async def test_truncates_title_to_512_chars(self, inmemory_factory):
        from general_ludd.routers.self_improve import _persist_gated_self_improve_todos

        long_title = "X" * 600
        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            ids = await _persist_gated_self_improve_todos(repo, [{"title": long_title}])
            created = await repo.get_by_id(ids[0])
            assert len(created.title) == 512

    @pytest.mark.asyncio
    async def test_empty_todos_list_returns_empty(self, inmemory_factory):
        from general_ludd.routers.self_improve import _persist_gated_self_improve_todos

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            ids = await _persist_gated_self_improve_todos(repo, [])
            assert ids == []

    @pytest.mark.asyncio
    async def test_defaults_missing_fields(self, inmemory_factory):
        from general_ludd.routers.self_improve import _persist_gated_self_improve_todos

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            ids = await _persist_gated_self_improve_todos(repo, [{}])
            created = await repo.get_by_id(ids[0])
            assert created.title == "Self-improvement task"
            assert created.priority == 10
            assert created.created_by == "self_improve_harness"


# ---------------------------------------------------------------------------
# _enqueue_config_change
# ---------------------------------------------------------------------------


class TestEnqueueConfigChange:
    @pytest.mark.asyncio
    async def test_creates_approval_required_record(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _enqueue_config_change,
        )

        payload = {
            "title": "Update config",
            "target_paths": ["config/app.yml"],
            "change_content": "key: value\n",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)
        assert result["status"] == "approval_required"
        assert result["tier"] == "config"
        approval_id = result["approval_id"]
        assert approval_id

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(approval_id)
            assert todo.status == TodoStatus.APPROVAL_REQUIRED.value
            assert todo.work_type == SELF_IMPROVE_WORK_TYPE
            assert todo.priority == 10
            assert todo.created_by == "self_improve_apply"

    @pytest.mark.asyncio
    async def test_serializes_full_spec_into_plan_artifact(self, inmemory_factory):
        from general_ludd.routers.self_improve import _enqueue_config_change

        payload = {
            "title": "Tune settings",
            "description": "Adjust the widget threshold",
            "target_paths": ["config/settings.yml", "config/overrides.yml"],
            "change_content": "widget: 42\n",
            "capability_required": "config_write",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["kind"] == "config"
            assert spec["capability_required"] == "config_write"
            assert spec["target_paths"] == ["config/settings.yml", "config/overrides.yml"]
            assert spec["change_content"] == "widget: 42\n"
            assert spec["reason"] == "Tune settings"

    @pytest.mark.asyncio
    async def test_reason_falls_back_to_description(self, inmemory_factory):
        from general_ludd.routers.self_improve import _enqueue_config_change

        payload = {
            "description": "Adjust the widget threshold",
            "target_paths": ["config/settings.yml"],
            "change_content": "widget: 42\n",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["reason"] == "Adjust the widget threshold"

    @pytest.mark.asyncio
    async def test_reason_falls_back_to_generic_label(self, inmemory_factory):
        from general_ludd.routers.self_improve import _enqueue_config_change

        payload: dict = {
            "target_paths": ["config/settings.yml"],
            "change_content": "key: v\n",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["reason"] == "self-improve config write (config)"

    @pytest.mark.asyncio
    async def test_title_truncated_in_created_todo(self, inmemory_factory):
        from general_ludd.routers.self_improve import _enqueue_config_change

        long_path = "config/" + ("x" * 500) + ".yml"
        payload = {
            "target_paths": [long_path],
            "change_content": "k: v\n",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            assert len(todo.title) <= 512

    @pytest.mark.asyncio
    async def test_empty_target_paths_uses_kind_as_targets(self, inmemory_factory):
        from general_ludd.routers.self_improve import _enqueue_config_change

        payload: dict = {
            "change_content": "key: value\n",
        }
        result = await _enqueue_config_change(inmemory_factory, "config", payload)

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            assert todo.title == "Self-improve config write: config"
            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["target_paths"] == []


# ---------------------------------------------------------------------------
# _apply_approved_config_change
# ---------------------------------------------------------------------------


class TestApplyApprovedConfigChange:
    @pytest.mark.asyncio
    async def test_404_when_approval_not_found(self, inmemory_factory):
        from general_ludd.routers.self_improve import _apply_approved_config_change

        with pytest.raises(HTTPException) as exc_info:
            await _apply_approved_config_change(inmemory_factory, "nonexistent")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_409_when_not_self_improve_work_type(self, inmemory_factory):
        from general_ludd.routers.self_improve import _apply_approved_config_change

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            created = await repo.create(
                {
                    "title": "Not self-improve",
                    "status": TodoStatus.QUEUED.value,
                    "work_type": "code",
                    "priority": 5,
                    "created_by": "test",
                    "plan_artifact": json.dumps(
                        {"kind": "config", "target_paths": ["x.yml"], "change_content": "k: v\n"}
                    ),
                }
            )
            await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await _apply_approved_config_change(inmemory_factory, created.todo_id)
        assert exc_info.value.status_code == 409
        assert "not a self-improve record" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_409_when_not_released_yet(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _apply_approved_config_change,
        )

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            created = await repo.create(
                {
                    "title": "Held config",
                    "status": TodoStatus.APPROVAL_REQUIRED.value,
                    "work_type": SELF_IMPROVE_WORK_TYPE,
                    "priority": 10,
                    "created_by": "test",
                    "plan_artifact": json.dumps(
                        {"kind": "config", "target_paths": ["x.yml"], "change_content": "k: v\n"}
                    ),
                }
            )
            await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await _apply_approved_config_change(inmemory_factory, created.todo_id)
        assert exc_info.value.status_code == 409
        assert "not released" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_422_when_malformed_spec(self, inmemory_factory):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _apply_approved_config_change,
        )

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            created = await repo.create(
                {
                    "title": "Broken spec",
                    "status": TodoStatus.QUEUED.value,
                    "work_type": SELF_IMPROVE_WORK_TYPE,
                    "priority": 10,
                    "created_by": "test",
                    "plan_artifact": "not valid json {{{",
                }
            )
            await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await _apply_approved_config_change(inmemory_factory, created.todo_id)
        assert exc_info.value.status_code == 422
        assert "malformed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_consumes_record_on_successful_apply(self, inmemory_factory, monkeypatch):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _apply_approved_config_change,
        )

        monkeypatch.setattr("general_ludd.routers.self_improve.Path.cwd", lambda: Path("/tmp/gludd-test-cwd"))

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            created = await repo.create(
                {
                    "title": "Config write",
                    "status": TodoStatus.QUEUED.value,
                    "work_type": SELF_IMPROVE_WORK_TYPE,
                    "priority": 10,
                    "created_by": "test",
                    "plan_artifact": json.dumps(
                        {
                            "kind": "config",
                            "target_paths": ["/tmp/gludd-test-cwd/cfg.yml"],
                            "change_content": "key: value\n",
                        }
                    ),
                }
            )
            await session.commit()

        result = await _apply_approved_config_change(inmemory_factory, created.todo_id)
        assert result["status"] == "applied"
        assert result["approval_id"] == created.todo_id

        async with inmemory_factory() as session:
            consumed = await TodoRepository(session).get_by_id(created.todo_id)
            assert consumed.status == TodoStatus.COMPLETE.value


# ---------------------------------------------------------------------------
# _enqueue_non_config_change
# ---------------------------------------------------------------------------


class TestEnqueueNonConfigChange:
    @pytest.mark.asyncio
    async def test_enqueues_approval_required_record(
        self,
        inmemory_factory,
        tmp_path: Path,
    ):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _enqueue_non_config_change,
        )

        payload = {
            "kind": "code",
            "title": "Refactor loop",
            "description": "Simplify the event loop tick method",
        }
        repo_root, worktree = await _non_config_workspace(inmemory_factory, tmp_path)
        payload["worktree_path"] = str(worktree)
        result = await _enqueue_non_config_change(
            inmemory_factory,
            "code",
            payload,
            project_id="project-1",
            repo_root=repo_root,
        )
        assert result["tier"] == "code"
        assert result["status"] == "approval_required"
        approval_id = result["approval_id"]

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(approval_id)
            assert todo.status == TodoStatus.APPROVAL_REQUIRED.value
            assert todo.work_type == SELF_IMPROVE_WORK_TYPE
            assert todo.priority == 10
            assert todo.created_by == "self_improve_apply"
            assert todo.project_id == "project-1"

            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["kind"] == "code"
            assert spec["title"] == "Refactor loop"
            assert spec["project_id"] == "project-1"
            assert spec["schema_version"] == 1
            assert spec["worktree_path"] == str(worktree.resolve())

    @pytest.mark.asyncio
    async def test_falls_back_to_generic_title_when_empty(
        self,
        inmemory_factory,
        tmp_path: Path,
    ):
        from general_ludd.routers.self_improve import _enqueue_non_config_change

        repo_root, worktree = await _non_config_workspace(inmemory_factory, tmp_path)
        payload: dict = {"kind": "role", "worktree_path": str(worktree)}
        result = await _enqueue_non_config_change(
            inmemory_factory,
            "role",
            payload,
            project_id="project-1",
            repo_root=repo_root,
        )

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            assert todo.title == "self-improve role change"

    @pytest.mark.asyncio
    async def test_truncates_title_to_512(
        self,
        inmemory_factory,
        tmp_path: Path,
    ):
        from general_ludd.routers.self_improve import _enqueue_non_config_change

        repo_root, worktree = await _non_config_workspace(inmemory_factory, tmp_path)
        long_title = "A" * 600
        payload = {
            "kind": "code",
            "title": long_title,
            "worktree_path": str(worktree),
        }
        result = await _enqueue_non_config_change(
            inmemory_factory,
            "code",
            payload,
            project_id="project-1",
            repo_root=repo_root,
        )

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            assert len(todo.title) == 512

    @pytest.mark.asyncio
    async def test_uses_description_for_plan_spec(
        self,
        inmemory_factory,
        tmp_path: Path,
    ):
        from general_ludd.routers.self_improve import _enqueue_non_config_change

        repo_root, worktree = await _non_config_workspace(inmemory_factory, tmp_path)
        payload = {
            "kind": "code",
            "title": "Fix N+1",
            "description": "Replace N+1 query pattern with joinedload",
            "worktree_path": str(worktree),
        }
        result = await _enqueue_non_config_change(
            inmemory_factory,
            "code",
            payload,
            project_id="project-1",
            repo_root=repo_root,
        )

        async with inmemory_factory() as session:
            todo = await TodoRepository(session).get_by_id(result["approval_id"])
            spec = json.loads(todo.plan_artifact or "{}")
            assert spec["description"] == "Replace N+1 query pattern with joinedload"


# ---------------------------------------------------------------------------
# _config_tier_apply — routing logic
# ---------------------------------------------------------------------------


class TestConfigTierApplyRouting:
    @pytest.mark.asyncio
    async def test_without_approval_id_delegates_to_enqueue(self, inmemory_factory):
        from general_ludd.routers.self_improve import _config_tier_apply

        app = FastAPI()
        app.state._session_factory = inmemory_factory

        result = await _config_tier_apply(
            app,
            "config",
            {"title": "Update", "target_paths": ["config/a.yml"], "change_content": "k: v\n"},
        )
        assert result["status"] == "approval_required"
        assert result["tier"] == "config"

    @pytest.mark.asyncio
    async def test_with_approval_id_delegates_to_apply(self, inmemory_factory, monkeypatch):
        from general_ludd.routers.self_improve import (
            SELF_IMPROVE_WORK_TYPE,
            _config_tier_apply,
        )

        monkeypatch.setattr("general_ludd.routers.self_improve.Path.cwd", lambda: Path("/tmp/gludd-test-cwd"))

        app = FastAPI()
        app.state._session_factory = inmemory_factory

        async with inmemory_factory() as session:
            repo = TodoRepository(session)
            created = await repo.create(
                {
                    "title": "Config write",
                    "status": TodoStatus.QUEUED.value,
                    "work_type": SELF_IMPROVE_WORK_TYPE,
                    "priority": 10,
                    "created_by": "test",
                    "plan_artifact": json.dumps(
                        {
                            "kind": "config",
                            "target_paths": ["/tmp/gludd-test-cwd/cfg.yml"],
                            "change_content": "key: value\n",
                        }
                    ),
                }
            )
            await session.commit()

        result = await _config_tier_apply(
            app,
            "config",
            {"approval_id": created.todo_id},
        )
        assert result["status"] == "applied"

    @pytest.mark.asyncio
    async def test_no_factory_raises_503(self):
        from general_ludd.routers.self_improve import _config_tier_apply

        app = FastAPI()
        with pytest.raises(HTTPException) as exc_info:
            await _config_tier_apply(app, "config", {})
        assert exc_info.value.status_code == 503
        assert "approval database" in exc_info.value.detail
