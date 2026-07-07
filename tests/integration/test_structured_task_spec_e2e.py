"""End-to-end tests: structured task-spec / acceptance_criteria through the API."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.routers.todos import register


@pytest_asyncio.fixture
async def test_app():
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

        from general_ludd.daemon import _daemon_state
        _daemon_state["todos"] = []
        _daemon_state["tick_metrics"] = {}
        _daemon_state["quality_gate"] = {}

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

        register(app, _daemon_state)
        yield app, engine, factory
    finally:
        await engine.dispose()


class TestStructuredTaskSpecE2E:
    @pytest.mark.asyncio
    async def test_create_todo_with_acceptance_criteria_persists(self, test_app):
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Implement login page",
                    "acceptance_criteria": [
                        "User can log in with email and password",
                        "Invalid password shows error message",
                        "Rate-limited after 5 failed attempts",
                    ],
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["title"] == "Implement login page"
            assert "todo_id" in data

            async with factory() as session:
                repo = TodoRepository(session)
                todo = await repo.get_by_id(data["todo_id"])
                assert todo is not None
                if isinstance(todo.acceptance_criteria, str):
                    criteria = json.loads(todo.acceptance_criteria)
                else:
                    criteria = todo.acceptance_criteria
                assert len(criteria) == 3
                assert "User can log in with email and password" in criteria

    @pytest.mark.asyncio
    async def test_create_todo_with_definition_of_done_persists(self, test_app):
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Add unit tests for auth module",
                    "definition_of_done": "All tests pass, code reviewed, coverage >= 80%",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["title"] == "Add unit tests for auth module"
            assert data["definition_of_done"] == "All tests pass, code reviewed, coverage >= 80%"

            async with factory() as session:
                repo = TodoRepository(session)
                todo = await repo.get_by_id(data["todo_id"])
                assert todo is not None
                assert todo.definition_of_done == "All tests pass, code reviewed, coverage >= 80%"

    @pytest.mark.asyncio
    async def test_create_todo_with_both_spec_fields(self, test_app):
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Deploy monitoring stack",
                    "acceptance_criteria": [
                        "Prometheus scrapes all targets",
                        "Grafana dashboards load",
                        "Alerts fire on 5xx errors",
                    ],
                    "definition_of_done": "Monitoring stack deployed, dashboards verified, alerts tested",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["title"] == "Deploy monitoring stack"
            assert "todo_id" in data

            async with factory() as session:
                repo = TodoRepository(session)
                todo = await repo.get_by_id(data["todo_id"])
                assert todo is not None
                if isinstance(todo.acceptance_criteria, str):
                    criteria = json.loads(todo.acceptance_criteria)
                else:
                    criteria = todo.acceptance_criteria
                assert len(criteria) == 3
                assert todo.definition_of_done == "Monitoring stack deployed, dashboards verified, alerts tested"

    @pytest.mark.asyncio
    async def test_get_todo_returns_criteria_and_dod(self, test_app):
        app, _engine, factory = test_app
        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-SPEC1",
                "title": "Implement rate limiter",
                "description": "Add token bucket rate limiter",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "acceptance_criteria": json.dumps([
                    "Requests under limit pass through",
                    "Requests over limit get 429",
                    "Bucket refills at configured rate",
                ]),
                "definition_of_done": (
                    "Rate limiter integrated, tested under load, config documented"
                ),
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos?todo_id=TODO-SPEC1")
            assert resp.status_code == 200
            todos = resp.json()
            matching = [t for t in todos if t["todo_id"] == "TODO-SPEC1"]
            assert len(matching) == 1
            todo = matching[0]
            assert "acceptance_criteria" in todo
            criteria = todo["acceptance_criteria"]
            assert len(criteria) == 3
            assert "Requests under limit pass through" in criteria
            assert todo["definition_of_done"] == "Rate limiter integrated, tested under load, config documented"

    @pytest.mark.asyncio
    async def test_empty_criteria_and_dod_defaults(self, test_app):
        app, _engine, _factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "Simple task"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["acceptance_criteria"] == []
            assert data["definition_of_done"] == ""

    @pytest.mark.asyncio
    async def test_reject_empty_title_with_criteria(self, test_app):
        app, _engine, _factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "", "acceptance_criteria": ["Must have a title"]},
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_criteria_persisted_as_json_array_in_db(self, test_app):
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "JSON persistence check",
                    "acceptance_criteria": ["A", "B", "C"],
                },
            )
            assert resp.status_code == 201
            data = resp.json()

            async with factory() as session:
                repo = TodoRepository(session)
                todo = await repo.get_by_id(data["todo_id"])
                assert todo is not None
                raw = todo.acceptance_criteria
                assert raw is not None
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                assert parsed == ["A", "B", "C"]
