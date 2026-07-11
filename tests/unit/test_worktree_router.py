"""Endpoint tests for routers/worktree.py.

POST /admin/worktree/scan  + GET /admin/worktree/status.
Both are admin paths (not in _PUBLIC_PATHS) and rely on the daemon's
auth middleware for PSK enforcement — the router itself does NOT re-check.
"""

from __future__ import annotations

import hmac

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.routers.worktree import register

_PSK = "unit-test-psk-worktree"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
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


@pytest.fixture
def app() -> FastAPI:
    """Bare app (no auth middleware) for testing router logic directly."""
    _app = FastAPI()
    register(_app, {})
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth posture (PSK gate)
# ---------------------------------------------------------------------------

class TestWorktreeAuthPosture:
    def test_scan_requires_auth(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.post("/admin/worktree/scan", json={})
        assert resp.status_code == 401

    def test_scan_with_valid_psk_reaches_handler(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.post(
            "/admin/worktree/scan",
            json={},
            headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code != 401, resp.text
        assert resp.status_code == 200

    def test_status_requires_auth(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.get("/admin/worktree/status")
        assert resp.status_code == 401

    def test_status_with_valid_psk_reaches_handler(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.get(
            "/admin/worktree/status",
            headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code != 401, resp.text
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scan endpoint
# ---------------------------------------------------------------------------

class TestWorktreeScan:
    POST = "/admin/worktree/scan"

    def test_happy_path_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.post(self.POST, json={})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "todos" in data
        assert "tracked_count" in data
        assert isinstance(data["tracked_count"], int)

    def test_with_watch_paths_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.post(
            self.POST,
            json={},
            params={"watch_paths": "/tmp/nonexistent-wt-scan"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data
        assert "tracked_count" in data

    def test_too_many_watch_paths_returns_413(self, client: TestClient) -> None:
        paths = ",".join(f"/tmp/wt-{i}" for i in range(101))
        resp = client.post(self.POST, json={}, params={"watch_paths": paths})
        assert resp.status_code == 413
        data = resp.json()
        assert "detail" in data


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

class TestWorktreeStatus:
    GET = "/admin/worktree/status"

    def test_happy_path_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "tracked_worktrees" in data
        assert "tracked_count" in data
        assert isinstance(data["tracked_worktrees"], list)
        assert isinstance(data["tracked_count"], int)


# ---------------------------------------------------------------------------
# Register contract
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_on_bare_app_adds_routes(self) -> None:
        app = FastAPI()
        before = len(app.routes)
        register(app, {})
        assert len(app.routes) > before
