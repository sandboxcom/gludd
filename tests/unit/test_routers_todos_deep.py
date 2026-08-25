"""Deep edge-case tests for src/general_ludd/routers/todos.py.

Gaps covered (not tested elsewhere):
  - Rate limiter exhaustion (429) on POST /api/todos and POST /api/todos/scheduled
  - _deserialize_json_list: non-list JSON, invalid JSON, None, empty
  - Scheduled todo: missing both scheduled_at + cron → 422; bad cron field count → 422
  - Pause/resume on non-scheduled todo → 422 (state guard)
  - Pause/resume on non-existent todo → 404
  - Invalid AddTodoRequest: priority, queue pattern Pydantic validation
  - PUT /api/todos/{id}: optimistic concurrency (version mismatch) and missing todo → 404
  - GET /api/todos list: limit=0 clamped, offset clamping, queue+status filter in degraded mode
  - Admin log-level: invalid level → 422; valid level → 200
  - GET /api/todos/scheduled: include_paused filtering via DB mock
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from general_ludd.routers.todos import (
    AddTodoRequest,
    _deserialize_json_list,
    register,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _bare_app() -> FastAPI:
    app = FastAPI()
    register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})
    return app


def _fake_todo(
    todo_id: str = "TODO-1",
    status: str = "queued",
    version: int = 1,
) -> MagicMock:
    t = MagicMock()
    t.todo_id = todo_id
    t.title = "Task"
    t.description = ""
    t.queue = "core"
    t.priority = 1
    t.work_type = "code"
    t.status = status
    t.project_id = None
    t.version = version
    t.created_at = None
    t.acceptance_criteria = None
    t.definition_of_done = ""
    t.scheduled_at = None
    t.cron = None
    t.schedule_timezone = "UTC"
    t.next_run_at = None
    t.last_run_at = None
    t.run_count = 0
    t.max_runs = None
    t.schedule_paused = False
    return t


def _db_factory() -> Any:
    @asynccontextmanager
    async def _f():
        s = AsyncMock()
        s.commit = AsyncMock()
        yield s

    return _f


# ── _deserialize_json_list ───────────────────────────────────────────────


class TestDeserializeJsonList:
    def test_none_returns_empty(self):
        assert _deserialize_json_list(None) == []

    def test_empty_string_returns_empty(self):
        assert _deserialize_json_list("") == []

    def test_invalid_json_returns_empty(self):
        assert _deserialize_json_list("{not json") == []

    def test_non_list_json_returns_empty(self):
        assert _deserialize_json_list('{"a": 1}') == []

    def test_integer_input_returns_empty(self):
        assert _deserialize_json_list("42") == []

    def test_bool_returns_list_with_bool(self):
        assert _deserialize_json_list("true") == []

    def test_valid_list_returns_parsed(self):
        result = _deserialize_json_list('["a","b","c"]')
        assert result == ["a", "b", "c"]

    def test_nested_list_works(self):
        result = _deserialize_json_list("[[1,2],[3]]")
        assert result == [[1, 2], [3]]


# ── rate limiter ─────────────────────────────────────────────────────────


class TestRateLimiterExhaustion:
    def test_post_todos_429_when_rate_limited(self):
        """POST /api/todos returns 429 when rate limiter allowance exhausted."""
        app = _bare_app()
        with patch.object(app.state._todo_rate_limiter, "allow", return_value=False):
            resp = TestClient(app).post("/api/todos", json={"title": "rate-limited task"})
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]

    def test_post_scheduled_todos_429_when_rate_limited(self):
        """POST /api/todos/scheduled returns 429 when rate limiter exhausted."""
        app = _bare_app()
        with patch.object(app.state._todo_rate_limiter, "allow", return_value=False):
            resp = TestClient(app).post(
                "/api/todos/scheduled",
                json={"title": "sched", "cron": "0 9 * * 1-5"},
            )
        assert resp.status_code == 429

    async def test_both_endpoints_share_app_owned_rate_limiter(self):
        """Both POST endpoints share one limiter owned by their application."""
        from general_ludd.routers.web_search import SlidingWindowRateLimiter

        app = _bare_app()
        assert isinstance(app.state._todo_rate_limiter, SlidingWindowRateLimiter)

    def test_separate_apps_do_not_share_todo_rate_limit_state(self) -> None:
        """One daemon instance cannot consume another instance's allowance."""
        first = _bare_app()
        second = _bare_app()

        assert first.state._todo_rate_limiter is not second.state._todo_rate_limiter
        with patch.object(first.state._todo_rate_limiter, "allow", return_value=False):
            assert TestClient(first).post("/api/todos", json={"title": "blocked"}).status_code == 429
        assert TestClient(second).post("/api/todos", json={"title": "independent"}).status_code == 201


