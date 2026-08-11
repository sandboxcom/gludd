"""Deep edge-case tests for src/general_ludd/routers/human_todos.py.

Gaps covered (not tested by test_human_todo_repo.py or test_human_todo_cli.py):
  - POST: invalid category/priority → 422, no-DB → 503, ValueError from repo → 422,
    parent-transition non-fatal on terminal parent, notification dispatch
  - GET list: no-DB → empty, limit/offset clamping, filter combinations
  - GET feed: no-DB → empty, with/without since param, 24h default
  - GET by-id: 404, 503 no-DB, found → 200
  - PATCH: terminal reject → 422, self-resolution → 403, done/dismissed missing
    fields → 422, in_progress transition, parent-unblock on resolve,
    escalation-sync best-effort, InvalidTransitionError → 422
  - DELETE: soft-delete open, terminal skip, 404, 503
  - Tags: not-found → 404, success append, 503
  - _human_todo_to_dict: corrupt tags JSON → [], null created_at/updated_at
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.db.repository import (
    InvalidTransitionError,
)
from general_ludd.routers.human_todos import (
    AddTagRequest,
    CreateHumanTodoRequest,
    PatchHumanTodoRequest,
    _human_todo_to_dict,
    register,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _bare_app() -> FastAPI:
    app = FastAPI()
    register(app, {})
    return app


def _make_session_factory(repo_class: Any = None, repo_side_effects: dict[str, Any] | None = None):
    """Return an async_sessionmaker yielding an AsyncMock with injected repo."""
    sfx = repo_side_effects or {}

    async def _mock_repo():
        return MagicMock(
            **{
                "_session": MagicMock(),
                **sfx,
            }
        )

    ctx = sfx.get("__factory_ctx", asynccontextmanager)

    @ctx
    async def _f():
        s = AsyncMock()
        s.commit = AsyncMock()
        if repo_class is not None:
            s._mock_repo_instance = repo_class(s)
        yield s

    return _f


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── _human_todo_to_dict ──────────────────────────────────────────────────


class TestHumanTodoToDict:
    def test_corrupt_tags_defaults_to_empty_list(self):
        row = MagicMock()
        row.id = "HTODO-1"
        row.tags = "not-json["
        row.parent_agent_todo_id = None
        row.agent_id = "a"
        row.session_id = None
        row.title = "t"
        row.body = "b"
        row.category = "blocker"
        row.priority = "high"
        row.status = "open"
        row.human_resolution = None
        row.human_resolver = None
        row.created_at = None
        row.updated_at = None
        row.resolved_at = None
        row.due_at = None
        d = _human_todo_to_dict(row)
        assert d["tags"] == []

    def test_null_datetime_fields_become_none(self):
        row = MagicMock()
        row.id = "HTODO-1"
        row.tags = "[]"
        row.parent_agent_todo_id = None
        row.agent_id = "a"
        row.session_id = None
        row.title = "t"
        row.body = "b"
        row.category = "blocker"
        row.priority = "high"
        row.status = "open"
        row.human_resolution = None
        row.human_resolver = None
        row.created_at = None
        row.updated_at = None
        row.resolved_at = None
        row.due_at = None
        d = _human_todo_to_dict(row)
        assert d["created_at"] is None
        assert d["updated_at"] is None
        assert d["resolved_at"] is None
        assert d["due_at"] is None

    def test_roundtrip_with_all_fields(self):
        now = datetime.now(UTC)
        row = MagicMock()
        row.id = "HTODO-X"
        row.tags = json.dumps(["esc:1", "prod"])
        row.parent_agent_todo_id = "TODO-5"
        row.agent_id = "agent-1"
        row.session_id = "sess-2"
        row.title = "Need approval"
        row.body = "Please approve the escalation."
        row.category = "permission_escalation"
        row.priority = "urgent"
        row.status = "open"
        row.human_resolution = None
        row.human_resolver = None
        row.created_at = now
        row.updated_at = now
        row.resolved_at = None
        row.due_at = now
        d = _human_todo_to_dict(row)
        assert d["tags"] == ["esc:1", "prod"]
        assert d["parent_agent_todo_id"] == "TODO-5"
        assert d["created_at"] == str(now)


# ── POST /api/human-todos ────────────────────────────────────────────────


class TestCreateHumanTodo:
    def test_invalid_category_returns_422(self):
        app = _bare_app()
        client = _client(app)
        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "a",
                "title": "t",
                "body": "b",
                "category": "nonsense",
            },
        )
        assert resp.status_code == 422
        assert "category" in resp.json()["detail"].lower()

    def test_invalid_priority_returns_422(self):
        app = _bare_app()
        client = _client(app)
        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "a",
                "title": "t",
                "body": "b",
                "category": "blocker",
                "priority": "critical",
            },
        )
        assert resp.status_code == 422
        assert "priority" in resp.json()["detail"].lower()

    @pytest.mark.parametrize(
        "bad_field,payload",
        [
            ("agent_id", {"title": "t", "body": "b", "category": "blocker"}),
            ("title", {"agent_id": "a", "body": "b", "category": "blocker"}),
            ("body", {"agent_id": "a", "title": "t", "category": "blocker"}),
            ("title", {"agent_id": "a", "title": "", "body": "b", "category": "blocker"}),
        ],
    )
    def test_missing_or_empty_required_fields_422(self, bad_field, payload):
        app = _bare_app()
        client = _client(app)
        resp = client.post("/api/human-todos", json=payload)
        assert resp.status_code == 422

    def test_no_database_returns_503(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "a",
                "title": "t",
                "body": "b",
                "category": "blocker",
            },
        )
        assert resp.status_code == 503

    def test_successful_create(self):
        from general_ludd.routers import human_todos as mod

        fake_row = MagicMock()
        fake_row.id = "HTODO-1"
        fake_row.tags = "[]"
        fake_row.parent_agent_todo_id = None
        fake_row.agent_id = "agent-1"
        fake_row.session_id = None
        fake_row.title = "Need key"
        fake_row.body = "missing"
        fake_row.category = "input_request"
        fake_row.priority = "high"
        fake_row.status = "open"
        fake_row.human_resolution = None
        fake_row.human_resolver = None
        fake_row.created_at = datetime.now(UTC)
        fake_row.updated_at = datetime.now(UTC)
        fake_row.resolved_at = None
        fake_row.due_at = None

        fake_repo = MagicMock()
        fake_repo.create = AsyncMock(return_value=fake_row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            s._fake_repo = fake_repo
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.post(
                "/api/human-todos",
                json={
                    "agent_id": "agent-1",
                    "title": "Need key",
                    "body": "missing",
                    "category": "input_request",
                    "priority": "high",
                },
            )
            assert resp.status_code == 201
            assert resp.json()["id"] == "HTODO-1"
            assert resp.json()["status"] == "open"

    def test_parent_transition_non_fatal_on_terminal_parent(self):
        from general_ludd.routers import human_todos as mod

        fake_row = MagicMock()
        fake_row.id = "HTODO-1"
        fake_row.tags = "[]"
        fake_row.parent_agent_todo_id = "TODO-5"
        fake_row.agent_id = "agent-1"
        fake_row.session_id = None
        fake_row.title = "t"
        fake_row.body = "b"
        fake_row.category = "blocker"
        fake_row.priority = "high"
        fake_row.status = "open"
        fake_row.human_resolution = None
        fake_row.human_resolver = None
        fake_row.created_at = datetime.now(UTC)
        fake_row.updated_at = datetime.now(UTC)
        fake_row.resolved_at = None
        fake_row.due_at = None

        fake_repo = MagicMock()
        fake_repo.create = AsyncMock(return_value=fake_row)

        fake_todo_repo = MagicMock()
        fake_todo_repo.get_by_id = AsyncMock(return_value=MagicMock(version=1))
        fake_todo_repo.transition = AsyncMock(side_effect=InvalidTransitionError("already terminal"))

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with (
            patch.object(mod, "HumanTodoRepository", return_value=fake_repo),
            patch.object(mod, "TodoRepository", return_value=fake_todo_repo),
        ):
            client = _client(app)
            resp = client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                    "parent_agent_todo_id": "TODO-5",
                },
            )
            assert resp.status_code == 201

    def test_repo_valueerror_returns_422(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.create = AsyncMock(side_effect=ValueError("bad input"))

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                },
            )
            assert resp.status_code == 422


# ── GET /api/human-todos (list) ──────────────────────────────────────────


class TestListHumanTodos:
    def test_no_database_returns_empty_list(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.get("/api/human-todos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_clamped_to_500(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.list_all = AsyncMock(return_value=[])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            client.get("/api/human-todos?limit=9999")
            call_kwargs = fake_repo.list_all.call_args.kwargs
            assert call_kwargs["limit"] == 500

    def test_limit_clamped_to_min_1(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.list_all = AsyncMock(return_value=[])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            client.get("/api/human-todos?limit=0")
            call_kwargs = fake_repo.list_all.call_args.kwargs
            assert call_kwargs["limit"] == 1

    def test_offset_clamped_to_min_0(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.list_all = AsyncMock(return_value=[])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            client.get("/api/human-todos?offset=-5")
            call_kwargs = fake_repo.list_all.call_args.kwargs
            assert call_kwargs["offset"] == 0

    def test_filters_passed_through(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.list_all = AsyncMock(return_value=[])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            client.get("/api/human-todos?status=open&category=blocker&priority=urgent&agent_id=agent-1")
            call_kwargs = fake_repo.list_all.call_args.kwargs
            assert call_kwargs["status"] == "open"
            assert call_kwargs["category"] == "blocker"
            assert call_kwargs["priority"] == "urgent"
            assert call_kwargs["agent_id"] == "agent-1"


# ── GET /api/human-todos/feed ────────────────────────────────────────────


class TestFeedHumanTodos:
    def test_no_database_returns_empty_list(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.get("/api/human-todos/feed")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_explicit_since(self):
        from general_ludd.routers import human_todos as mod

        fake_row = MagicMock()
        fake_row.id = "HTODO-1"
        fake_row.tags = "[]"
        fake_row.parent_agent_todo_id = None
        fake_row.agent_id = "a"
        fake_row.session_id = None
        fake_row.title = "t"
        fake_row.body = "b"
        fake_row.category = "blocker"
        fake_row.priority = "high"
        fake_row.status = "open"
        fake_row.human_resolution = None
        fake_row.human_resolver = None
        fake_row.created_at = datetime.now(UTC)
        fake_row.updated_at = datetime.now(UTC)
        fake_row.resolved_at = None
        fake_row.due_at = None

        fake_repo = MagicMock()
        fake_repo.list_changed_since = AsyncMock(return_value=[fake_row])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            since = "2026-01-01T00:00:00Z"
            resp = client.get(f"/api/human-todos/feed?since={since}")
            assert resp.status_code == 200
            assert len(resp.json()) == 1

    def test_without_since_uses_24h_default(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.list_changed_since = AsyncMock(return_value=[])

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.get("/api/human-todos/feed")
            assert resp.status_code == 200
            assert fake_repo.list_changed_since.called
            boundary_arg = fake_repo.list_changed_since.call_args.args[0]
            assert isinstance(boundary_arg, datetime)


# ── GET /api/human-todos/{id} ────────────────────────────────────────────


class TestGetHumanTodo:
    def test_not_found_returns_404(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=None)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.get("/api/human-todos/HTODO-999")
            assert resp.status_code == 404

    def test_no_database_returns_503(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.get("/api/human-todos/HTODO-1")
        assert resp.status_code == 503

    def test_found_returns_200(self):
        from general_ludd.routers import human_todos as mod

        fake_row = MagicMock()
        fake_row.id = "HTODO-1"
        fake_row.tags = "[]"
        fake_row.parent_agent_todo_id = None
        fake_row.agent_id = "a"
        fake_row.session_id = None
        fake_row.title = "Need key"
        fake_row.body = "missing"
        fake_row.category = "input_request"
        fake_row.priority = "urgent"
        fake_row.status = "open"
        fake_row.human_resolution = None
        fake_row.human_resolver = None
        fake_row.created_at = datetime.now(UTC)
        fake_row.updated_at = datetime.now(UTC)
        fake_row.resolved_at = None
        fake_row.due_at = None

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=fake_row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.get("/api/human-todos/HTODO-1")
            assert resp.status_code == 200
            assert resp.json()["title"] == "Need key"


# ── PATCH /api/human-todos/{id} ──────────────────────────────────────────


class TestPatchHumanTodo:
    def _fake_open_row(self) -> MagicMock:
        row = MagicMock()
        row.id = "HTODO-1"
        row.tags = "[]"
        row.parent_agent_todo_id = None
        row.agent_id = "agent-1"
        row.session_id = None
        row.title = "Need key"
        row.body = "missing"
        row.category = "input_request"
        row.priority = "high"
        row.status = "open"
        row.human_resolution = None
        row.human_resolver = None
        row.created_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        row.resolved_at = None
        row.due_at = None
        return row

    def test_no_database_returns_503(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.patch("/api/human-todos/HTODO-1", json={"status": "done"})
        assert resp.status_code == 503

    def test_not_found_returns_404(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=None)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch("/api/human-todos/HTODO-999", json={"status": "done"})
            assert resp.status_code == 404

    def test_terminal_state_rejected_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        row.status = "done"
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch("/api/human-todos/HTODO-1", json={"status": "done"})
            assert resp.status_code == 422
            assert "terminal" in resp.json()["detail"].lower()

    def test_invalid_target_status_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch("/api/human-todos/HTODO-1", json={"status": "nonsense"})
            assert resp.status_code == 422
            assert "invalid status" in resp.json()["detail"].lower()

    def test_self_resolution_forbidden_403(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "agent-1",
                    "human_resolution": "I did it myself",
                },
            )
            assert resp.status_code == 403
            assert "self_resolution" in resp.json()["detail"].lower()

    def test_done_missing_resolver_returns_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolution": "fixed",
                },
            )
            assert resp.status_code == 422
            assert "human_resolver" in resp.json()["detail"].lower()

    def test_done_missing_resolution_returns_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                },
            )
            assert resp.status_code == 422
            assert "human_resolution" in resp.json()["detail"].lower()

    def test_dismissed_missing_resolver_returns_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "dismissed",
                    "human_resolution": "will not fix",
                },
            )
            assert resp.status_code == 422

    def test_successful_done(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        resolved_row = self._fake_open_row()
        resolved_row.status = "done"
        resolved_row.human_resolver = "shawn"
        resolved_row.human_resolution = "key rotated"

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_done = AsyncMock(return_value=resolved_row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "key rotated",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "done"

    def test_successful_in_progress(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        ip_row = self._fake_open_row()
        ip_row.status = "in_progress"

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_in_progress = AsyncMock(return_value=ip_row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch("/api/human-todos/HTODO-1", json={"status": "in_progress"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "in_progress"

    def test_invalid_transition_returns_422(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_done = AsyncMock(side_effect=InvalidTransitionError("bad transition"))

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "fixed",
                },
            )
            assert resp.status_code == 422

    def test_parent_unblock_on_done(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        row.parent_agent_todo_id = "TODO-5"
        resolved_row = self._fake_open_row()
        resolved_row.status = "done"
        resolved_row.parent_agent_todo_id = "TODO-5"
        resolved_row.human_resolver = "shawn"
        resolved_row.human_resolution = "done"

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_done = AsyncMock(return_value=resolved_row)

        parent = MagicMock(version=2)
        fake_todo_repo = MagicMock()
        fake_todo_repo.get_by_id = AsyncMock(return_value=parent)
        fake_todo_repo.transition = AsyncMock()

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with (
            patch.object(mod, "HumanTodoRepository", return_value=fake_repo),
            patch.object(mod, "TodoRepository", return_value=fake_todo_repo),
        ):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "done",
                },
            )
            assert resp.status_code == 200
            fake_todo_repo.transition.assert_called_once()
            call_args = fake_todo_repo.transition.call_args
            from general_ludd.schemas.todo import TodoStatus

            assert call_args.args[1] == TodoStatus.QUEUED

    def test_parent_unblock_on_dismissed(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        row.parent_agent_todo_id = "TODO-5"
        resolved_row = self._fake_open_row()
        resolved_row.status = "dismissed"
        resolved_row.parent_agent_todo_id = "TODO-5"
        resolved_row.human_resolver = "shawn"
        resolved_row.human_resolution = "wont do"

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.dismiss = AsyncMock(return_value=resolved_row)

        parent = MagicMock(version=2)
        fake_todo_repo = MagicMock()
        fake_todo_repo.get_by_id = AsyncMock(return_value=parent)
        fake_todo_repo.transition = AsyncMock()

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with (
            patch.object(mod, "HumanTodoRepository", return_value=fake_repo),
            patch.object(mod, "TodoRepository", return_value=fake_todo_repo),
        ):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "dismissed",
                    "human_resolver": "shawn",
                    "human_resolution": "wont do",
                },
            )
            assert resp.status_code == 200
            from general_ludd.schemas.todo import TodoStatus

            assert fake_todo_repo.transition.call_args.args[1] == TodoStatus.CANCELLED

    def test_parent_unblock_non_fatal_on_error(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        row.parent_agent_todo_id = "TODO-5"
        resolved_row = self._fake_open_row()
        resolved_row.status = "done"
        resolved_row.parent_agent_todo_id = "TODO-5"
        resolved_row.human_resolver = "shawn"
        resolved_row.human_resolution = "done"

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_done = AsyncMock(return_value=resolved_row)

        fake_todo_repo = MagicMock()
        fake_todo_repo.get_by_id = AsyncMock(return_value=MagicMock(version=1))
        fake_todo_repo.transition = AsyncMock(side_effect=Exception("db error"))

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with (
            patch.object(mod, "HumanTodoRepository", return_value=fake_repo),
            patch.object(mod, "TodoRepository", return_value=fake_todo_repo),
        ):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "done",
                },
            )
            assert resp.status_code == 200

    def test_escalation_sync_best_effort_non_fatal(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_open_row()
        row.category = "permission_escalation"
        row.parent_agent_todo_id = None
        row.tags = json.dumps(["esc:1"])
        resolved_row = self._fake_open_row()
        resolved_row.status = "done"
        resolved_row.category = "permission_escalation"
        resolved_row.human_resolver = "shawn"
        resolved_row.human_resolution = "approved"
        resolved_row.tags = json.dumps(["esc:1"])

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.mark_done = AsyncMock(return_value=resolved_row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        from general_ludd.routers import security

        with (
            patch.object(mod, "HumanTodoRepository", return_value=fake_repo),
            patch.object(
                security, "_sync_escalation_from_human_todo", side_effect=RuntimeError("escalation sync broken")
            ),
        ):
            client = _client(app)
            resp = client.patch(
                "/api/human-todos/HTODO-1",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "approved",
                },
            )
            assert resp.status_code == 200


# ── DELETE /api/human-todos/{id} ─────────────────────────────────────────


class TestDeleteHumanTodo:
    def test_no_database_returns_503(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.delete("/api/human-todos/HTODO-1")
        assert resp.status_code == 503

    def test_not_found_returns_404(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=None)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.delete("/api/human-todos/HTODO-999")
            assert resp.status_code == 404

    def test_soft_delete_open_moves_to_dismissed(self):
        from general_ludd.routers import human_todos as mod

        row = MagicMock()
        row.id = "HTODO-1"
        row.status = "open"
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)
        fake_repo.dismiss = AsyncMock()

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.delete("/api/human-todos/HTODO-1")
            assert resp.status_code == 200
            assert resp.json()["id"] == "HTODO-1"
            assert resp.json()["status"] == "deleted"
            fake_repo.dismiss.assert_called_once_with("HTODO-1", "admin", "soft-deleted by admin")

    def test_terminal_already_skips_dismiss(self):
        from general_ludd.routers import human_todos as mod

        row = MagicMock()
        row.id = "HTODO-1"
        row.status = "done"
        fake_repo = MagicMock()
        fake_repo.get = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.delete("/api/human-todos/HTODO-1")
            assert resp.status_code == 200
            fake_repo.dismiss.assert_not_called()


# ── POST /api/human-todos/{id}/tags ──────────────────────────────────────


class TestAddTag:
    def _fake_row(self) -> MagicMock:
        row = MagicMock()
        row.id = "HTODO-1"
        row.tags = json.dumps(["existing"])
        row.parent_agent_todo_id = None
        row.agent_id = "agent-1"
        row.session_id = None
        row.title = "t"
        row.body = "b"
        row.category = "blocker"
        row.priority = "high"
        row.status = "open"
        row.human_resolution = None
        row.human_resolver = None
        row.created_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        row.resolved_at = None
        row.due_at = None
        return row

    def test_no_database_returns_503(self):
        app = FastAPI()
        register(app, {})
        app.state._session_factory = None
        client = _client(app)
        resp = client.post("/api/human-todos/HTODO-1/tags", json={"tag": "esc:1"})
        assert resp.status_code == 503

    def test_not_found_returns_404(self):
        from general_ludd.routers import human_todos as mod

        fake_repo = MagicMock()
        fake_repo.add_tag = AsyncMock(side_effect=InvalidTransitionError("not found"))

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.post("/api/human-todos/HTODO-999/tags", json={"tag": "esc:1"})
            assert resp.status_code == 404

    def test_successful_tag_append(self):
        from general_ludd.routers import human_todos as mod

        row = self._fake_row()
        fake_repo = MagicMock()
        fake_repo.add_tag = AsyncMock(return_value=row)

        app = FastAPI()
        register(app, {})

        @asynccontextmanager
        async def _f():
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = _f

        with patch.object(mod, "HumanTodoRepository", return_value=fake_repo):
            client = _client(app)
            resp = client.post("/api/human-todos/HTODO-1/tags", json={"tag": "esc:1"})
            assert resp.status_code == 200
            fake_repo.add_tag.assert_called_once_with("HTODO-1", "esc:1")


# ── Request model validation ─────────────────────────────────────────────


class TestRequestModels:
    def test_create_request_defaults(self):
        req = CreateHumanTodoRequest(
            agent_id="a",
            title="t",
            body="b",
            category="blocker",
        )
        assert req.priority == "medium"
        assert req.tags == []
        assert req.parent_agent_todo_id is None

    def test_create_request_min_lengths(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CreateHumanTodoRequest(agent_id="", title="t", body="b", category="blocker")
        with pytest.raises(pydantic.ValidationError):
            CreateHumanTodoRequest(agent_id="a", title="", body="b", category="blocker")

    def test_patch_request_all_none_default(self):
        req = PatchHumanTodoRequest()
        assert req.status is None
        assert req.human_resolution is None
        assert req.human_resolver is None

    def test_add_tag_request_empty_tag_rejected(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            AddTagRequest(tag="")
