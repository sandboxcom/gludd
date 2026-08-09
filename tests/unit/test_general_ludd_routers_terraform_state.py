from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.terraform_state import register


@pytest.fixture
def client():
    import general_ludd.routers.terraform_state as ts_module

    ts_module._state_store.clear()
    ts_module._lock_store.clear()

    app = FastAPI()
    daemon_state: dict[str, object] = {}
    register(app, daemon_state)
    return TestClient(app)


class TestTerraformStateGet:
    def test_get_missing_returns_404(self, client):
        response = client.get("/api/terraform/state/nonexistent")
        assert response.status_code == 404

    def test_get_after_post(self, client):
        state = {"version": 4, "resources": []}
        client.post("/api/terraform/state/test-stack", json=state)
        response = client.get("/api/terraform/state/test-stack")
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert data["state"]["version"] == 4


class TestTerraformStatePost:
    def test_post_simple(self, client):
        state = {"version": 1, "resources": [{"type": "aws_instance"}]}
        response = client.post("/api/terraform/state/stack-a", json=state)
        assert response.status_code == 200

    def test_post_overwrites(self, client):
        state1 = {"version": 1}
        state2 = {"version": 2}
        client.post("/api/terraform/state/stack-b", json=state1)
        client.post("/api/terraform/state/stack-b", json=state2)
        response = client.get("/api/terraform/state/stack-b")
        assert response.json()["state"]["version"] == 2


class TestTerraformStateDelete:
    def test_delete_existing(self, client):
        client.post("/api/terraform/state/stack-c", json={"version": 1})
        response = client.delete("/api/terraform/state/stack-c")
        assert response.status_code == 200
        assert client.get("/api/terraform/state/stack-c").status_code == 404

    def test_delete_nonexistent_is_idempotent(self, client):
        response = client.delete("/api/terraform/state/nonexistent")
        assert response.status_code == 200


class TestTerraformStateLock:
    def test_acquire_lock(self, client):
        body = {"ID": "lock-1", "Operation": "plan"}
        response = client.request("LOCK", "/api/terraform/state/stack-d", json=body)
        assert response.status_code == 200
        data = response.json()
        assert data["ID"] == "lock-1"

    def test_double_lock_returns_423(self, client):
        body = {"ID": "lock-1"}
        client.request("LOCK", "/api/terraform/state/stack-e", json=body)
        response = client.request("LOCK", "/api/terraform/state/stack-e", json={"ID": "lock-2"})
        assert response.status_code == 423

    def test_lock_auto_generates_id(self, client):
        response = client.request("LOCK", "/api/terraform/state/stack-f", json={})
        assert response.status_code == 200
        data = response.json()
        assert "ID" in data
        assert len(data["ID"]) > 0


class TestTerraformStateUnlock:
    def test_unlock_with_correct_id(self, client):
        client.request("LOCK", "/api/terraform/state/stack-g", json={"ID": "lock-1"})
        response = client.request("UNLOCK", "/api/terraform/state/stack-g", json={"ID": "lock-1"})
        assert response.status_code == 200

    def test_unlock_mismatched_id_returns_409(self, client):
        client.request("LOCK", "/api/terraform/state/stack-h", json={"ID": "lock-1"})
        response = client.request("UNLOCK", "/api/terraform/state/stack-h", json={"ID": "wrong-id"})
        assert response.status_code == 409

    def test_unlock_nonexistent_returns_200(self, client):
        response = client.request("UNLOCK", "/api/terraform/state/no-lock", json={})
        assert response.status_code == 200
