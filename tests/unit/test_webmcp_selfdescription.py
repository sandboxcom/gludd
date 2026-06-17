"""Tests for GET /api/webmcp self-description accuracy.

P2 self-description honesty checks:
  - POST /api/todos must declare auth_required=True (it is protected by the
    method-aware _is_public() gate in daemon.py — POST is never a safe method).
  - /api/status response_shape must NOT advertise a raw db_url credential
    field (credential-leak risk in a public endpoint's documentation).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.webmcp import register


def _client() -> TestClient:
    app = FastAPI()
    register(app, {})
    return TestClient(app)


class TestWebmcpSelfDescription:
    def test_post_todos_auth_required_is_true(self) -> None:
        """POST /api/todos must declare auth_required=True.

        The daemon's _is_public() gate excludes all non-SAFE methods, so
        POST /api/todos requires a Bearer token even though GET /api/todos
        is public.  The self-description must reflect this accurately.
        """
        client = _client()
        resp = client.get("/api/webmcp")
        assert resp.status_code == 200
        data = resp.json()
        endpoints = data["endpoints"]
        post_todos = [
            ep for ep in endpoints
            if ep["method"] == "POST" and ep["path"] == "/api/todos"
        ]
        assert len(post_todos) == 1, (
            "Expected exactly one POST /api/todos entry in the endpoint inventory"
        )
        assert post_todos[0]["auth_required"] is True, (
            "POST /api/todos must declare auth_required=True — it is gated by "
            "the method-aware _is_public() check (POST is not a safe method)"
        )

    def test_status_response_shape_does_not_advertise_db_url(self) -> None:
        """/api/status response_shape must not contain a raw db_url field.

        db_url is a credential-bearing string.  Advertising it in the public
        self-description leaks where to look for connection secrets and what
        format to expect.  Use db_dialect or a similarly non-sensitive field.
        """
        client = _client()
        resp = client.get("/api/webmcp")
        assert resp.status_code == 200
        data = resp.json()
        endpoints = data["endpoints"]
        status_ep = [
            ep for ep in endpoints
            if ep["method"] == "GET" and ep["path"] == "/api/status"
        ]
        assert len(status_ep) == 1, (
            "Expected exactly one GET /api/status entry in the endpoint inventory"
        )
        response_shape = status_ep[0].get("response_shape", {})
        assert "db_url" not in response_shape, (
            "/api/status response_shape must not advertise 'db_url' — "
            "raw DB credentials must not appear in public documentation"
        )
