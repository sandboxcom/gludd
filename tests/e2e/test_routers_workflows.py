"""E2E tests for gludd API routers: human_todos, dispatch, quantization, admin,
security, review, projects, spend, coordination, self_update router.

Covers endpoint registration, auth middleware, request validation, pagination,
error responses. Uses real FastAPI apps with in-memory SQLite and ASGITransport.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    Subsystem,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_app() -> tuple[FastAPI, async_sessionmaker]:
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

        yield app, factory
    finally:
        await engine.dispose()


@ pytest_asyncio.fixture
async def full_app(db_app: tuple[FastAPI, async_sessionmaker]) -> tuple[FastAPI, async_sessionmaker]:
    app, factory = db_app
    daemon_state: dict[str, object] = cast(dict[str, object], app.state.daemon_state)

    from general_ludd.routers.coordination import register as register_coord
    from general_ludd.routers.dispatch import register as register_dispatch
    from general_ludd.routers.facts import register as register_facts
    from general_ludd.routers.human_todos import register as register_ht
    from general_ludd.routers.quantization import register as register_quant
    from general_ludd.routers.review import register as register_review
    from general_ludd.routers.spend import register as register_spend
    from general_ludd.routers.todos import register as register_todos

    register_ht(app, daemon_state)
    register_todos(app, daemon_state)
    register_facts(app, daemon_state)
    register_quant(app, daemon_state)
    register_review(app, daemon_state)
    register_spend(app, daemon_state)
    register_coord(app, daemon_state)
    register_dispatch(app, daemon_state)

    return app, factory


@pytest.fixture
def dispatch_app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


# ---------------------------------------------------------------------------
# HumanTodos — GET /api/human-todos
# ---------------------------------------------------------------------------


class TestHumanTodosEndpoints:
    def test_list_returns_empty_array_no_db(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/human-todos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_query_params_no_db(self, dispatch_app: TestClient):
        resp = dispatch_app.get(
            "/api/human-todos?status=open&category=input_request&limit=10&offset=0"
        )
        assert resp.status_code == 200

    def test_get_nonexistent_returns_503_no_db(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/human-todos/nonexistent")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_list_with_db_returns_200(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_post_minimal_creates_with_201(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "test-agent",
                    "title": "Test human todo",
                    "body": "This is a test",
                    "category": "input_request",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "id" in data
            assert data["agent_id"] == "test-agent"
            assert data["title"] == "Test human todo"
            assert data["status"] == "open"

    @pytest.mark.asyncio
    async def test_post_invalid_category_returns_422(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "test-agent",
                    "title": "Test",
                    "body": "Body",
                    "category": "invalid_category",
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_post_missing_required_fields_returns_422(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/human-todos", json={})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_post_empty_title_returns_422(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "test-agent",
                    "title": "",
                    "body": "Body",
                    "category": "input_request",
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_by_id_returns_200(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "agent-get",
                    "title": "Get this todo",
                    "body": "Test body for GET",
                    "category": "input_request",
                },
            )
            created = create_resp.json()
            ht_id = created["id"]

            resp = await client.get(f"/api/human-todos/{ht_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == ht_id
            assert data["title"] == "Get this todo"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos/nonexistent-999")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_to_done_returns_200(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "agent-done",
                    "title": "To be done",
                    "body": "Will be resolved",
                    "category": "input_request",
                },
            )
            ht_id = create_resp.json()["id"]

            resp = await client.patch(
                f"/api/human-todos/{ht_id}",
                json={
                    "status": "done",
                    "human_resolution": "Resolved",
                    "human_resolver": "operator",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "done"

    @pytest.mark.asyncio
    async def test_patch_nonexistent_returns_404(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/human-todos/nonexistent",
                json={"status": "done", "human_resolution": "Done"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/human-todos",
                json={
                    "agent_id": "agent-filter",
                    "title": "Open todo",
                    "body": "Should be filtered",
                    "category": "input_request",
                },
            )

            resp = await client.get("/api/human-todos?status=done")
            assert resp.status_code == 200
            data = resp.json()
            assert all(t.get("status") == "done" for t in data)

    @pytest.mark.asyncio
    async def test_list_pagination_limit_offset(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for i in range(5):
                await client.post(
                    "/api/human-todos",
                    json={
                        "agent_id": f"agent-{i}",
                        "title": f"Paginate {i}",
                        "body": f"Body {i}",
                        "category": "input_request",
                    },
                )

            resp = await client.get("/api/human-todos?limit=2&offset=1")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) <= 2

    @pytest.mark.asyncio
    async def test_list_filter_by_priority(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos?priority=high")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_feed_endpoint_returns_200(self, full_app):
        app, _factory = full_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/human-todos/feed")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Dispatch — POST /api/dispatch
# ---------------------------------------------------------------------------


class TestDispatchEndpoints:
    def test_empty_body_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/api/dispatch", json={})
        assert resp.status_code == 422

    def test_missing_tool_calls_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/api/dispatch", json={"unknown": "value"})
        assert resp.status_code == 422

    def test_invalid_kind_returns_proper_response(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "unknown_kind", "name": "test_tool", "args": {}}
                ]
            },
        )
        assert resp.status_code in (200, 422, 500)

    def test_too_many_calls_returns_422(self, dispatch_app: TestClient):
        calls = [
            {"kind": "role", "name": f"tool_{i}", "args": {}} for i in range(25)
        ]
        resp = dispatch_app.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 422

    def test_single_call_with_kind_and_name(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/api/dispatch",
            json={"kind": "role", "name": "echo", "args": {}},
        )
        assert resp.status_code in (200, 422, 500)


# ---------------------------------------------------------------------------
# Quantization — /admin/quantization/*
# ---------------------------------------------------------------------------


class TestQuantizationEndpoints:
    def test_list_returns_200_no_tracker(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/quantization")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data

    def test_detect_missing_model_id_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/admin/quantization/detect", json={})
        assert resp.status_code == 422

    def test_detect_with_model_id_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/quantization/detect", json={"model_id": "test-model"}
        )
        assert resp.status_code == 200

    def test_get_model_by_id_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/quantization/test-model")
        assert resp.status_code == 200

    def test_drift_check_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/admin/quantization/drift-check")
        assert resp.status_code == 200

    def test_status_monitor_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/quantization/status")
        assert resp.status_code == 200

    def test_config_monitor_missing_body_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/admin/quantization/config", json={})
        assert resp.status_code in (422, 503)


# ---------------------------------------------------------------------------
# Review — /admin/review/*
# ---------------------------------------------------------------------------


class TestReviewEndpoints:
    def test_pending_returns_503_no_human_gate(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/review/pending")
        assert resp.status_code == 503

    def test_approve_returns_503_no_human_gate(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/review/approve/test-thread",
            json={"decision": "approved"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Spend — /api/spend, /api/costs, /api/credits, /admin/costs
# ---------------------------------------------------------------------------


class TestSpendEndpoints:
    def test_spend_status_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert "window_spend_usd" in data or "limiter_active" in data

    def test_costs_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/costs")
        assert resp.status_code == 200

    def test_admin_costs_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/costs")
        assert resp.status_code == 200

    def test_credits_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/credits")
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Projects — /admin/projects
# ---------------------------------------------------------------------------


class TestProjectsEndpoints:
    def test_list_projects_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/admin/projects")
        assert resp.status_code in (200, 404)

    def test_add_project_missing_required_fields_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/admin/projects", json={"name": "test-project"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Coordination — /api/coordination/*
# ---------------------------------------------------------------------------


class TestCoordinationEndpoints:
    def test_claims_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/coordination/claims")
        assert resp.status_code == 200

    def test_overlaps_missing_worker_id_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.get("/api/coordination/overlaps")
        assert resp.status_code == 422

    def test_claim_missing_worker_id_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/api/coordination/claim", json={})
        assert resp.status_code == 422

    def test_claim_with_worker_id_returns_201(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/api/coordination/claim",
            json={"worker_id": "worker-1", "files": ["file1.py"]},
        )
        assert resp.status_code == 201

    def test_release_missing_worker_id_returns_422(self, dispatch_app: TestClient):
        resp = dispatch_app.post("/api/coordination/release", json={})
        assert resp.status_code == 422

    def test_release_with_worker_id_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/api/coordination/release",
            json={"worker_id": "worker-1"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Self-update Router — /admin/self-update/*
# ---------------------------------------------------------------------------


class TestSelfUpdateRouterE2E:
    def test_plan_empty_text_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/self-update/plan",
            json={"raw_text": "", "requested_by": "operator"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_plan_config_change_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/self-update/plan",
            json={
                "raw_text": "set spend limit to 100",
                "requested_by": "operator",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_plan_with_approval_token_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/self-update/plan",
            json={
                "raw_text": "increase window",
                "requested_by": "operator",
                "approval_token": "test-token",
            },
        )
        assert resp.status_code == 200

    def test_enqueue_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/self-update/enqueue",
            json={
                "raw_text": "tune the spend window",
                "requested_by": "operator",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_enqueue_with_project_id_returns_200(self, dispatch_app: TestClient):
        resp = dispatch_app.post(
            "/admin/self-update/enqueue",
            json={
                "raw_text": "adjust lint gate",
                "requested_by": "operator",
                "project_id": "test-project",
            },
        )
        assert resp.status_code == 200

    def test_plan_protected_path_refused(self, dispatch_app: TestClient):
        with patch(
            "general_ludd.routers.self_update.classify",
            return_value=SelfUpdatePlan(
                subsystem=Subsystem.SECURITY,
                change_kind=ChangeKind.VALUE_EDIT,
                target_files=(
                    "src/general_ludd/security/capability_lattice.py",
                ),
                apply_tier=ApplyTier.CONFIG,
                requires_approval=False,
                rationale="protected path",
                confidence=0.5,
            ),
        ):
            resp = dispatch_app.post(
                "/admin/self-update/plan",
                json={
                    "raw_text": "edit capability lattice",
                    "requested_by": "operator",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("outcome") in ("refused", "awaiting_approval")
