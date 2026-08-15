"""Integration test: registering the OpenBao break-glass backup timer via the
daemon scheduling API.

This exercises the operator contract for section 3 of OPENBAO_BREAK_GLASS_BACKUP:

* An operator (or `make init` on a host with OpenBao configured) registers a
  recurring daily-at-03:00 schedule entry via `POST /api/todos/scheduled`
  (PSK-gated). The entry points at the `openbao_break_glass` work type.
* The entry is persisted (via the TodoRepository factory) so it survives a
  daemon restart.
* `GET /api/todos/scheduled` lists it back.

The actual scheduled run (which would invoke the role + module against a live
OpenBao) is intentionally NOT exercised here — that is covered by the molecule
scenario `molecule/playbooks/openbao_break_glass_backup/`. This test stays
API-only.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base

PSK = "test-psk-openbao-backup"
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


class TestOpenBaoBackupTimerRegistration:
    @pytest.mark.asyncio
    async def test_register_persisted_and_listable(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            # Register the timer.
            body = {
                "title": "OpenBao break-glass backup",
                "description": "Daily encrypted snapshot of the OpenBao raft store.",
                "queue": "core",
                "priority": "high",
                "work_type": "openbao_break_glass",
                "cron": "0 3 * * *",  # daily at 03:00 local
                "schedule_timezone": "UTC",
            }
            post = await client.post("/api/todos/scheduled", json=body, headers=AUTH)
            assert post.status_code == 201, post.text
            created = post.json()
            assert created["cron"] == "0 3 * * *"
            assert created["status"] == "scheduled"
            assert created["work_type"] == "openbao_break_glass"

            # List schedules back — entry should appear.
            lst = await client.get("/api/todos/scheduled", headers=AUTH)
            assert lst.status_code == 200, lst.text
            items = lst.json()
            assert any(i.get("cron") == "0 3 * * *" for i in items), items

            # next_run_at should be populated from the cron expression.
            matching = [i for i in items if i.get("cron") == "0 3 * * *"]
            assert matching[0]["next_run_at"] is not None
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_register_requires_psk(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            body = {
                "title": "OpenBao break-glass backup",
                "work_type": "openbao_break_glass",
                "cron": "0 3 * * *",
            }
            # No Authorization header -> 401.
            post = await client.post("/api/todos/scheduled", json=body)
            assert post.status_code in (401, 403), post.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_register_rejects_invalid_cron(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            body = {
                "title": "OpenBao break-glass backup",
                "work_type": "openbao_break_glass",
                "cron": "garbage",  # not 5 fields
            }
            post = await client.post("/api/todos/scheduled", json=body, headers=AUTH)
            assert post.status_code == 422, post.text
        finally:
            await client.aclose()
            await engine.dispose()