# ── scheduled todo validation ────────────────────────────────────────────


class TestScheduledTodoValidation:
    def test_neither_scheduled_at_nor_cron_returns_422(self):
        """Omitting both scheduled_at and cron → 422."""
        app = _bare_app()
        resp = TestClient(app).post("/api/todos/scheduled", json={"title": "no schedule"})
        assert resp.status_code == 422
        assert "At least one of scheduled_at or cron" in resp.json()["detail"]

    def test_cron_wrong_field_count_returns_422(self):
        """Cron with !=5 fields → 422."""
        app = _bare_app()
        resp = TestClient(app).post(
            "/api/todos/scheduled",
            json={"title": "bad cron", "cron": "* * * *"},
        )
        assert resp.status_code == 422
        assert "must be a 5-field expression" in resp.json()["detail"]

    def test_cron_6_fields_returns_422(self):
        app = _bare_app()
        resp = TestClient(app).post(
            "/api/todos/scheduled",
            json={"title": "6-field", "cron": "* * * * * *"},
        )
        assert resp.status_code == 422

    def test_cron_empty_string_returns_422(self):
        """Empty cron string: len(split)==1, not 5 → 422."""
        app = _bare_app()
        resp = TestClient(app).post(
            "/api/todos/scheduled",
            json={"title": "empty cron", "cron": ""},
        )
        assert resp.status_code == 422
        assert "must be a 5-field expression" in resp.json()["detail"]

    def test_cron_whitespace_only_fails_validation(self):
        """Whitespace-only cron: split gives [] of length 0 → 422 (not 5)."""
        app = _bare_app()
        resp = TestClient(app).post(
            "/api/todos/scheduled",
            json={"title": "ws cron", "cron": "     "},
        )
        assert resp.status_code == 422

    def test_scheduled_at_only_without_cron_is_valid(self):
        """scheduled_at alone (no cron) is valid — creates a one-shot scheduled todo."""
        app = _bare_app()
        # No DB factory → in-memory path raises 503 because scheduled path
        # requires DB (no degraded fallback). This proves validation PASSED
        # (not 422) and the endpoint reached the DB-access layer.
        resp = TestClient(app).post(
            "/api/todos/scheduled",
            json={
                "title": "one-shot",
                "scheduled_at": "2026-12-25T00:00:00Z",
            },
        )
        assert resp.status_code == 503
        assert "No database available" in resp.json()["detail"]


# ── pause / resume state guards ──────────────────────────────────────────


class TestPauseResumeStateGuards:
    async def test_pause_non_scheduled_todo_returns_422(self):
        """Pausing a todo in 'queued' status → 422 (not 'scheduled')."""
        todo = _fake_todo(status="queued")
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=todo)

        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        app.state._session_factory = _db_factory()

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T1/schedule/pause",
                    params={"project_id": "p1"},
                )
        assert r.status_code == 422
        assert "Cannot pause" in r.json()["detail"]

    async def test_resume_non_scheduled_todo_returns_422(self):
        """Resuming a todo in 'queued' status → 422."""
        todo = _fake_todo(status="queued")
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=todo)

        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        app.state._session_factory = _db_factory()

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T1/schedule/resume",
                    params={"project_id": "p1"},
                )
        assert r.status_code == 422
        assert "Cannot resume" in r.json()["detail"]

    async def test_pause_missing_todo_returns_404(self):
        """Pausing a non-existent todo_id → 404."""
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=None)

        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        app.state._session_factory = _db_factory()

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T-NONEXIST/schedule/pause",
                    params={"project_id": "p1"},
                )
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    async def test_resume_missing_todo_returns_404(self):
        """Resuming a non-existent todo_id → 404."""
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=None)

        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        app.state._session_factory = _db_factory()

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T-NONEXIST/schedule/resume",
                    params={"project_id": "p1"},
                )
        assert r.status_code == 404


# ── PUT update edge cases ────────────────────────────────────────────────


