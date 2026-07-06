"""Cross-tenant isolation tests for src/general_ludd/routers/todos.py.

All DB I/O is replaced with mocks — no real SQLite is started. Tests verify:

  - GET /api/todos (unscoped) in multi-project mode → [] (no tenant leak)
  - GET /api/todos?project_id=A  uses scoped repo; B's todos absent
  - GET /api/todos/{id}?project_id=B  cross-tenant → 404
  - GET /api/todos/{id}?project_id=A  correct project → 200
  - POST .../schedule/pause?project_id=B  cross-tenant → 404
  - POST .../schedule/resume?project_id=B  cross-tenant → 404
  - POST .../schedule/pause (no project_id)  → 422 (missing required param)
  - POST /api/todos  no project_id in multi-project → 201 (global/unscoped todo)
  - POST /api/todos  unknown project_id → 422
  - POST /api/todos  no PM / single-project: project_id=None allowed (back-compat)
  - TodoRepository.claim_runnable (INTERNAL) unaffected by router-layer scoping
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.routers import todos as todos_mod

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _pm(*project_ids: str) -> MagicMock:
    """Fake ProjectManager advertising the given active project IDs."""
    pm = MagicMock()
    projects: list[Any] = []
    for pid in project_ids:
        p = MagicMock()
        p.project_id = pid
        projects.append(p)
    pm.list_active.return_value = projects
    return pm


def _fake_todo(todo_id: str, project_id: str, status: str = "queued") -> MagicMock:
    """ORM-row stub compatible with _todo_to_dict / _todo_to_dict_scheduled."""
    t = MagicMock()
    t.todo_id = todo_id
    t.project_id = project_id
    t.title = f"Task {todo_id}"
    t.description = ""
    t.queue = "core"
    t.priority = 1
    t.work_type = "code"
    t.status = status
    t.version = 1
    t.created_at = None
    t.scheduled_at = None
    t.cron = None
    t.schedule_timezone = "UTC"
    t.next_run_at = None
    t.last_run_at = None
    t.run_count = 0
    t.max_runs = None
    t.schedule_paused = False
    return t


def _repo_mock(
    *,
    get_by_id_return: Any = None,
    list_all_return: list[Any] | None = None,
) -> MagicMock:
    """Return a mock TodoRepository with common async methods pre-wired."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=get_by_id_return)
    repo.list_all = AsyncMock(return_value=list_all_return if list_all_return is not None else [])
    repo.update = AsyncMock()
    repo.create = AsyncMock()
    return repo


def _db_factory() -> Any:
    """Minimal async session-factory (yields a no-op AsyncMock session)."""

    @asynccontextmanager
    async def _f():
        s = AsyncMock()
        s.commit = AsyncMock()
        yield s

    return _f


def _build_app(
    pm: Any | None = None,
    with_db: bool = False,
) -> FastAPI:
    """Build a minimal FastAPI with the todos router registered."""
    app = FastAPI()
    daemon_state: dict[str, Any] = {"todos": [], "quality_gate": {}}
    todos_mod.register(app, daemon_state)
    if pm is not None:
        app.state._project_manager = pm
    if with_db:
        app.state._session_factory = _db_factory()
    return app


# ---------------------------------------------------------------------------
# GET /api/todos — list isolation
# ---------------------------------------------------------------------------

