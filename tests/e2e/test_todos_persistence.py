from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.routers.todos import register


async def _create_test_app() -> tuple[FastAPI, async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from general_ludd.daemon import _daemon_state
    _daemon_state["todos"] = []

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
    return app, factory


class TestTodosPersistence:
    @pytest.mark.asyncio
    async def test_post_todo_persists_to_db(self):
        app, factory = await _create_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "Fix the bug", "queue": "core", "priority": "high", "work_type": "code"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "todo_id" in data

            async with factory() as session:
                repo = TodoRepository(session)
                todo = await repo.get_by_id(data["todo_id"])
                assert todo is not None
                assert todo.title == "Fix the bug"
                assert todo.queue == "core"

    @pytest.mark.asyncio
    async def test_get_todos_reads_from_db(self):
        app, factory = await _create_test_app()
        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-TEST1",
                "title": "Test todo",
                "description": "A test",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) >= 1
            assert any(t.get("todo_id") == "TODO-TEST1" for t in data)

    @pytest.mark.asyncio
    async def test_get_todo_by_id_reads_from_db(self):
        app, factory = await _create_test_app()
        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-TEST2",
                "title": "Another test",
                "description": "",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
                "status": "queued",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos/TODO-TEST2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["todo_id"] == "TODO-TEST2"
            assert data["title"] == "Another test"

    @pytest.mark.asyncio
    async def test_get_todo_not_found_returns_404(self):
        app, factory = await _create_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos/NONEXISTENT")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_posted_todo_is_claimable_by_event_loop(self):
        app, factory = await _create_test_app()

        from general_ludd.event_loop.loop import EventLoop

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "Claimable", "queue": "core", "priority": "high", "work_type": "code"},
            )
            assert resp.status_code == 201

        loop = EventLoop(session=factory, daemon_state={})
        async with factory() as session:
            repo = TodoRepository(session)
            claimed = await repo.claim_runnable()
            assert len(claimed) == 1
            assert claimed[0].title == "Claimable"

    @pytest.mark.asyncio
    async def test_status_reads_from_db(self):
        app, factory = await _create_test_app()
        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data={
                "todo_id": "TODO-S1",
                "title": "S1",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "status": "queued",
            })
            await repo.create(todo_data={
                "todo_id": "TODO-S2",
                "title": "S2",
                "queue": "model",
                "priority": "medium",
                "work_type": "review",
                "status": "active",
            })
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["todos_total"] == 2


class TestTodosPersistenceFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_in_memory_when_no_session_factory(self):
        from general_ludd.daemon import _daemon_state
        _daemon_state["todos"] = [{"todo_id": "INMEM", "title": "mem", "status": "queued", "queue": "core"}]

        app = FastAPI()
        app.state._config_dir = None
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._templates_dir = None
        app.state._playbooks_dir = None
        register(app, _daemon_state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/todos")
            assert resp.status_code == 200
            data = resp.json()
            assert any(t.get("todo_id") == "INMEM" for t in data)
