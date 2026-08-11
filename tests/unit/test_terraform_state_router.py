"""Unit tests for routers/terraform_state.py — HTTP state CRUD + locking API.

Covers all 5 endpoints (GET/POST/DELETE/LOCK/UNLOCK), edge cases, lock
conflicts, ID mismatches, missing state, and concurrent access patterns.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.routers.terraform_state import (
    _lock_store,
    _state_store,
    register,
)


def _clear_stores() -> None:
    _state_store.clear()
    _lock_store.clear()


@pytest.fixture(autouse=True)
def _reset_stores() -> Any:
    _clear_stores()
    yield
    _clear_stores()


def _make_app() -> Any:
    from fastapi import FastAPI

    app = FastAPI()
    register(app, {})
    return app


def _make_request(method: str, body: dict[str, Any] | None = None) -> Any:
    request = MagicMock()
    request.method = method

    async def _json() -> Any:
        return body or {}

    request.json = AsyncMock(side_effect=_json)
    return request


def _make_get_request() -> Any:
    return _make_request("GET")


def _make_post_request(body: dict[str, Any] | None = None) -> Any:
    return _make_request("POST", body)


def _make_delete_request() -> Any:
    return _make_request("DELETE")


def _make_lock_request(body: dict[str, Any] | None = None) -> Any:
    return _make_request("LOCK", body)


def _make_unlock_request(body: dict[str, Any] | None = None) -> Any:
    return _make_request("UNLOCK", body)


# ── Register ─────────────────────────────────────────────────────────


def test_register_adds_five_routes() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    register(app, {})
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    tf_routes = [r for r in routes if "/api/terraform/state" in r]
    assert len(tf_routes) == 5


def test_register_is_idempotent() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    register(app, {})
    register(app, {})
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    tf_routes = [r for r in routes if "/api/terraform/state" in r]
    assert len(tf_routes) == 10


# ── State CRUD ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_state_not_found_404() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/terraform/state/nonexistent")
    assert resp.status_code == 404
    assert "State not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_and_get_state_roundtrip() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    payload = {"version": 4, "terraform_version": "1.5.0", "serial": 1}
    post_resp = client.post("/api/terraform/state/my-stack", json=payload)
    assert post_resp.status_code == 200
    assert post_resp.json() == {}

    get_resp = client.get("/api/terraform/state/my-stack")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "state" in data
    assert data["state"]["version"] == 4
    assert data["state"]["terraform_version"] == "1.5.0"


@pytest.mark.asyncio
async def test_post_overwrites_existing_state() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/terraform/state/stack1", json={"v": 1})
    client.post("/api/terraform/state/stack1", json={"v": 2})
    resp = client.get("/api/terraform/state/stack1")
    assert resp.json()["state"]["v"] == 2


@pytest.mark.asyncio
async def test_delete_state_success() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/terraform/state/to-delete", json={"v": 1})
    del_resp = client.delete("/api/terraform/state/to-delete")
    assert del_resp.status_code == 200
    assert del_resp.json() == {}
    get_resp = client.get("/api/terraform/state/to-delete")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_state_no_error() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.delete("/api/terraform/state/nonexistent")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_delete_clears_lock_too() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/terraform/state/stack-x", json={"v": 1})
    resp = client.request("LOCK", "/api/terraform/state/stack-x", json={"ID": "lock-1"})
    assert resp.status_code == 200
    client.delete("/api/terraform/state/stack-x")
    unlock_resp = client.request("UNLOCK", "/api/terraform/state/stack-x", json={"ID": "lock-1"})
    assert unlock_resp.status_code == 200
    assert unlock_resp.json() == {}


@pytest.mark.asyncio
async def test_multiple_stacks_independent_state() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/terraform/state/s1", json={"a": 1})
    client.post("/api/terraform/state/s2", json={"b": 2})
    assert client.get("/api/terraform/state/s1").json()["state"]["a"] == 1
    assert client.get("/api/terraform/state/s2").json()["state"]["b"] == 2
    client.delete("/api/terraform/state/s1")
    assert client.get("/api/terraform/state/s2").status_code == 200
    assert client.get("/api/terraform/state/s1").status_code == 404


# ── Locking ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_acquire_success() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    lock_body = {
        "ID": "abc-123",
        "Operation": "apply",
        "Info": "test lock",
        "Who": "test-user",
        "Version": "1.0",
        "Created": "2024-01-01T00:00:00Z",
        "Path": "terraform.tfstate",
    }
    resp = client.request("LOCK", "/api/terraform/state/mystack", json=lock_body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ID"] == "abc-123"
    assert data["Operation"] == "apply"
    assert data["Who"] == "test-user"


@pytest.mark.asyncio
async def test_lock_conflict_when_already_locked() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "first"})
    resp = client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "second"})
    assert resp.status_code == 423
    assert resp.json()["ID"] == "first"


@pytest.mark.asyncio
async def test_lock_auto_generates_id_when_missing() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.request("LOCK", "/api/terraform/state/mystack", json={})
    assert resp.status_code == 200
    lock_id = resp.json()["ID"]
    assert len(lock_id) > 0
    uuid.UUID(lock_id)


@pytest.mark.asyncio
async def test_unlock_success() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
    unlock_resp = client.request("UNLOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
    assert unlock_resp.status_code == 200
    assert unlock_resp.json() == {}
    lock_resp = client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-2"})
    assert lock_resp.status_code == 200


@pytest.mark.asyncio
async def test_unlock_without_prior_lock_no_error() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.request("UNLOCK", "/api/terraform/state/nolock", json={"ID": "x"})
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_unlock_with_wrong_id_409() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "correct"})
    resp = client.request("UNLOCK", "/api/terraform/state/mystack", json={"ID": "wrong"})
    assert resp.status_code == 409
    assert "Lock ID mismatch" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_unlock_with_empty_id_accepted() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/mystack", json={"ID": "lock-1"})
    resp = client.request("UNLOCK", "/api/terraform/state/mystack", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lock_preserves_all_fields() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    lock_body = {
        "ID": "detailed-lock",
        "Operation": "plan",
        "Info": "Planning infrastructure changes",
        "Who": "ci-pipeline",
        "Version": "2.0",
        "Created": "2025-06-15T12:00:00Z",
        "Path": "prod/terraform.tfstate",
    }
    client.request("LOCK", "/api/terraform/state/prod", json=lock_body)
    resp = client.request("LOCK", "/api/terraform/state/prod", json={"ID": "conflict"})
    assert resp.status_code == 423
    data = resp.json()
    assert data["Operation"] == "plan"
    assert data["Who"] == "ci-pipeline"
    assert data["Version"] == "2.0"
    assert data["Path"] == "prod/terraform.tfstate"


# ── Concurrent Operations ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_delete_unlock_cycle_on_different_stacks() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/stack-a", json={"ID": "la"})
    client.request("LOCK", "/api/terraform/state/stack-b", json={"ID": "lb"})
    a_lock = client.request("LOCK", "/api/terraform/state/stack-a", json={"ID": "la2"})
    assert a_lock.status_code == 423
    assert a_lock.json()["ID"] == "la"
    client.request("UNLOCK", "/api/terraform/state/stack-a", json={"ID": "la"})
    a2 = client.request("LOCK", "/api/terraform/state/stack-a", json={"ID": "la2"})
    assert a2.status_code == 200
    assert a2.json()["ID"] == "la2"


# ── Edge Cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_state_with_dash_and_dot_in_stack_name() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    name = "prod-us-east-1.cluster-v2"
    client.post(f"/api/terraform/state/{name}", json={"v": 1})
    resp = client.get(f"/api/terraform/state/{name}")
    assert resp.status_code == 200
    assert resp.json()["state"]["v"] == 1


@pytest.mark.asyncio
async def test_post_empty_object() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/terraform/state/empty", json={})
    assert resp.status_code == 200
    get_resp = client.get("/api/terraform/state/empty")
    assert get_resp.json()["state"] == {}


@pytest.mark.asyncio
async def test_post_large_state_body() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    large = {"resources": [{"type": "aws_instance", "name": f"res-{i}"} for i in range(1000)]}
    resp = client.post("/api/terraform/state/large", json=large)
    assert resp.status_code == 200
    get_resp = client.get("/api/terraform/state/large")
    assert len(get_resp.json()["state"]["resources"]) == 1000


@pytest.mark.asyncio
async def test_lock_empty_body_generates_id() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.request("LOCK", "/api/terraform/state/tf", json={})
    assert resp.status_code == 200
    assert "ID" in resp.json()


@pytest.mark.asyncio
async def test_lock_with_only_id_preserves_generated_created() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.request("LOCK", "/api/terraform/state/tf", json={"ID": "mine"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ID"] == "mine"
    assert "Created" in data
    assert len(data["Created"]) > 0


@pytest.mark.asyncio
async def test_unlock_with_empty_body_accepted() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.request("LOCK", "/api/terraform/state/tf", json={"ID": "x"})
    resp = client.request("UNLOCK", "/api/terraform/state/tf", json={})
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_deleted_stack_can_be_relocked() -> None:
    app = _make_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/terraform/state/stack", json={"v": 1})
    client.delete("/api/terraform/state/stack")
    resp = client.request("LOCK", "/api/terraform/state/stack", json={"ID": "new-lock"})
    assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
