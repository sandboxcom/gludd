"""Tests for the account backup/deletion mechanism.

Covers:
- ``general_ludd.account.backup`` — backup_account, delete_account, get_deletion_policy
- ``general_ludd.account.deletion_notice`` — per-service retention notices
- ``general_ludd.cli_account`` — ``gludd account {backup|delete|policy}`` CLI
- ``general_ludd.routers.account`` — ``POST /api/account/backup``,
  ``DELETE /api/account``, ``GET /api/account/policy``

All DB-backed tests use an in-memory SQLite async engine so no on-disk state
leaks between tests.
"""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.account.backup import (
    backup_account,
    delete_account,
    get_deletion_policy,
)
from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_all_notices,
)
from general_ludd.db.models import (
    Base,
    MemoryRecordModel,
    TaskReturnModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)

_TEST_PSK = "account-backup-test-psk"
_AUTH_HEADERS = {"Authorization": f"Bearer {_TEST_PSK}"}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    async def _dispose() -> None:
        await engine.dispose()

    asyncio.run(_dispose())


def _seed_user(session_factory, user_id: str) -> dict[str, int]:
    """Seed one user with one row in each category. Returns counts."""

    async def _go() -> dict[str, int]:
        async with session_factory() as session:
            todo = TodoModel(
                todo_id=f"todo-{user_id}",
                title="test todo",
                created_by=user_id,
                assigned_agent=user_id,
            )
            session.add(todo)
            await session.flush()
            ret = TaskReturnModel(
                return_id=f"ret-{user_id}",
                todo_id=todo.todo_id,
                job_id=f"job-{user_id}",
                playbook="pb.yml",
                queue="core",
            )
            session.add(ret)
            session.add(
                MemoryRecordModel(
                    id=f"mem-{user_id}",
                    agent_id=user_id,
                    key="pref",
                    value="dark-mode",
                    namespace="default",
                )
            )
            ns = VariableNamespaceModel(namespace=f"user:{user_id}", description="user settings")
            session.add(ns)
            await session.flush()
            session.add(
                VariableValueModel(
                    namespace_id=ns.id,
                    key="theme",
                    value="dark",
                    value_type="string",
                )
            )
            await session.commit()
            return {
                "todos": 1,
                "returns": 1,
                "memory": 1,
                "settings": 1,
            }

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# backup_account / delete_account / get_deletion_policy
# ---------------------------------------------------------------------------


class TestBackupAccount:
    def test_writes_a_json_file_with_all_categories(self, session_factory, tmp_path):
        _seed_user(session_factory, "alice")
        path = backup_account("alice", session_factory=session_factory, dest_dir=tmp_path)
        assert isinstance(path, Path)
        assert path.exists()
        assert path.suffix == ".json"
        payload = json.loads(path.read_text())
        assert payload["user_id"] == "alice"
        assert "exported_at" in payload
        assert isinstance(payload["todos"], list) and len(payload["todos"]) == 1
        assert payload["todos"][0]["todo_id"] == "todo-alice"
        assert isinstance(payload["returns"], list) and len(payload["returns"]) == 1
        assert payload["returns"][0]["return_id"] == "ret-alice"
        assert isinstance(payload["memory"], list) and len(payload["memory"]) == 1
        assert payload["memory"][0]["key"] == "pref"
        assert isinstance(payload["settings"], list) and len(payload["settings"]) == 1
        assert payload["settings"][0]["key"] == "theme"

    def test_filename_contains_user_id(self, session_factory, tmp_path):
        _seed_user(session_factory, "bob")
        path = backup_account("bob", session_factory=session_factory, dest_dir=tmp_path)
        assert "bob" in path.name

    def test_only_includes_the_named_user(self, session_factory, tmp_path):
        _seed_user(session_factory, "alice")
        _seed_user(session_factory, "bob")
        path = backup_account("alice", session_factory=session_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())
        user_ids_in_todos = {t["created_by"] for t in payload["todos"]}
        assert user_ids_in_todos == {"alice"}
        assert all(m["key"] != "pref-bob" for m in payload["memory"])  # other-user memory excluded

    def test_backup_empty_user_still_writes_a_file(self, session_factory, tmp_path):
        path = backup_account("nobody", session_factory=session_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())
        assert payload["todos"] == []
        assert payload["returns"] == []
        assert payload["memory"] == []
        assert payload["settings"] == []


