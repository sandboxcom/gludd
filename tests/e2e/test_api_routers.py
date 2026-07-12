"""E2E tests for gludd API routers: facts, todos, healthz, readyz, projects, human-todos.

Exercises real FastAPI apps with in-memory SQLite and ASGITransport/AsyncClient.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base


@pytest_asyncio.fixture
async def db_app():
    """FastAPI app with in-memory SQLite and all router-tested tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        app = FastAPI()
        app.state._session_factory = factory
        app.state._db_engine = engine
        app.state._config_dir = None
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._templates_dir = None
        app.state._playbooks_dir = None
        app.state._metrics_collector = None
        app.state._project_manager = None
        app.state._recent_traces = None
        app.state._skill_registry = None
        app.state._spend_limiter = None
        app.state._dispatch_facet = None
        app.state._otel_bridge = None
        app.state._schedule_last_plan = None
        app.state._filestore = None
        app.state._hardware = None

        daemon_state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        app.state.daemon_state = daemon_state

        from general_ludd.routers.facts import register as register_facts
        from general_ludd.routers.human_todos import register as register_human_todos
        from general_ludd.routers.todos import register as register_todos

        register_facts(app, daemon_state)
        register_todos(app, daemon_state)
        register_human_todos(app, daemon_state)

        yield app, factory
    finally:
        await engine.dispose()


@pytest.fixture
def health_app(monkeypatch: pytest.MonkeyPatch):
    """Daemon app for healthz/readyz tests (no DB, no lifespan)."""
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


@pytest.fixture
def project_app(monkeypatch: pytest.MonkeyPatch):
    """Daemon app for /admin/projects tests with in-memory DB."""
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def _init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state._session_factory = factory
        app.state._db_engine = engine

    asyncio.run(_init_db())

    client = TestClient(app)
    yield client
    asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# /readyz and /healthz
# ---------------------------------------------------------------------------

class TestHealthReadiness:
    def test_healthz_returns_healthy(self, health_app):
        resp = health_app.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "no_auth" in data
        assert "budget_exhausted" in data
        assert data["status"] in {"healthy"}

    def test_readyz_returns_ready(self, health_app):
        resp = health_app.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_readyz_degraded_when_flag_set(self, health_app):
        health_app.app.state._degraded = "test-degraded"
        resp = health_app.get("/readyz")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["reason"] == "test-degraded"

    def test_healthz_degraded_when_flag_set(self, health_app):
        health_app.app.state._degraded = "test-degraded"
        resp = health_app.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["reason"] == "test-degraded"

    def test_healthz_exposes_security_posture(self, health_app):
        resp = health_app.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "no_auth" in data
        assert "require_auth" in data
        assert "allow_no_auth" in data
        assert "auth_degraded" in data
        assert "budget_exhausted" in data


# ---------------------------------------------------------------------------
# /api/facts
# ---------------------------------------------------------------------------

