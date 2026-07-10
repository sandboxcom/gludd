"""Endpoint tests for routers/remediation.py.

The remediation router (``/admin/remediation/scan|remediate|chronic-blockers|
history|config``) is registered directly by ``daemon.py`` (see
``daemon.py``'s ``create_daemon_app`` router-import block) and — as of this
change — also by ``routers/__init__.register_all`` (the parallel contract
pinned by ``tests/unit/test_router_registration.py``). Until now it had zero
direct endpoint tests: the router logic was only exercised indirectly via
``test_blocker_detector.py`` / ``test_remediation_dispatcher.py`` (which test
the engine, not the HTTP surface).

Follows the router test convention (TestClient over a bare FastAPI app with
the router registered and app.state primed), mirroring
``test_observe_router.py`` for the DB-less parts and
``test_remediation_dispatcher.py`` / ``test_blocker_detector.py`` for the
real-SQLite session-factory setup.

Coverage:
  - GET  /admin/remediation/config            -> defaults, no DB required
  - GET  /admin/remediation/scan               -> finds a chronic re-queue
  - POST /admin/remediation/remediate          -> dispatches + persists audit row
  - GET  /admin/remediation/history            -> surfaces the persisted action
  - GET  /admin/remediation/chronic-blockers   -> 200 (empty) with no incidents
  - Empty-state degradation: no ``app.state._session_factory`` -> 503, not 500
  - Auth posture: the router registers no public bypass; PSK-style middleware
    gates every path exactly like ``test_observe_auth_posture.py`` proves for
    ``routers/observe.py``. Real PSK enforcement is the daemon middleware's
    job (see ``routers/remediation.py`` module docstring) — this test proves
    the router does not accidentally short-circuit that gate.
"""

from __future__ import annotations

import hmac
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel
from general_ludd.remediation.blocker_detector import RemediationConfig
from general_ludd.routers.remediation import register
from general_ludd.schemas.todo import TodoStatus


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_chronic_todo(
    session: AsyncSession,
    *,
    todo_id: str = "TODO-CHRONIC-1",
    run_count: int = 5,
) -> TodoModel:
    """A live QUEUED todo whose run_count crosses the chronic threshold.

    ``BlockerDetector._scan_chronic_requeues`` fires on run_count alone (no
    age check), so this is the simplest deterministic finding to seed for
    endpoint tests — no clock manipulation required.
    """
    todo = TodoModel(
        todo_id=todo_id,
        title="chronically re-queued task",
        status=TodoStatus.QUEUED.value,
        work_type="code",
        queue="core",
        project_id=None,
        run_count=run_count,
    )
    session.add(todo)
    await session.flush()
    return todo


@pytest.fixture
def app_with_db(async_engine) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    register(app, {})
    return app


@pytest.fixture
def client_with_db(app_with_db: FastAPI) -> TestClient:
    return TestClient(app_with_db)


class TestConfigEndpoint:
    def test_config_returns_defaults_with_no_db(self) -> None:
        """GET /admin/remediation/config needs no DB; must work on a bare app."""
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/remediation/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["human_input_block_hours"] == 24
        assert data["permission_escalation_block_hours"] == 4
        assert data["max_requeues_before_chronic"] == 3

    def test_config_reflects_injected_daemon_state(self) -> None:
        cfg = RemediationConfig(human_input_block_hours=1, max_requeues_before_chronic=9)
        app = FastAPI()
        register(app, {"remediation_config": cfg})
        client = TestClient(app)
        resp = client.get("/admin/remediation/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["human_input_block_hours"] == 1
        assert data["max_requeues_before_chronic"] == 9


class TestScanAndRemediate:
    async def test_scan_finds_chronic_requeue(
        self, app_with_db: FastAPI, client_with_db: TestClient
    ) -> None:
        factory = app_with_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session)
            await session.commit()

        resp = client_with_db.get("/admin/remediation/scan")
        assert resp.status_code == 200
        data = resp.json()
        ids = [b["todo_id"] for b in data["blocked_tasks"]]
        assert "TODO-CHRONIC-1" in ids
        finding = next(b for b in data["blocked_tasks"] if b["todo_id"] == "TODO-CHRONIC-1")
        assert finding["blocker_kind"] == "resource_contention"
        assert finding["suggested_remediation"] == "dispatch_agent"

    async def test_scan_project_filter_excludes_other_projects(
        self, app_with_db: FastAPI, client_with_db: TestClient
    ) -> None:
        factory = app_with_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-OTHER-PROJECT")
            await session.commit()

        resp = client_with_db.get(
            "/admin/remediation/scan", params={"project_id": "some-other-project"}
        )
        assert resp.status_code == 200
        ids = [b["todo_id"] for b in resp.json()["blocked_tasks"]]
        assert "TODO-OTHER-PROJECT" not in ids

    async def test_remediate_dispatches_and_persists_audit_row(
        self, app_with_db: FastAPI, client_with_db: TestClient
    ) -> None:
        factory = app_with_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-CHRONIC-2")
            await session.commit()

        resp = client_with_db.post("/admin/remediation/remediate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scanned"] >= 1
        action = next(
            a for a in data["actions"] if a["blocked_todo_id"] == "TODO-CHRONIC-2"
        )
        assert action["kind"] == "dispatch_agent"
        assert action["ok"] is True

        # The dispatcher must have created a fresh QUEUED todo.
        async with factory() as session:
            result = await session.execute(
                select(TodoModel).where(TodoModel.parent_todo_id == "TODO-CHRONIC-2")
            )
            new_todos = result.scalars().all()
            assert len(new_todos) == 1
            assert new_todos[0].status == TodoStatus.QUEUED.value

        # And the audit trail must be queryable via /history.
        hist = client_with_db.get("/admin/remediation/history")
        assert hist.status_code == 200
        hist_ids = [a["blocked_todo_id"] for a in hist.json()["actions"]]
        assert "TODO-CHRONIC-2" in hist_ids


