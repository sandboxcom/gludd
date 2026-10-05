"""Integration tests for the HumanTodo HTTP API (routers/human_todos.py).

Exercises the daemon via ASGI transport with PSK auth, mirroring
tests/integration/test_messages_and_facts_api.py. Covers:
  - agent files a human-todo
  - human lists open human-todos (public GET)
  - human marks done / dismissed
  - terminal states reject further patches
  - parent agent todo transitions to blocked_on_human on file
  - done unblocks the parent (-> queued); dismissed cancels it (-> cancelled)
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import HumanTodoRepository, TodoRepository
from general_ludd.schemas.todo import TodoStatus

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestHumanTodosApi:
    @pytest.mark.asyncio
    async def test_agent_can_file_human_todo(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "agent-1",
                    "title": "Need prod key",
                    "body": "OPENAI_API_KEY missing",
                    "category": "input_request",
                    "priority": "urgent",
                },
                headers=AUTH,
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["id"].startswith("HTODO-")
            assert body["status"] == "open"
            assert body["category"] == "input_request"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_human_can_list_open(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            for i in range(3):
                await client.post(
                    "/api/human-todos",
                    json={
                        "agent_id": "a",
                        "title": f"t{i}",
                        "body": "b",
                        "category": "blocker",
                    },
                    headers=AUTH,
                )
            # public GET (no auth) — human reads the queue
            resp = await client.get("/api/human-todos")
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 3
            # filter by status=open
            resp2 = await client.get("/api/human-todos", params={"status": "open"})
            assert len(resp2.json()) == 3
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_human_can_mark_done(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                },
                headers=AUTH,
            )
            hid = created.json()["id"]
            resp = await client.patch(
                f"/api/human-todos/{hid}",
                json={
                    "status": "done",
                    "human_resolver": "shawn",
                    "human_resolution": "key rotated",
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "done"
            assert resp.json()["human_resolution"] == "key rotated"
            assert resp.json()["resolved_at"] is not None
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_human_can_dismiss(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "decision",
                },
                headers=AUTH,
            )
            hid = created.json()["id"]
            resp = await client.patch(
                f"/api/human-todos/{hid}",
                json={
                    "status": "dismissed",
                    "human_resolver": "shawn",
                    "human_resolution": "won't do this",
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "dismissed"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_terminal_states_reject_further_patches(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                },
                headers=AUTH,
            )
            hid = created.json()["id"]
            await client.patch(
                f"/api/human-todos/{hid}",
                json={
                    "status": "done",
                    "human_resolver": "h",
                    "human_resolution": "ok",
                },
                headers=AUTH,
            )
            # second patch on terminal state -> 422
            resp = await client.patch(
                f"/api/human-todos/{hid}",
                json={"status": "in_progress"},
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_parent_agent_todo_transitions_to_blocked_on_human(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            # seed a parent agent todo
            async with factory() as session:
                repo = TodoRepository(session)
                parent = await repo.create(
                    todo_data={
                        "todo_id": "TODO-PARENT1",
                        "title": "parent work",
                        "status": TodoStatus.QUEUED.value,
                    }
                )
                assert parent is not None
                await session.commit()
            # agent files a human-todo blocking on it
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "need permission",
                    "body": "explain",
                    "category": "permission_escalation",
                    "parent_agent_todo_id": "TODO-PARENT1",
                },
                headers=AUTH,
            )
            assert resp.status_code == 201
            # parent should now be blocked_on_human
            async with factory() as session:
                repo = TodoRepository(session)
                p = await repo.get_by_id("TODO-PARENT1")
                assert p is not None
                assert p.status == TodoStatus.BLOCKED_ON_HUMAN.value
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_done_human_todo_unblocks_parent_agent_todo(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            async with factory() as session:
                repo = TodoRepository(session)
                await repo.create(
                    todo_data={
                        "todo_id": "TODO-PARENT2",
                        "title": "parent",
                        "status": TodoStatus.QUEUED.value,
                    }
                )
                await session.commit()
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "need input",
                    "body": "explain",
                    "category": "input_request",
                    "parent_agent_todo_id": "TODO-PARENT2",
                },
                headers=AUTH,
            )
            hid = created.json()["id"]
            await client.patch(
                f"/api/human-todos/{hid}",
                json={
                    "status": "done",
                    "human_resolver": "h",
                    "human_resolution": "here it is",
                },
                headers=AUTH,
            )
            async with factory() as session:
                repo = TodoRepository(session)
                p = await repo.get_by_id("TODO-PARENT2")
                assert p is not None
                assert p.status == TodoStatus.QUEUED.value
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dismissed_human_todo_cancels_parent(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            async with factory() as session:
                repo = TodoRepository(session)
                await repo.create(
                    todo_data={
                        "todo_id": "TODO-PARENT3",
                        "title": "parent",
                        "status": TodoStatus.QUEUED.value,
                    }
                )
                await session.commit()
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "decision needed",
                    "body": "which approach?",
                    "category": "decision",
                    "parent_agent_todo_id": "TODO-PARENT3",
                },
                headers=AUTH,
            )
            hid = created.json()["id"]
            await client.patch(
                f"/api/human-todos/{hid}",
                json={
                    "status": "dismissed",
                    "human_resolver": "h",
                    "human_resolution": "try a different approach",
                },
                headers=AUTH,
            )
            async with factory() as session:
                repo = TodoRepository(session)
                p = await repo.get_by_id("TODO-PARENT3")
                assert p is not None
                assert p.status == TodoStatus.CANCELLED.value
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_invalid_category_rejected(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "nonsense",
                },
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_missing_psk_rejected_on_write(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                },
                # no AUTH header
            )
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_feed_endpoint(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "a",
                    "title": "t",
                    "body": "b",
                    "category": "blocker",
                },
                headers=AUTH,
            )
            resp = await client.get("/api/human-todos/feed", headers=AUTH)
            assert resp.status_code == 200
            assert len(resp.json()) >= 1
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_tolerates_legacy_malformed_tag_json(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            created = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "legacy-agent",
                    "title": "legacy row",
                    "body": "tags were stored by an older writer",
                    "category": "blocker",
                    "tags": ["original"],
                },
                headers=AUTH,
            )
            assert created.status_code == 201
            human_todo_id = created.json()["id"]

            async with factory() as session:
                row = await HumanTodoRepository(session).get(human_todo_id)
                assert row is not None
                row.tags = "{malformed-json"
                await session.commit()

            response = await client.get(
                f"/api/human-todos/{human_todo_id}",
                headers=AUTH,
            )

            assert response.status_code == 200
            assert response.json()["tags"] == []
        finally:
            await client.aclose()
            await engine.dispose()
