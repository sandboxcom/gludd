"""Tests for the ``gludd human-todo`` CLI subcommands (cli_human_todos.py).

Uses an in-process fake daemon (FastAPI TestClient) so the CLI's HTTP calls
resolve without network. Validates: list table, done, dismiss requires reason,
watch polls feed, --json output supported.
"""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.cli_human_todos import (
    _cmd_dismiss,
    _cmd_done,
    _cmd_list,
    _cmd_stats,
    _cmd_watch,
)
from general_ludd.db.models import Base
from general_ludd.db.repository import HumanTodoRepository

PSK = "test-psk-secret"


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _client_http(client: TestClient, method: str, url: str, **kwargs):
    from urllib.parse import urlparse

    path = urlparse(url).path
    params = kwargs.get("params")
    json_body = kwargs.get("json_body")
    headers = {"Authorization": f"Bearer {PSK}"}
    if method == "GET":
        resp = client.get(path, params=params, headers=headers)
    elif method == "POST":
        resp = client.post(path, json=json_body, params=params, headers=headers)
    elif method == "PATCH":
        resp = client.patch(path, json=json_body, params=params, headers=headers)
    elif method == "DELETE":
        resp = client.delete(path, params=params, headers=headers)
    else:
        resp = client.request(method, path, json=json_body, params=params, headers=headers)
    if resp.status_code not in kwargs.get("ok_codes", (200, 201)):
        raise SystemExit(1)
    try:
        return resp.json()
    except Exception:
        return None


@pytest.fixture()
def fake_app(monkeypatch):
    monkeypatch.setenv("GLUDD_PSK", PSK)
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
    from general_ludd.daemon import create_daemon_app

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    async def _seed():
        async with factory() as session:
            repo = HumanTodoRepository(session)
            await repo.create(
                agent_id="agent-1",
                title="Need prod key",
                body="OPENAI_API_KEY missing",
                category="input_request",
                priority="urgent",
            )
            await session.commit()

    asyncio.run(_seed())
    try:
        yield app, factory, engine
    finally:
        asyncio.run(engine.dispose())


class TestHumanTodoCli:
    def test_list_prints_table(self, fake_app, monkeypatch):
        app, _factory, _engine = fake_app
        client = TestClient(app)
        import general_ludd.cli_human_todos as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(
            status=None, category=None, priority=None, agent_id=None,
            daemon_url="http://localhost:8000", json=False,
        )
        with redirect_stdout(captured):
            _cmd_list(args)
        out = captured.getvalue()
        assert "Need prod key" in out

    def test_json_output_supported(self, fake_app, monkeypatch):
        app, _factory, _engine = fake_app
        client = TestClient(app)
        import general_ludd.cli_human_todos as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(
            status=None, category=None, priority=None, agent_id=None,
            daemon_url="http://localhost:8000", json=True,
        )
        with redirect_stdout(captured):
            _cmd_list(args)
        rows = json.loads(captured.getvalue())
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["title"] == "Need prod key"

    def test_done_marks_resolved(self, fake_app, monkeypatch):
        app, factory, _engine = fake_app
        client = TestClient(app)
        import general_ludd.cli_human_todos as mod

        async def _get_id():
            async with factory() as session:
                repo = HumanTodoRepository(session)
                rows = await repo.list_open()
                return rows[0].id

        hid = asyncio.run(_get_id())
        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(
            id=hid, resolution="key rotated", resolver="tester",
            daemon_url="http://localhost:8000", json=True,
        )
        with redirect_stdout(captured):
            _cmd_done(args)
        result = json.loads(captured.getvalue())
        assert result["status"] == "done"
        assert result["human_resolution"] == "key rotated"

    def test_dismiss_requires_reason(self, fake_app, monkeypatch):
        args = _Args(id="HTODO-X", reason=None, resolver="t",
                     daemon_url="http://localhost:8000", json=False)
        with pytest.raises(SystemExit) as exc:
            _cmd_dismiss(args)
        assert exc.value.code == 2

    def test_watch_polls_feed(self, fake_app, monkeypatch):
        app, _factory, _engine = fake_app
        client = TestClient(app)
        import general_ludd.cli_human_todos as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))

        call_count = {"n": 0}

        def _fake_sleep(secs):
            call_count["n"] += 1
            if call_count["n"] >= 1:
                raise KeyboardInterrupt()

        monkeypatch.setattr(mod.time, "sleep", _fake_sleep)
        args = _Args(poll=1, daemon_url="http://localhost:8000")
        captured = io.StringIO()
        with redirect_stdout(captured):
            _cmd_watch(args)
        out = captured.getvalue()
        assert "watching" in out
        assert "stopped" in out

    def test_stats(self, fake_app, monkeypatch):
        app, _factory, _engine = fake_app
        client = TestClient(app)
        import general_ludd.cli_human_todos as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(daemon_url="http://localhost:8000", json=False)
        with redirect_stdout(captured):
            _cmd_stats(args)
        out = captured.getvalue()
        assert "total" in out