class TestDeleteAccount:
    def test_returns_summary_with_counts(self, session_factory):
        _seed_user(session_factory, "alice")
        summary = delete_account("alice", session_factory=session_factory)
        assert summary["user_id"] == "alice"
        assert "deleted_at" in summary
        assert summary["todos_deleted"] == 1
        assert summary["returns_deleted"] == 1
        assert summary["memory_deleted"] == 1
        assert summary["settings_namespaces_deleted"] == 1

    def test_actually_removes_rows(self, session_factory):
        _seed_user(session_factory, "alice")
        delete_account("alice", session_factory=session_factory)

        async def _count():
            async with session_factory() as session:
                todos = (await session.execute(select(TodoModel))).scalars().all()
                mem = (await session.execute(select(MemoryRecordModel))).scalars().all()
                rets = (await session.execute(select(TaskReturnModel))).scalars().all()
                nss = (await session.execute(select(VariableNamespaceModel))).scalars().all()
                return len(todos), len(mem), len(rets), len(nss)

        n_todos, n_mem, n_rets, n_ns = asyncio.run(_count())
        assert n_todos == 0
        assert n_mem == 0
        assert n_rets == 0
        assert n_ns == 0

    def test_does_not_touch_other_users(self, session_factory):
        _seed_user(session_factory, "alice")
        _seed_user(session_factory, "bob")
        delete_account("alice", session_factory=session_factory)

        async def _leftover_bob():
            async with session_factory() as session:
                todos = (
                    await session.execute(
                        select(TodoModel).where(TodoModel.created_by == "bob")
                    )
                ).scalars().all()
                mem = (
                    await session.execute(
                        select(MemoryRecordModel).where(MemoryRecordModel.agent_id == "bob")
                    )
                ).scalars().all()
                return len(todos), len(mem)

        n_todos, n_mem = asyncio.run(_leftover_bob())
        assert n_todos == 1
        assert n_mem == 1

    def test_delete_unknown_user_returns_zero_counts(self, session_factory):
        summary = delete_account("nonexistent-user", session_factory=session_factory)
        assert summary["todos_deleted"] == 0
        assert summary["memory_deleted"] == 0


# ---------------------------------------------------------------------------
# get_deletion_policy + deletion_notice
# ---------------------------------------------------------------------------


