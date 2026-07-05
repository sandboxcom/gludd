"""Route-level tests for GET /api/environment and /api/environment/advise.

Covers:
  - 200 OK with all expected top-level sections
  - project_id scoping in the project facet
  - optimization advisor facet delivers recommendations
  - graceful degradation when config/app.state is missing
  - PSK auth required (401 without, 200 with)
  - advise endpoint contract and validation
"""

from __future__ import annotations

import hmac
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.config.model_routing import ModelRoutingConfig
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.routers.environment import register

_PSK = "env-endpoint-test-psk"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _model_gateway() -> ModelGateway:
    profile = ModelProfile(
        model_profile_id="flagship",
        provider="openai",
        model_name="glm-4.6",
        enabled=True,
        quality_class="high",
        latency_class="standard",
        api_metered=True,
        fallback_profiles=["weak"],
    )
    weak = ModelProfile(
        model_profile_id="weak",
        provider="openai",
        model_name="glm-4.5-air",
        enabled=True,
        api_metered=False,
    )
    return ModelGateway(profiles=[profile, weak])


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
    app.state._model_gateway = _model_gateway()
    app.state._startup_config = {
        "model_routing": ModelRoutingConfig(
            default_profile="flagship",
            weak_model_profile="weak",
            role_routing={"reviewer": "flagship"},
            fallback_chain=["flagship", "weak"],
        )
    }
    app.state._budget_guard = None
    app.state._spend_limiter = None
    app.state._session_factory = None
    app.state._mcp_client = None
    app.state._skill_registry = None
    app.state._compute_config = None
    register(app, {})

    def _is_public(method: str, path: str) -> bool:
        if method.upper() not in _SAFE_METHODS:
            return False
        return path in _PUBLIC_PATHS

    @app.middleware("http")
    async def _auth(request: Any, call_next: Any) -> Any:
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


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_PSK}"}


# ---------------------------------------------------------------------------
# GET /api/environment — main brief
# ---------------------------------------------------------------------------


class TestEnvironmentEndpoint:
    """Route-level tests for GET /api/environment."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(_app_with_psk_gate())

    def test_returns_200_with_expected_structure(self, client: TestClient) -> None:
        """GET /api/environment returns 200 with all top-level sections present."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        expected_sections = (
            "models",
            "routing",
            "budget",
            "compute",
            "tools",
            "skills",
            "queues",
            "system",
            "optimization",
            "project",
        )
        for section in expected_sections:
            assert section in body, f"missing section: {section}"

    def test_models_facet_carries_characteristic_fields(self, client: TestClient) -> None:
        """Model roster contains profile_id/provider/model and NEVER credential fields."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200
        roster = resp.json()["models"]
        assert len(roster) >= 1
        flagship = next(m for m in roster if m.get("profile_id") == "flagship")
        assert flagship["provider"] == "openai"
        assert flagship["model"] == "glm-4.6"
        assert flagship["enabled"] is True
        assert flagship["quality_class"] == "high"
        for forbidden in ("credential_alias", "api_base_alias", "api_key", "token", "secret"):
            assert forbidden not in flagship, f"leaked {forbidden}"

    def test_routing_facet_reflects_config(self, client: TestClient) -> None:
        """Routing section mirrors the startup model_routing config."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200
        routing = resp.json()["routing"]
        assert routing["default_profile"] == "flagship"
        assert routing["weak_model_profile"] == "weak"
        assert "roles" in routing
        assert "fallback_chain" in routing

    def test_advisor_facet_returns_recommendations(self, client: TestClient) -> None:
        """Optimization section has hints list + recommended_profile_for dict."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200
        optimization = resp.json()["optimization"]
        assert "hints" in optimization
        assert isinstance(optimization["hints"], list)
        assert "recommended_profile_for" in optimization
        assert isinstance(optimization["recommended_profile_for"], dict)

    def test_system_facet_provides_host_facts(self, client: TestClient) -> None:
        """System section carries cpu_count / python_version / disk_free_mb keys."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200
        system = resp.json()["system"]
        for key in ("cpu_count", "python_version", "load_avg", "disk_free_mb", "mem_available_mb"):
            assert key in system, f"missing system key: {key}"

    def test_project_facet_empty_when_no_session_factory(self, client: TestClient) -> None:
        """When session_factory is None the project facet renders {}."""
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["project"] == {}


# ---------------------------------------------------------------------------
# auth gating
# ---------------------------------------------------------------------------


class TestEnvironmentAuth:
    """PSK auth is required for both /api/environment and /api/environment/advise."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(_app_with_psk_gate())

    def test_environment_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/environment")
        assert resp.status_code == 401

    def test_environment_authenticated_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/environment", headers=_auth_headers())
        assert resp.status_code == 200

    def test_advise_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/environment/advise", params={"work_type": "feature"})
        assert resp.status_code == 401

    def test_advise_authenticated_returns_200(self, client: TestClient) -> None:
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# graceful degradation
# ---------------------------------------------------------------------------


class TestEnvironmentGracefulDegradation:
    """A bare FastAPI app with no wired state degrades gracefully, never 500s."""

    def test_bare_app_environment_returns_200(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/environment")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["models"] == []
        assert body["routing"] == {}
        assert body["tools"] != []  # ANSIBLE_TOOL_MODULES floor

    def test_bare_app_advise_returns_200_with_fallback(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/environment/advise", params={"work_type": "bugfix"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_type"] == "bugfix"
        assert "recommendation" in body
        assert "model_profile" in body["recommendation"]
        assert isinstance(body["est_cost_usd"], float)

    def test_bare_app_advise_unknown_work_type_falls_back(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get(
            "/api/environment/advise", params={"work_type": "chat"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_type"] == "chat"
        assert body["recommendation"]["model_profile"] is not None


# ---------------------------------------------------------------------------
# /api/environment/advise
# ---------------------------------------------------------------------------


class TestAdviseEndpoint:
    """Route-level tests for GET /api/environment/advise."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(_app_with_psk_gate())

    def test_advise_bugfix_returns_contract(self, client: TestClient) -> None:
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "bugfix", "prompt_tokens": 2000},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_type"] == "bugfix"
        for key in (
            "recommendation",
            "route",
            "est_cost_usd",
            "use_workflow",
            "workflow_reason",
            "resource_hints",
        ):
            assert key in body, f"missing advise key: {key}"

    def test_advise_refactor_returns_contract(self, client: TestClient) -> None:
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "refactor"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_type"] == "refactor"
        assert isinstance(body["est_cost_usd"], float)

    def test_advise_rejects_missing_work_type(self, client: TestClient) -> None:
        resp = client.get(
            "/api/environment/advise",
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_advise_unknown_priority_defaults_to_quality(self, client: TestClient) -> None:
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature", "priority": "nonexistent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200, resp.text
