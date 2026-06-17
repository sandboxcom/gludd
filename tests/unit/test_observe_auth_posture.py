"""Least-privilege auth posture for the observe router.

The observe endpoints are NOT public — they must sit behind the daemon's PSK
middleware exactly like /api/facts (see routers/facts.py docstring: "PSK auth is
applied by the daemon middleware; path is not public"). The router itself must
not register any public bypass.

Because the integration pass owns the daemon's real _PUBLIC_PATHS set, this test
proves the posture at the contract level: it wraps the observe router in a
middleware that mirrors the daemon's PSK gate (a path is public ONLY if it is on
an allow-list AND the method is safe) with NO observe path on the allow-list,
then asserts:

- an unauthenticated request to every observe path is refused (401);
- the SAME request with a correct Bearer PSK succeeds.

If a future edit gave an observe route a public bypass, the first assertion
would break — locking in the "PSK-gated, not public" requirement.
"""

from __future__ import annotations

import hmac
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.routers.observe import register

_PSK = "unit-test-psk"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Deliberately empty for observe paths — mirrors the daemon NOT listing them.
_PUBLIC_PATHS: set[str] = {"/healthz"}


class _FakeSource:
    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "logs")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
    app.state._connector_registry = ConnectorRegistry.from_config(
        [{"name": "s1", "kind": "logs", "factory": "fake"}],
        factories={"fake": _FakeSource},
    )
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


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_psk_gate())


_CASES = [
    ("GET", "/api/observe/sources", None),
    ("GET", "/api/observe/health", None),
    ("POST", "/api/observe/query", {"source": "s1", "spec": {}}),
]


class TestObserveIsPskGated:
    @pytest.mark.parametrize("method,path,body", _CASES)
    def test_unauthenticated_is_refused(
        self, client: TestClient, method: str, path: str, body: Any
    ) -> None:
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _CASES)
    def test_with_psk_succeeds(
        self, client: TestClient, method: str, path: str, body: Any
    ) -> None:
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"}
        )
        assert resp.status_code == 200