class TestListTodosIsolation:
    async def test_unscoped_multiproject_returns_empty(self) -> None:
        """No project_id + PM with 2 active projects → [] (no cross-tenant leak)."""
        app = _build_app(pm=_pm("proj-A", "proj-B"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/todos")
        assert r.status_code == 200
        assert r.json() == []

    async def test_scoped_list_excludes_other_project(self) -> None:
        """GET /api/todos?project_id=proj-A only returns proj-A todos."""
        todo_a = _fake_todo("T-A1", "proj-A")
        repo = _repo_mock(list_all_return=[todo_a])

        app = _build_app(pm=_pm("proj-A", "proj-B"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/todos", params={"project_id": "proj-A"})

        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["project_id"] == "proj-A"
        assert all(t["project_id"] == "proj-A" for t in data)

    async def test_no_pm_single_project_no_filter(self) -> None:
        """No PM and no project_id: unscoped path is fine (back-compat)."""
        app = _build_app(pm=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/todos")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# GET /api/todos/scheduled — scheduled-list isolation
# ---------------------------------------------------------------------------

class TestListScheduledTodosIsolation:
    async def test_unscoped_multiproject_returns_empty(self) -> None:
        """No project_id + PM with active projects → [] (no scheduled-todo leak)."""
        app = _build_app(pm=_pm("proj-A", "proj-B"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/todos/scheduled")
        assert r.status_code == 200
        assert r.json() == []

    async def test_scoped_list_excludes_other_project(self) -> None:
        """GET /api/todos/scheduled?project_id=proj-A only returns proj-A items."""
        sched_a = _fake_todo("S-A1", "proj-A", status="scheduled")
        repo = _repo_mock(list_all_return=[sched_a])
        app = _build_app(pm=_pm("proj-A", "proj-B"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/todos/scheduled", params={"project_id": "proj-A"})

        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert all(t["project_id"] == "proj-A" for t in data)


# ---------------------------------------------------------------------------
# POST /api/todos/scheduled — scheduled-create isolation
# ---------------------------------------------------------------------------

class TestCreateScheduledTodoIsolation:
    async def test_unknown_project_id_422(self) -> None:
        """Unknown project_id in multi-project mode → 422 (before any DB write)."""
        app = _build_app(pm=_pm("proj-A"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/todos/scheduled",
                json={
                    "title": "test",
                    "project_id": "proj-GHOST",
                    "cron": "0 9 * * 1-5",
                },
            )
        assert r.status_code == 422
        assert "Unknown project_id" in r.json()["detail"]

    async def test_no_project_id_in_multiproject_allowed(self) -> None:
        """project_id=None (omitted) in multi-project mode → ALLOWED (creates a
        global/unscoped scheduled todo). A null project_id is NOT a cross-tenant
        leak because unscoped LIST queries return [] in multi-project mode. This
        matches the established contract in
        tests/security/test_router_auth_redteam.py::
        TestCrossProjectTodoCreate::test_null_project_id_always_allowed.
        """
        fake = _fake_todo("S-G1", None, status="scheduled")
        repo = _repo_mock()
        repo.create = AsyncMock(return_value=fake)
        app = _build_app(pm=_pm("proj-A"), with_db=True)
        with patch.object(todos_mod, "TodoRepository", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/scheduled",
                    json={"title": "test", "cron": "0 9 * * 1-5"},
                )
        assert r.status_code == 201, r.text
        assert r.json()["project_id"] is None


# ---------------------------------------------------------------------------
# GET /api/todos/{todo_id} — per-item isolation
# ---------------------------------------------------------------------------

class TestGetTodoIsolation:
    async def test_cross_tenant_id_returns_404(self) -> None:
        """Todo belongs to proj-A; requesting with project_id=proj-B → 404."""
        # scoped to proj-B → get_by_id returns None (filtered out in SQL)
        repo = _repo_mock(get_by_id_return=None)
        app = _build_app(pm=_pm("proj-A", "proj-B"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/todos/T-A1", params={"project_id": "proj-B"})

        assert r.status_code == 404

    async def test_correct_project_returns_200(self) -> None:
        """Todo belongs to proj-A; requesting with project_id=proj-A → 200."""
        todo = _fake_todo("T-A1", "proj-A")
        repo = _repo_mock(get_by_id_return=todo)
        app = _build_app(pm=_pm("proj-A"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/todos/T-A1", params={"project_id": "proj-A"})

        assert r.status_code == 200
        body = r.json()
        assert body["todo_id"] == "T-A1"
        assert body["project_id"] == "proj-A"

    async def test_get_todo_missing_project_id_allowed_404_when_absent(self) -> None:
        """GET /api/todos/{id} treats project_id as OPTIONAL (back-compat, matching
        test_no_pm_no_project_id_allowed): an omitted project_id is not a validation
        error. With no PM/DB and no such todo, the endpoint returns 404 (genuinely
        absent) rather than 422."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/todos/T-A1")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/todos/{id}/schedule/pause|resume — isolation
# ---------------------------------------------------------------------------

class TestSchedulePauseResumeIsolation:
    async def test_pause_cross_tenant_404(self) -> None:
        """Pause with wrong project_id: scoped get_by_id returns None → 404."""
        repo = _repo_mock(get_by_id_return=None)
        app = _build_app(pm=_pm("proj-A", "proj-B"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T-A1/schedule/pause",
                    params={"project_id": "proj-B"},
                )

        assert r.status_code == 404

    async def test_resume_cross_tenant_404(self) -> None:
        """Resume with wrong project_id → 404."""
        repo = _repo_mock(get_by_id_return=None)
        app = _build_app(pm=_pm("proj-A", "proj-B"), with_db=True)

        with patch.object(todos_mod.TodoRepository, "scoped", return_value=repo):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    "/api/todos/T-A1/schedule/resume",
                    params={"project_id": "proj-B"},
                )

        assert r.status_code == 404

    async def test_pause_missing_project_id_422(self) -> None:
        """FastAPI requires project_id on pause; omitting it → 422 validation error."""
        app = _build_app(with_db=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos/T-A1/schedule/pause")
        assert r.status_code == 422

    async def test_resume_missing_project_id_422(self) -> None:
        """FastAPI requires project_id on resume; omitting it → 422 validation error."""
        app = _build_app(with_db=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos/T-A1/schedule/resume")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/todos — create isolation
# ---------------------------------------------------------------------------

class TestCreateTodoIsolation:
    async def test_unknown_project_id_422(self) -> None:
        """Unknown project_id in multi-project mode → 422 Unprocessable Entity."""
        app = _build_app(pm=_pm("proj-A"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos", json={"title": "test", "project_id": "proj-GHOST"})
        assert r.status_code == 422
        assert "Unknown project_id" in r.json()["detail"]

    async def test_no_project_id_in_multiproject_allowed(self) -> None:
        """project_id=None (omitted) in multi-project mode → ALLOWED (creates a
        global/unscoped todo). A null project_id is NOT a cross-tenant leak
        because unscoped LIST queries return [] in multi-project mode. This
        matches the established contract in
        tests/security/test_router_auth_redteam.py::
        TestCrossProjectTodoCreate::test_null_project_id_always_allowed.
        """
        app = _build_app(pm=_pm("proj-A"))
        # No DB factory → in-memory path; 201 with the created global todo.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos", json={"title": "test"})
        assert r.status_code == 201, r.text
        assert r.json()["project_id"] is None

    async def test_no_pm_no_project_id_allowed(self) -> None:
        """Single-project / no-PM mode: project_id=None is fine (back-compat)."""
        app = _build_app(pm=None)
        # No DB factory → in-memory path; 201 with the created todo.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos", json={"title": "single-tenant task"})
        assert r.status_code == 201

    async def test_pm_with_no_active_projects_allows_no_project_id(self) -> None:
        """PM present but no active projects: behaves like no-PM (back-compat)."""
        app = _build_app(pm=_pm())  # PM with zero active projects
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/todos", json={"title": "zero-project task"})
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# TodoRepository.claim_runnable — INTERNAL unscoped path must not break
# ---------------------------------------------------------------------------

class TestClaimRunnableUnscoped:
    async def test_claim_runnable_unscoped_repo_not_broken(self) -> None:
        """claim_runnable with project_id=None on an unscoped repo must not raise.

        The router-layer scoping changes must NOT reach into the internal
        cross-tenant scheduling path. An unscoped TodoRepository must still be
        constructable and runnable.
        """
        from general_ludd.db.repository import TodoRepository

        session = AsyncMock()
        # Empty candidate result → no UPDATE loop → safe to mock simply.
        candidates = MagicMock()
        candidates.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=candidates)
        session.flush = AsyncMock()

        # Unscoped (admin / cross-tenant path): project_id=None in _resolve_pid
        # propagates None → no WHERE project_id clause in the SELECT.
        repo = TodoRepository(session)
        claimed = await repo.claim_runnable(limit=5, project_id=None)
        assert isinstance(claimed, list)
        assert claimed == []

    async def test_claim_runnable_scoped_repo_not_broken(self) -> None:
        """claim_runnable via a per-project scoped repo must also not raise."""
        from general_ludd.db.repository import TodoRepository

        session = AsyncMock()
        candidates = MagicMock()
        candidates.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=candidates)
        session.flush = AsyncMock()

        repo = TodoRepository.scoped(session, "proj-A")
        claimed = await repo.claim_runnable(limit=5)
        assert isinstance(claimed, list)
        assert claimed == []