class TestApiFacts:
    @pytest.mark.asyncio
    async def test_facts_returns_200(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/facts")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_facts_has_expected_keys(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/facts")
            data = resp.json()
            expected_sections = [
                "work",
                "todos",
                "models",
                "history",
                "messages",
                "metrics",
                "traces",
                "codebase",
                "features",
                "dispatch",
                "spend",
                "accounting",
                "schedule",
                "coordination",
                "osquery",
                "project_id",
            ]
            for key in expected_sections:
                assert key in data, f"facts missing key: {key}"

    @pytest.mark.asyncio
    async def test_facts_todos_empty_initially(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/facts")
            data = resp.json()
            assert data["todos"]["total"] == 0

    @pytest.mark.asyncio
    async def test_facts_todos_reflect_created_todo(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-FACTS-1",
                "title": "Facts integration test",
                "description": "",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/facts")
            data = resp.json()
            assert data["todos"]["total"] >= 1


# ---------------------------------------------------------------------------
# /api/todos — CRUD
# ---------------------------------------------------------------------------

class TestTodosCRUD:
    @pytest.mark.asyncio
    async def test_create_todo(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "CRUD create test",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "todo_id" in data
            assert data["title"] == "CRUD create test"
            assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_read_todos_list(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            for i in range(3):
                await repo.create(todo_data={
                    "todo_id": f"TODO-LIST-{i}",
                    "title": f"List todo {i}",
                    "description": "",
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                    "status": "queued",
                    "project_id": "e2e-project",
                })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos", params={"project_id": "e2e-project"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) >= 3

    @pytest.mark.asyncio
    async def test_read_todo_by_id(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-BYID",
                "title": "Get by ID",
                "description": "",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos/TODO-BYID")
            assert resp.status_code == 200
            data = resp.json()
            assert data["todo_id"] == "TODO-BYID"
            assert data["title"] == "Get by ID"

    @pytest.mark.asyncio
    async def test_read_todo_not_found(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos/NONEXISTENT")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_todo(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-UPDATE",
                "title": "Original title",
                "description": "original",
                "queue": "core",
                "priority": "low",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/todos/TODO-UPDATE",
                json={
                    "title": "Updated title",
                    "description": "updated",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "Updated title"
            assert data["description"] == "updated"

    @pytest.mark.asyncio
    async def test_todos_list_by_status(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-ACTIVE",
                "title": "Active",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "status": "active",
                "project_id": "e2e-status",
            })
            await repo.create(todo_data={
                "todo_id": "TODO-QUEUED",
                "title": "Queued",
                "queue": "bugs",
                "priority": "low",
                "work_type": "fix",
                "status": "queued",
                "project_id": "e2e-status",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/todos",
                params={"project_id": "e2e-status", "status": "active"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) >= 1
            assert data[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_api_status_endpoint(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import TodoRepository

        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-STATUS",
                "title": "Status test",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "version" in data
            assert "todos_total" in data
            assert data["todos_total"] >= 1


# ---------------------------------------------------------------------------
# /admin/projects
# ---------------------------------------------------------------------------

class TestAdminProjects:
    def test_list_projects_returns_200(self, project_app):
        resp = project_app.get("/admin/projects")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_add_project_succeeds_with_valid_input(self, project_app):
        resp = project_app.post(
            "/admin/projects",
            json={"name": "test-project", "weight": 0.5, "description": "e2e test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "project_id" in data
        assert data["name"] == "test-project"

    def test_set_dispatch_mode(self, project_app):
        resp = project_app.put(
            "/admin/dispatch/mode",
            json={"mode": "passive_external"},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatch_mode"] == "passive_external"

    def test_set_dispatch_mode_invalid(self, project_app):
        resp = project_app.put(
            "/admin/dispatch/mode",
            json={"mode": "invalid_mode"},
        )
        assert resp.status_code == 400

    def test_self_improve_endpoint(self, project_app):
        resp = project_app.post("/admin/self-improve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "findings_count" in data


# ---------------------------------------------------------------------------
# /api/human-todos
# ---------------------------------------------------------------------------

class TestHumanTodos:
    @pytest.mark.asyncio
    async def test_create_human_todo(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "test-agent",
                    "title": "Need permission",
                    "body": "Please approve access to S3 bucket.",
                    "category": "permission_escalation",
                    "priority": "high",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "id" in data
            assert data["title"] == "Need permission"
            assert data["category"] == "permission_escalation"
            assert data["status"] == "open"

    @pytest.mark.asyncio
    async def test_create_human_todo_invalid_category(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "test-agent",
                    "title": "Bad category",
                    "body": "Testing invalid input",
                    "category": "nonexistent_category",
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_human_todos(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import HumanTodoRepository

        async with factory() as session:
            repo = HumanTodoRepository(session)
            await repo.create(
                agent_id="agent-1",
                title="Task 1",
                body="Requesting approval",
                category="permission_escalation",
                priority="high",
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) >= 1
            assert data[0]["category"] == "permission_escalation"

    @pytest.mark.asyncio
    async def test_get_human_todo_by_id(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import HumanTodoRepository

        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.create(
                agent_id="agent-2",
                title="Specific task",
                body="Need input on design",
                category="decision",
            )
            await session.commit()
            htid = str(row.id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/human-todos/{htid}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "Specific task"

    @pytest.mark.asyncio
    async def test_get_human_todo_not_found(self, db_app):
        app, _factory = db_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos/999999")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_human_todo_done(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import HumanTodoRepository

        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.create(
                agent_id="agent-3",
                title="Resolve this",
                body="Please decide",
                category="decision",
            )
            await session.commit()
            htid = str(row.id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/human-todos/{htid}",
                json={
                    "status": "in_progress",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "in_progress"

            resp = await client.patch(
                f"/api/human-todos/{htid}",
                json={
                    "status": "done",
                    "human_resolver": "operator-1",
                    "human_resolution": "Approved the access.",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "done"

    @pytest.mark.asyncio
    async def test_delete_human_todo(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import HumanTodoRepository

        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.create(
                agent_id="agent-4",
                title="Delete me",
                body="This should be dismissed",
                category="decision",
            )
            await session.commit()
            htid = str(row.id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/human-todos/{htid}")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_human_todos_by_category(self, db_app):
        app, factory = db_app
        from general_ludd.db.repository import HumanTodoRepository

        async with factory() as session:
            repo = HumanTodoRepository(session)
            await repo.create(
                agent_id="agent-5",
                title="Escalation needed",
                body="Need more permissions",
                category="permission_escalation",
            )
            await repo.create(
                agent_id="agent-6",
                title="Input request",
                body="Need input on X",
                category="input_request",
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/human-todos", params={"category": "permission_escalation"}
            )
            assert resp.status_code == 200
            data = resp.json()
            for item in data:
                assert item["category"] == "permission_escalation"