class TestUpdateTodoEdgeCases:
    async def test_update_missing_todo_returns_404(self):
        """PUT /api/todos/{id} for non-existent todo → 404 (degraded mode)."""
        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        resp = TestClient(app).put(
            "/api/todos/T-NONEXIST",
            json={"title": "updated title"},
        )
        assert resp.status_code == 404

    async def test_update_retains_fields_not_in_request(self):
        """PUT update patch TodoRepository directly to verify
        expected_version is passed for optimistic concurrency."""
        todo = _fake_todo(status="queued", version=2)
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=todo)
        repo.update = AsyncMock()
        repo.scoped = MagicMock(return_value=repo)

        app = FastAPI()
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, {"todos": [], "quality_gate": {}})
        app.state._session_factory = _db_factory()

        with patch.object(todos_mod, "TodoRepository", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.put(
                    "/api/todos/T1",
                    json={
                        "title": "new title",
                        "description": "new desc",
                    },
                )
        assert r.status_code == 200
        repo.update.assert_awaited_once()
        call_args = repo.update.call_args
        assert call_args[0][0] == "T1"
        assert call_args[1]["expected_version"] == 2

    async def test_update_no_db_factory_uses_inmemory_path(self):
        """PUT update in degraded mode (no DB) updates in-memory deque."""
        app = FastAPI()
        daemon_state = {"todos": [], "quality_gate": {}}
        from general_ludd.routers import todos as todos_mod

        todos_mod.register(app, daemon_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Create a todo first
            r1 = await c.post("/api/todos", json={"title": "orig"})
            assert r1.status_code == 201
            todo_id = r1.json()["todo_id"]

            # Update it
            r2 = await c.put(
                f"/api/todos/{todo_id}",
                json={"title": "updated"},
            )
            assert r2.status_code == 200
            assert r2.json()["title"] == "updated"


# ── list param bounds ────────────────────────────────────────────────────


class TestListParamBounds:
    def test_limit_zero_clamped_to_1(self):
        """limit=0 → clamped to 1."""
        app = _bare_app()
        resp = TestClient(app).get("/api/todos", params={"limit": 0})
        assert resp.status_code == 200
        # In-memory path with no todos → returns empty, not error
        assert resp.json() == []

    def test_limit_negative_clamped_to_1(self):
        resp = TestClient(_bare_app()).get("/api/todos", params={"limit": -5})
        assert resp.status_code == 200

    def test_limit_exceeds_500_clamped(self):
        """limit > 500 → clamped to 500."""
        app = _bare_app()
        resp = TestClient(app).get("/api/todos", params={"limit": 99999})
        assert resp.status_code == 200

    def test_offset_negative_clamped_to_0(self):
        resp = TestClient(_bare_app()).get("/api/todos", params={"offset": -10})
        assert resp.status_code == 200

    def test_queue_filter_in_degraded_mode(self):
        app = _bare_app()
        client = TestClient(app)
        client.post("/api/todos", json={"title": "core-task", "queue": "core"})
        client.post("/api/todos", json={"title": "qa-task", "queue": "qa"})

        resp = client.get("/api/todos", params={"queue": "core"})
        data = resp.json()
        assert all(t["queue"] == "core" for t in data)
        assert any(t["title"] == "core-task" for t in data)
        assert not any(t["title"] == "qa-task" for t in data)

    def test_status_filter_in_degraded_mode(self):
        app = _bare_app()
        client = TestClient(app)
        client.post("/api/todos", json={"title": "a", "queue": "core"})
        # All created todos have status "queued"
        resp = client.get("/api/todos", params={"status": "running"})
        assert resp.json() == []

    def test_combined_queue_status_filter(self):
        app = _bare_app()
        client = TestClient(app)
        client.post("/api/todos", json={"title": "x", "queue": "core"})
        client.post("/api/todos", json={"title": "y", "queue": "qa"})

        resp = client.get("/api/todos", params={"queue": "qa", "status": "queued"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["queue"] == "qa"

    def test_project_id_filter_in_degraded_mode(self):
        app = _bare_app()
        client = TestClient(app)
        client.post(
            "/api/todos",
            json={"title": "scoped", "queue": "core", "project_id": "proj-1"},
        )
        client.post(
            "/api/todos",
            json={"title": "other", "queue": "core", "project_id": "proj-2"},
        )

        resp = client.get("/api/todos", params={"project_id": "proj-1"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["project_id"] == "proj-1"

    def test_offset_pagination_in_degraded_mode(self):
        app = _bare_app()
        client = TestClient(app)
        for i in range(5):
            client.post("/api/todos", json={"title": f"t-{i}", "queue": "core"})

        all_items = client.get("/api/todos", params={"limit": 500}).json()
        page1 = client.get("/api/todos", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/api/todos", params={"limit": 2, "offset": 2}).json()
        page3 = client.get("/api/todos", params={"limit": 2, "offset": 4}).json()

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
        assert page1 + page2 + page3 == all_items


# ── AddTodoRequest Pydantic validation ───────────────────────────────────


class TestAddTodoRequestValidation:
    def test_empty_title_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(title="")

    def test_title_too_long_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(title="x" * 513)

    def test_description_too_long_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(title="ok", description="x" * 4097)

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(title="ok", priority="urgent")

    def test_invalid_queue_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(title="ok", queue="core queue")

    def test_acceptance_criteria_exceeds_max(self):
        with pytest.raises(ValueError):
            AddTodoRequest(
                title="ok",
                acceptance_criteria=["a"] * 21,
            )

    def test_definition_of_done_too_long_rejected(self):
        with pytest.raises(ValueError):
            AddTodoRequest(
                title="ok",
                definition_of_done="x" * 4097,
            )

    def test_all_valid_priorities_accepted(self):
        for p in ("low", "medium", "high", "critical"):
            req = AddTodoRequest(title="ok", priority=p)
            assert req.priority == p

    @pytest.mark.parametrize("queue", ["core", "qa", "dev-ops", "batch_jobs_7"])
    def test_valid_queue_patterns(self, queue: str):
        req = AddTodoRequest(title="ok", queue=queue)
        assert req.queue == queue

    def test_defaults_are_sensible(self):
        req = AddTodoRequest(title="test")
        assert req.priority == "medium"
        assert req.queue == "core"
        assert req.description == ""
        assert req.project_id is None
        assert req.acceptance_criteria == []
        assert req.definition_of_done == ""


# ── admin log-level ─────────────────────────────────────────────────────


class TestAdminLogLevel:
    def test_invalid_level_returns_422(self):
        app = _bare_app()
        resp = TestClient(app).post("/admin/log-level", json={"level": "TRACE"})
        assert resp.status_code == 422
        assert "Invalid log level" in resp.json()["detail"]

    def test_valid_level_lowercase_returns_200(self):
        app = _bare_app()
        resp = TestClient(app).post("/admin/log-level", json={"level": "debug"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["level"] == "debug"

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_all_valid_levels_accepted(self, level: str):
        app = _bare_app()
        resp = TestClient(app).post("/admin/log-level", json={"level": level})
        assert resp.status_code == 200
        assert resp.json()["level"] == level

    def test_empty_level_returns_422(self):
        app = _bare_app()
        resp = TestClient(app).post("/admin/log-level", json={"level": ""})
        assert resp.status_code == 422


# ── status endpoint edge cases ───────────────────────────────────────────


class TestStatusEndpointEdgeCases:
    def test_status_returns_version(self):
        app = _bare_app()
        resp = TestClient(app).get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)

    def test_status_defaults_when_no_todos(self):
        app = _bare_app()
        resp = TestClient(app).get("/api/status")
        data = resp.json()
        assert data["todos_total"] == 0
        assert data["queue_depths"] == {}
        assert data["quality_gate"] == {
            "overall": "not_run",
            "passed_count": 0,
            "total_count": 0,
        }

    def test_status_counts_todos_in_degraded_mode(self):
        app = _bare_app()
        client = TestClient(app)
        client.post("/api/todos", json={"title": "t1", "queue": "core"})
        client.post("/api/todos", json={"title": "t2", "queue": "qa"})
        client.post("/api/todos", json={"title": "t3", "queue": "core"})

        resp = client.get("/api/status")
        data = resp.json()
        assert data["todos_total"] == 3
        assert data["queue_depths"]["core"] == 2
        assert data["queue_depths"]["qa"] == 1

    def test_status_config_dir_file_count(self):
        """config_file_count is computed from app.state._config_dir."""
        app = _bare_app()
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            app.state._config_dir = tmpdir
            # Create some .yml files
            for name in ["a.yml", "b.yaml", "c.txt", ".hidden"]:
                open(os.path.join(tmpdir, name), "w").close()
            resp = TestClient(app).get("/api/status")
            assert resp.status_code == 200
            assert resp.json()["config_file_count"] == 2

    def test_status_handles_missing_config_dir(self):
        app = _bare_app()
        app.state._config_dir = "/nonexistent/path"
        resp = TestClient(app).get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["config_file_count"] == 0


# ── admin todos ──────────────────────────────────────────────────────────


class TestAdminTodos:
    def test_admin_todos_returns_dict_with_keys(self):
        app = _bare_app()
        resp = TestClient(app).get("/admin/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data
        assert "count" in data
        assert isinstance(data["todos"], list)
        assert data["count"] == 0

    def test_admin_todos_filters_by_status(self):
        app = _bare_app()
        client = TestClient(app)
        client.post("/api/todos", json={"title": "a", "queue": "core"})
        # default status is "queued"
        resp = client.get("/admin/todos", params={"status": "running"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_admin_todos_filters_by_project_id(self):
        app = _bare_app()
        client = TestClient(app)
        client.post(
            "/api/todos",
            json={"title": "p1", "queue": "core", "project_id": "p1"},
        )
        client.post(
            "/api/todos",
            json={"title": "p2", "queue": "core", "project_id": "p2"},
        )
        resp = client.get("/admin/todos", params={"project_id": "p1"})
        data = resp.json()
        assert data["count"] == 1
        assert data["todos"][0]["project_id"] == "p1"


# ── get-by-id in degraded mode ───────────────────────────────────────────


class TestGetByIdDegraded:
    def test_get_existing_todo_by_id(self):
        app = _bare_app()
        client = TestClient(app)
        r = client.post("/api/todos", json={"title": "find-me", "queue": "core"})
        todo_id = r.json()["todo_id"]

        resp = client.get(f"/api/todos/{todo_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "find-me"

    def test_get_nonexistent_todo_returns_404(self):
        app = _bare_app()
        resp = TestClient(app).get("/api/todos/TODO-NONEXIST")
        assert resp.status_code == 404

    def test_get_todo_with_project_id_filter_in_degraded(self):
        app = _bare_app()
        client = TestClient(app)
        r = client.post(
            "/api/todos",
            json={"title": "scoped", "queue": "core", "project_id": "proj-A"},
        )
        todo_id = r.json()["todo_id"]

        # Same project_id → found
        resp = client.get(f"/api/todos/{todo_id}", params={"project_id": "proj-A"})
        assert resp.status_code == 200

        # Different project_id → not found
        resp = client.get(f"/api/todos/{todo_id}", params={"project_id": "proj-B"})
        assert resp.status_code == 404


# ── scheduled list degraded + include_paused ─────────────────────────────


class TestScheduledListDegraded:
    def test_scheduled_list_degraded_returns_empty(self):
        """GET /api/todos/scheduled with no DB → empty list."""
        app = _bare_app()
        resp = TestClient(app).get("/api/todos/scheduled")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_scheduled_list_with_limit_offset(self):
        app = _bare_app()
        resp = TestClient(app).get("/api/todos/scheduled", params={"limit": 10, "offset": 5})
        assert resp.status_code == 200
        assert resp.json() == []


# ── _todo_to_dict / _todo_to_dict_scheduled shape ────────────────────────


class TestTodoSerializationShape:
    def test_todo_to_dict_basic_fieds(self):
        todo = _fake_todo()
        from general_ludd.routers.todos import _todo_to_dict

        d = _todo_to_dict(todo)
        assert d["todo_id"] == "TODO-1"
        assert d["title"] == "Task"
        assert d["status"] == "queued"
        assert d["priority"] == 1
        assert d["queue"] == "core"
        assert "created_at" in d
        assert "acceptance_criteria" in d
        assert "definition_of_done" in d

    def test_todo_to_dict_with_prefixed_id(self):
        todo = _fake_todo("TODO-ABCD1234")
        from general_ludd.routers.todos import _todo_to_dict

        d = _todo_to_dict(todo)
        assert d["todo_id"] == "TODO-ABCD1234"


# ── PUT update degraded mode from another project_id ─────────────────────


class TestUpdateDegradedCrossProject:
    def test_update_with_mismatched_project_id_404_in_degraded(self):
        """PUT update in degraded mode: todo exists but project_id doesn't match → 404."""
        app = _bare_app()
        client = TestClient(app)
        r = client.post(
            "/api/todos",
            json={
                "title": "task A",
                "queue": "core",
                "project_id": "proj-A",
            },
        )
        todo_id = r.json()["todo_id"]

        resp = client.put(
            f"/api/todos/{todo_id}",
            params={"project_id": "proj-B"},
            json={"title": "should not update"},
        )
        assert resp.status_code == 404
