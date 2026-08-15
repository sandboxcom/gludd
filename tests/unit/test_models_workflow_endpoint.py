"""Unit tests for the POST /admin/models/workflow endpoint (routers/models.py).

The endpoint runs the multi-step LangGraphGateway over a chat-message list,
mirroring /admin/models/call's PSK auth + budget pre-check. These tests use a
FastAPI TestClient and stub LangGraphGateway.call so no model provider is hit.

Auth coverage uses the full daemon app (which installs the PSK middleware):
  - without GLUDD_ALLOW_NO_AUTH and no PSK -> fail-closed 503
  - with GLUDD_ALLOW_NO_AUTH=1 -> reachable, passthrough of the canned result
Body validation + passthrough use a minimal app via register() (no middleware).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANNED_RESULT: dict[str, Any] = {
    "content": "def f():\n    return 1\n",
    "model": "default",
    "prompt": None,
    "quality_score": 0.85,
    "retries": 1,
    "warnings": [],
}


def _minimal_app(monkeypatch: pytest.MonkeyPatch, *, canned: dict[str, Any] | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the models router registered.

    Stubs LangGraphGateway.call so no provider call is made; sets the minimal
    app.state the handler reads. No PSK middleware (register() only).
    """
    from general_ludd.routers import models as models_mod

    async def _fake_call(
        self: Any, messages: Any, task_context: Any = None, profile_id: str = "default"
    ) -> dict[str, Any]:
        return dict(canned if canned is not None else _CANNED_RESULT)

    monkeypatch.setattr(models_mod.LangGraphGateway, "call", _fake_call)

    app = FastAPI()
    # Minimal state the handler / register() reads.
    gw = MagicMock()
    profile = MagicMock()
    profile.model_profile_id = "default"
    gw.list_profiles.return_value = [profile]
    gw.call_model.return_value = MagicMock(content="hi", usage_metadata=None)
    app.state._model_gateway = gw
    app.state._budget_guard = None
    app.state._health_tracker = None
    app.state._project_manager = None
    app.state._metrics_collector = None
    app.state._session_factory = None
    app.state._model_registry = MagicMock()
    app.state._model_registry.search.return_value = []
    app.state._model_registry.list_downloaded.return_value = []

    models_mod.register(app, {})
    return app


# ---------------------------------------------------------------------------
# Auth — exercised against the full daemon app (PSK middleware installed)
# ---------------------------------------------------------------------------

class TestWorkflowAuth:
    def test_route_registered(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.0)
        routes = {getattr(r, "path", None) for r in app.routes}
        assert "/admin/models/workflow" in routes

    def test_requires_auth_by_default(self) -> None:
        """No PSK + no GLUDD_ALLOW_NO_AUTH -> fail-closed (503/401)."""
        os.environ.pop("GLUDD_ALLOW_NO_AUTH", None)
        os.environ.pop("GLUDD_AUTH_PSK", None)
        os.environ.pop("GLUDD_REQUIRE_AUTH", None)

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.0)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/admin/models/workflow",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code in (401, 503)

    def test_passthrough_with_no_auth_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLUDD_ALLOW_NO_AUTH=1 -> reachable; canned result passes through."""
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

        from general_ludd.daemon import create_daemon_app
        from general_ludd.routers import models as models_mod

        async def _fake_call(
            self: Any, messages: Any, task_context: Any = None, profile_id: str = "default"
        ) -> dict[str, Any]:
            return dict(_CANNED_RESULT)

        monkeypatch.setattr(models_mod.LangGraphGateway, "call", _fake_call)

        app = create_daemon_app(tick_interval=0.0)
        app.state._budget_guard = None
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/admin/models/workflow",
                json={"messages": [{"role": "user", "content": "write f"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == _CANNED_RESULT["content"]
        assert body["quality_score"] == _CANNED_RESULT["quality_score"]
        assert body["retries"] == _CANNED_RESULT["retries"]


# ---------------------------------------------------------------------------
# Passthrough + validation — minimal app (no middleware)
# ---------------------------------------------------------------------------

class TestWorkflowPassthrough:
    def test_valid_request_passes_through_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/workflow",
            json={
                "messages": [{"role": "user", "content": "write f"}],
                "profile_id": "default",
                "work_type": "feature",
                "max_retries": 1,
                "quality_threshold": 0.5,
                "enable_graph": True,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == _CANNED_RESULT["content"]
        assert body["quality_score"] == _CANNED_RESULT["quality_score"]
        assert body["retries"] == _CANNED_RESULT["retries"]

    def test_empty_messages_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/models/workflow", json={"messages": []})
        assert resp.status_code == 422

    def test_missing_messages_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/models/workflow", json={"profile_id": "default"})
        assert resp.status_code == 422

    def test_malformed_numeric_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/workflow",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "quality_threshold": "not-a-number",
            },
        )
        assert resp.status_code == 422

    def test_exhausted_budget_guard_returns_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _minimal_app(monkeypatch)
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "daily cap"}
        app.state._budget_guard = guard
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/workflow",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 429
        assert "budget exhausted" in resp.json()["detail"]

    def test_gateway_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.routers import models as models_mod

        async def _boom(self: Any, *a: Any, **k: Any) -> dict[str, Any]:
            raise RuntimeError("kaboom")

        app = _minimal_app(monkeypatch)
        monkeypatch.setattr(models_mod.LangGraphGateway, "call", _boom)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/models/workflow",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "workflow execution failed"