class TestChronicBlockers:
    def test_chronic_blockers_empty_with_no_incidents(
        self, client_with_db: TestClient
    ) -> None:
        resp = client_with_db.get("/admin/remediation/chronic-blockers")
        assert resp.status_code == 200


class TestEmptyStateDegradation:
    """No ``app.state._session_factory`` -> 503, never a bare 500."""

    def test_scan_without_session_factory_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/remediation/scan")
        assert resp.status_code == 503

    def test_remediate_without_session_factory_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/admin/remediation/remediate")
        assert resp.status_code == 503

    def test_history_without_session_factory_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/remediation/history")
        assert resp.status_code == 503

    def test_chronic_blockers_without_session_factory_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/remediation/chronic-blockers")
        assert resp.status_code == 503

    def test_register_on_bare_app_and_empty_state_does_not_crash(self) -> None:
        """Mirrors the generic contract in test_router_registration.py:
        ``register(app, {})`` must add >=1 route without raising."""
        app = FastAPI()
        before = len(app.routes)
        register(app, {})
        assert len(app.routes) > before


_PSK = "unit-test-psk-remediation"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Deliberately empty for remediation paths — mirrors the daemon not listing
# any /admin/remediation/* path in _PUBLIC_PATHS.
_PUBLIC_PATHS: set[str] = {"/healthz"}


def _app_with_psk_gate(async_engine) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    register(app, {})

    def _is_public(method: str, path: str) -> bool:
        if method.upper() not in _SAFE_METHODS:
            return False
        return path in _PUBLIC_PATHS

    @app.middleware("http")
    async def _auth(request, call_next):
        if not _is_public(request.method, request.url.path):
            auth = request.headers.get("Authorization", "")
            token = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not token or not hmac.compare_digest(token, _PSK):
                return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    return app


class TestRemediationIsPskGated:
    """Mirrors test_observe_auth_posture.py: the router registers no public
    bypass, so a PSK-style gate wrapped around it must refuse unauthenticated
    calls and allow correctly-authenticated ones."""

    _CASES: ClassVar[list[tuple[str, str, None]]] = [
        ("GET", "/admin/remediation/scan", None),
        ("POST", "/admin/remediation/remediate", None),
        ("GET", "/admin/remediation/chronic-blockers", None),
        ("GET", "/admin/remediation/history", None),
        ("GET", "/admin/remediation/config", None),
    ]

    @pytest.mark.parametrize("method,path,body", _CASES)
    async def test_unauthenticated_is_refused(
        self, async_engine, method: str, path: str, body
    ) -> None:
        client = TestClient(_app_with_psk_gate(async_engine))
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _CASES)
    async def test_with_psk_reaches_the_handler(
        self, async_engine, method: str, path: str, body
    ) -> None:
        client = TestClient(_app_with_psk_gate(async_engine))
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"}
        )
        # A correctly-authenticated request must reach the handler (never a
        # 401); the handler itself may still 200 (empty results) since the
        # seeded DB has no rows.
        assert resp.status_code != 401
        assert resp.status_code == 200
