"""Integration tests for the message-queue API (routers/messages.py) and the
facts aggregation API (routers/facts.py), exercised through the REAL daemon app
via ASGITransport with PSK auth enabled.

Covers PART 1 + PART 2 of the facts/MQ backbone:
  - send -> inbox -> ack round-trip
  - broadcast visible to all recipients
  - unknown ack -> 404
  - missing PSK -> 401
  - /api/facts reflects seeded todos / returns / messages
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import (
    AgentMessageRepository,
    TaskReturnRepository,
    TodoRepository,
)

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    """Build the real daemon app with PSK auth enabled, WITHOUT leaking the env.

    GLUDD_PSK is read inside create_daemon_app and captured in the auth
    middleware closure, so it must be set before app creation. We use
    monkeypatch (auto-reverted at test teardown) rather than os.environ so the
    PSK does not leak into the ~90 other daemon-app tests sharing the process.
    """
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    # Inject our test DB factory (bypasses the lifespan which we don't run here).
    app.state._session_factory = factory
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestMessagesApi:
    @pytest.mark.asyncio
    async def test_send_inbox_ack_roundtrip(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/messages",
                json={"sender": "planner", "recipient": "coder", "topic": "t", "body": "hi"},
                headers=AUTH,
            )
            assert resp.status_code == 201, resp.text
            msg_id = resp.json()["id"]

            inbox = await client.get("/api/messages", params={"recipient": "coder"}, headers=AUTH)
            assert inbox.status_code == 200
            assert inbox.json()["count"] == 1
            assert inbox.json()["messages"][0]["id"] == msg_id

            ack = await client.post(f"/api/messages/{msg_id}/ack", headers=AUTH)
            assert ack.status_code == 200
            assert ack.json()["acked"] is True

            inbox2 = await client.get("/api/messages", params={"recipient": "coder"}, headers=AUTH)
            assert inbox2.json()["count"] == 0
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_broadcast_visible_to_all(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/api/messages",
                json={"sender": "boss", "recipient": "broadcast", "topic": "all"},
                headers=AUTH,
            )
            for who in ("coder", "reviewer"):
                inbox = await client.get("/api/messages", params={"recipient": who}, headers=AUTH)
                assert inbox.json()["count"] == 1
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ack_unknown_404(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post("/api/messages/MSG-NOPE/ack", headers=AUTH)
            assert resp.status_code == 404
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_missing_psk_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/messages",
                json={"sender": "a", "recipient": "b", "topic": "t"},
            )
            assert resp.status_code == 401
            inbox = await client.get("/api/messages", params={"recipient": "b"})
            assert inbox.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()


class TestFactsApi:
    @pytest.mark.asyncio
    async def test_facts_reflects_seeded_data(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            async with factory() as session:
                todo_repo = TodoRepository(session)
                await todo_repo.create(
                    {"title": "t1", "queue": "core", "work_type": "code", "status": "queued"}
                )
                await todo_repo.create(
                    {"title": "t2", "queue": "core", "work_type": "test", "status": "active"}
                )
                tr_repo = TaskReturnRepository(session)
                await tr_repo.create(
                    {
                        "return_id": "R1",
                        "job_id": "J1",
                        "playbook": "noop.yml",
                        "queue": "core",
                        "work_type": "code",
                        "status": "created",
                        "exit_code": 0,
                    }
                )
                await tr_repo.create(
                    {
                        "return_id": "R2",
                        "job_id": "J2",
                        "playbook": "noop.yml",
                        "queue": "core",
                        "work_type": "code",
                        "status": "created",
                        "exit_code": 1,
                    }
                )
                msg_repo = AgentMessageRepository(session)
                await msg_repo.send({"sender": "a", "recipient": "coder", "topic": "x"})
                await session.commit()

            resp = await client.get("/api/facts", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["todos"]["total"] == 2
            assert data["todos"]["by_status"].get("queued") == 1
            assert data["todos"]["backlog_size"] == 1

            assert data["work"]["total"] == 2
            assert data["history"]["total_returns"] == 2
            assert data["history"]["success_count"] == 1
            assert data["history"]["failure_count"] == 1
            assert data["history"]["success_rate"] == 0.5

            assert data["messages"]["unread_by_recipient"].get("coder") == 1
            assert data["messages"]["total_unread"] == 1

            assert "routing" in data["models"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_facts_requires_psk(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/facts")
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()