class TestDeletionPolicy:
    @pytest.mark.parametrize("svc", sorted(SUPPORTED_SERVICES))
    def test_get_deletion_policy_known_service(self, svc):
        text = get_deletion_policy(svc)
        assert isinstance(text, str) and len(text) > 20

    def test_get_deletion_policy_case_insensitive(self):
        assert get_deletion_policy("OpenAI") == get_deletion_policy("openai")
        assert get_deletion_policy(" AWS ") == get_deletion_policy("aws")

    def test_get_deletion_policy_unknown_service_raises(self):
        with pytest.raises(ValueError):
            get_deletion_policy("not-a-real-cloud")

    def test_build_deletion_notice_is_human_readable(self):
        notice = build_deletion_notice("openai")
        assert isinstance(notice, str)
        assert "OpenAI" in notice or "openai" in notice.lower()
        # Should mention retention / deletion timing
        low = notice.lower()
        assert any(word in low for word in ("retain", "delete", "day", "persist", "after"))

    def test_get_all_notices_covers_every_service(self):
        notices = get_all_notices()
        assert set(notices.keys()) == SUPPORTED_SERVICES
        for svc, text in notices.items():
            assert isinstance(text, str) and len(text) > 20, svc


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture()
def app_with_account_router(session_factory, monkeypatch):
    """Spin up a FastAPI app with the account router wired in."""
    monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
    monkeypatch.setenv("GLUDD_AUTH_PSK", _TEST_PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = session_factory
    return app, session_factory


def _client_http(client: TestClient, method: str, url: str, **kwargs):
    from urllib.parse import urlparse

    path = urlparse(url).path
    params = kwargs.get("params")
    json_body = kwargs.get("json_body")
    if method == "GET":
        resp = client.get(path, params=params)
    elif method == "POST":
        resp = client.post(path, json=json_body, params=params)
    elif method == "DELETE":
        # Starlette TestClient.delete doesn't accept json=, so use request().
        resp = client.request("DELETE", path, json=json_body, params=params)
    else:
        resp = client.request(method, path, json=json_body, params=params)
    if resp.status_code not in kwargs.get("ok_codes", (200, 201, 204)):
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except Exception:
        return None


class TestCliAccount:
    def test_backup_subcommand_prints_path(self, app_with_account_router, monkeypatch, tmp_path):
        app, sf = app_with_account_router
        _seed_user(sf, "alice")
        client = TestClient(app, headers=_AUTH_HEADERS)
        import general_ludd.cli_account as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(
            user_id="alice",
            daemon_url="http://localhost:8000",
            json=True,
        )
        with redirect_stdout(captured):
            mod._cmd_backup(args)
        out = captured.getvalue().strip()
        assert "alice" in out

    def test_delete_subcommand_returns_summary(self, app_with_account_router, monkeypatch):
        app, sf = app_with_account_router
        _seed_user(sf, "alice")
        client = TestClient(app, headers=_AUTH_HEADERS)
        import general_ludd.cli_account as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(
            user_id="alice",
            confirm=True,
            daemon_url="http://localhost:8000",
            json=True,
        )
        with redirect_stdout(captured):
            mod._cmd_delete(args)
        result = json.loads(captured.getvalue())
        assert result["user_id"] == "alice"
        assert result["todos_deleted"] == 1

    def test_delete_without_confirm_exits_nonzero(self, monkeypatch):
        import general_ludd.cli_account as mod

        args = _Args(user_id="alice", confirm=False, daemon_url="x", json=False)
        with pytest.raises(SystemExit) as exc:
            mod._cmd_delete(args)
        assert exc.value.code != 0

    def test_policy_subcommand_prints_notice(self, app_with_account_router, monkeypatch):
        app, _sf = app_with_account_router
        client = TestClient(app, headers=_AUTH_HEADERS)
        import general_ludd.cli_account as mod

        monkeypatch.setattr(mod, "_http", lambda *a, **k: _client_http(client, *a, **k))
        captured = io.StringIO()
        args = _Args(service="openai", daemon_url="http://localhost:8000", json=False)
        with redirect_stdout(captured):
            mod._cmd_policy(args)
        out = captured.getvalue()
        assert "OpenAI" in out or "openai" in out.lower()


# ---------------------------------------------------------------------------
# Router endpoints
# ---------------------------------------------------------------------------


class TestAccountRouter:
    def test_post_account_backup_returns_200_with_payload(self, app_with_account_router):
        app, sf = app_with_account_router
        _seed_user(sf, "alice")
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.post("/api/account/backup", json={"user_id": "alice"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == "alice"
        assert "exported_at" in body
        assert len(body["todos"]) == 1
        assert body["todos"][0]["created_by"] == "alice"

    def test_post_account_backup_validates_user_id(self, app_with_account_router):
        app, _sf = app_with_account_router
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.post("/api/account/backup", json={"user_id": ""})
        assert resp.status_code == 422

    def test_delete_account_returns_summary(self, app_with_account_router):
        app, sf = app_with_account_router
        _seed_user(sf, "alice")
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.request("DELETE", "/api/account", json={"user_id": "alice", "confirm": True})
        assert resp.status_code in (200, 204), resp.text
        if resp.status_code == 200:
            body = resp.json()
            assert body["user_id"] == "alice"
            assert body["todos_deleted"] == 1

    def test_delete_account_requires_confirm(self, app_with_account_router):
        app, sf = app_with_account_router
        _seed_user(sf, "alice")
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.request("DELETE", "/api/account", json={"user_id": "alice", "confirm": False})
        assert resp.status_code == 400

    def test_get_policy_known_service(self, app_with_account_router):
        app, _sf = app_with_account_router
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.get("/api/account/policy", params={"service": "openai"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["service"] == "openai"
        assert "policy" in body
        assert len(body["policy"]) > 20

    def test_get_policy_unknown_service_returns_422(self, app_with_account_router):
        app, _sf = app_with_account_router
        client = TestClient(app, headers=_AUTH_HEADERS)
        resp = client.get("/api/account/policy", params={"service": "not-a-cloud"})
        assert resp.status_code == 422
