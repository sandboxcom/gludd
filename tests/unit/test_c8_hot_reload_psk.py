"""C8: Worker registration endpoint MUST require PSK auth.

The worker registration endpoint leaks the daemon PSK to arbitrary
caller-supplied addresses when unauthenticated. The fix: the endpoint
requires the PSK it would receive, enforced by the daemon's existing
Authorization: Bearer middleware on all /admin/* paths.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.reload.worker_broadcast import WorkerBroadcaster
from general_ludd.routers.reload import register
from general_ludd.security.auth import check_bearer_token

_PSK = "test-secret-psk"
_SAFE_WORKER = "https://worker-1.internal.example.com:8000"


def _build_client() -> TestClient:
    app = FastAPI()

    app.state._psk = _PSK
    app.state._no_auth = False
    app.state._require_auth = False
    app.state._allow_no_auth = False
    app.state._config_dir = "/tmp/gludd-test-config"
    app.state._templates_dir = "/tmp/gludd-test-templates"
    app.state._playbooks_dir = "/tmp/gludd-test-playbooks"
    app.state._project_gludd_dir = None

    @app.middleware("http")
    async def auth_middleware(request, call_next):
        from fastapi.responses import JSONResponse

        path = request.url.path
        if not path.startswith("/admin/"):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not check_bearer_token(auth_header, _PSK):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    broadcaster = WorkerBroadcaster()
    app.state._event_bus = MagicMock()
    app.state._hook_system = MagicMock()
    app.state._hook_system.list_hooks.return_value = []
    app.state._hook_system.register_webhook.return_value = "mock-hook-id"
    app.state._worker_broadcaster = broadcaster

    register(app, {})

    return TestClient(app)


def test_worker_registration_requires_psk() -> None:
    """POST /admin/workers without Authorization header → 401."""
    client = _build_client()
    resp = client.post("/admin/workers", json={
        "worker_id": "w1",
        "address": _SAFE_WORKER,
    })
    assert resp.status_code == 401


def test_worker_registration_with_valid_psk() -> None:
    """POST /admin/workers with valid Bearer PSK → 200, worker registered."""
    client = _build_client()
    resp = client.post(
        "/admin/workers",
        json={"worker_id": "w1", "address": _SAFE_WORKER},
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True, data
    assert data["worker_id"] == "w1"

    list_resp = client.get(
        "/admin/workers",
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert list_resp.status_code == 200
    workers = list_resp.json()["workers"]
    assert any(w["worker_id"] == "w1" for w in workers)


def test_worker_registration_with_invalid_psk() -> None:
    """POST /admin/workers with wrong Bearer PSK → 401."""
    client = _build_client()
    resp = client.post(
        "/admin/workers",
        json={"worker_id": "w1", "address": _SAFE_WORKER},
        headers={"Authorization": "Bearer wrong-psk"},
    )
    assert resp.status_code == 401


def test_worker_registration_rejects_unsafe_address() -> None:
    """POST /admin/workers with metadata address → 422 (SSRF guard via Pydantic validation)."""
    client = _build_client()
    resp = client.post(
        "/admin/workers",
        json={"worker_id": "meta", "address": "http://169.254.169.254"},
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert resp.status_code == 422
