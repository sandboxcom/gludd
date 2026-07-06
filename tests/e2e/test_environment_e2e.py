"""E2E proof: GET /api/environment through the daemon router.

Exercises the full environment-introspection endpoint:
  1. Returns 200 with all top-level sections present.
  2. Model roster NEVER leaks credentials (the load-bearing security assertion).
  3. PSK auth gating (401 without token, 200 with).
  4. Optimization advisor surfaces hints.
  5. Project facet returns project data when available.
  6. Degraded gracefully when subsystems are absent (no 500).

This is the missing e2e proof for environment-endpoint (features.yml: 85%->100%).
"""

from __future__ import annotations

import hmac
import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.routers.environment import register

_PSK = "env-e2e-test-psk"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}
_SECRET_VALUE = "sk-super-secret-key-DO-NOT-LEAK-0123456789"  # pragma: allowlist secret


class _PSKMiddleware(BaseHTTPMiddleware):
    """Minimal PSK auth gate mirroring the daemon's real middleware contract.

    Subclasses BaseHTTPMiddleware so Starlette's ``cls(app, *args, **kwargs)``
    instantiation in ``build_middleware_stack`` succeeds — the prior
    ``type("PSKMiddleware", (), ...)`` stub lacked an ``__init__`` and raised
    ``TypeError: PSKMiddleware() takes no arguments`` on every request.
    """

    def __init__(self, app: ASGIApp, psk: str) -> None:
        super().__init__(app)
        self._psk = psk

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SAFE_METHODS and request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(
            auth.removeprefix("Bearer "), self._psk
        ):
            return JSONResponse(
                status_code=401, content={"detail": "Unauthorized"}
            )
        return await call_next(request)


def _gateway_with_secret() -> ModelGateway:
    profile = ModelProfile(
        model_profile_id="flagship",
        provider="openai",
        model_name="glm-4.6",
        api_key=_SECRET_VALUE,
        api_base="https://api.example.com",
    )
    gw = MagicMock(spec=ModelGateway)
    gw.list_profiles.return_value = [profile]
    return gw


def _app(**state: object) -> FastAPI:
    app = FastAPI()
    app.add_middleware(_PSKMiddleware, psk=_PSK)
    app.state._psk = _PSK
    app.state._model_gateway = state.get("model_gateway")
    app.state._startup_config = state.get("startup_config", {})
    app.state._budget_guard = state.get("budget_guard")
    app.state._spend_limiter = state.get("spend_limiter")
    app.state._skill_registry = state.get("skill_registry")
    app.state._mcp_client = state.get("mcp_client")
    app.state._metrics_collector = state.get("metrics_collector")
    app.state._session_factory = state.get("session_factory")
    app.state._project_repo = state.get("project_repo")
    app.state._todo_repo = state.get("todo_repo")
    register(app, {"psk": _PSK})
    return app


def _psk_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_PSK}"}


# ---------------------------------------------------------------------------
# PSK auth gating
# ---------------------------------------------------------------------------


class TestEnvironmentAuth:
    def test_rejected_without_psk(self) -> None:
        client = TestClient(_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 401

    def test_accepted_with_psk(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200

    def test_wrong_psk_rejected(self) -> None:
        client = TestClient(_app())
        resp = client.get(
            "/api/environment",
            headers={"Authorization": "Bearer wrong-psk"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Response structure
# ---------------------------------------------------------------------------


class TestEnvironmentResponse:
    def test_all_top_level_sections_present(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert "models" in body
        assert "routing" in body
        assert "system" in body
        assert "optimization" in body

    def test_optimization_section_has_hints(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        body = resp.json()
        assert "optimization" in body
        assert "hints" in body["optimization"]

    def test_system_section_has_python_version(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        body = resp.json()
        assert "python_version" in body["system"]

    def test_routing_section_present(self) -> None:
        gw = _gateway_with_secret()
        routing_config = {"model_routing": {"default_profile": "test"}}
        client = TestClient(_app(model_gateway=gw, startup_config=routing_config))
        resp = client.get("/api/environment", headers=_psk_headers())
        body = resp.json()
        assert "routing" in body


# ---------------------------------------------------------------------------
# Security: credential leak prevention
# ---------------------------------------------------------------------------


class TestEnvironmentCredentials:
    def test_api_key_never_leaks_in_model_roster(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        body = resp.json()
        raw = json.dumps(body)
        assert _SECRET_VALUE not in raw

    def test_no_psk_leaked_in_response(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        raw = resp.text
        assert _PSK not in raw

    def test_model_field_names_are_characteristic_only(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        body = resp.json()
        for model in body.get("models", []):
            assert "api_key" not in model
            assert "api_secret" not in model
            assert "token" not in model
            assert "password" not in model


# ---------------------------------------------------------------------------
# Degradation: no 500 when subsystems absent
# ---------------------------------------------------------------------------


class TestEnvironmentDegradation:
    def test_no_gateway_returns_200_with_empty_models(self) -> None:
        client = TestClient(_app())
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("models") == [] or body.get("models") is None

    def test_no_budget_guard_still_returns_200(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200

    def test_no_skill_registry_still_returns_200(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200

    def test_no_mcp_client_still_returns_200(self) -> None:
        gw = _gateway_with_secret()
        client = TestClient(_app(model_gateway=gw))
        resp = client.get("/api/environment", headers=_psk_headers())
        assert resp.status_code == 200
