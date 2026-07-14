from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.terraform_state import register


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    register(app, {})
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_get_state_not_found(client: TestClient) -> None:
    stack = f"nonexistent-{uuid.uuid4().hex[:6]}"
    resp = client.get(f"/api/terraform/state/{stack}")
    assert resp.status_code == 404
    assert "State not found" in resp.json()["detail"]


def test_post_and_get_state(client: TestClient) -> None:
    stack = f"write-{uuid.uuid4().hex[:6]}"
    content = {"version": 4, "terraform_version": "1.5.0", "resources": []}
    post_resp = client.post(f"/api/terraform/state/{stack}", json=content)
    assert post_resp.status_code == 200

    get_resp = client.get(f"/api/terraform/state/{stack}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == content


def test_delete_state(client: TestClient) -> None:
    stack = f"delete-{uuid.uuid4().hex[:6]}"
    content = {"version": 1}
    client.post(f"/api/terraform/state/{stack}", json=content)

    del_resp = client.delete(f"/api/terraform/state/{stack}")
    assert del_resp.status_code == 200

    get_resp = client.get(f"/api/terraform/state/{stack}")
    assert get_resp.status_code == 404


def test_acquire_lock(client: TestClient) -> None:
    stack = f"lock-{uuid.uuid4().hex[:6]}"
    lock_body = {"ID": "abc-123", "Operation": "apply"}
    resp = client.request("LOCK", f"/api/terraform/state/{stack}", json=lock_body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ID"] == "abc-123"
    assert data["Operation"] == "apply"


def test_lock_conflict(client: TestClient) -> None:
    stack = f"conflict-{uuid.uuid4().hex[:6]}"
    lock_body = {"ID": "lock-1"}
    resp1 = client.request("LOCK", f"/api/terraform/state/{stack}", json=lock_body)
    assert resp1.status_code == 200

    resp2 = client.request("LOCK", f"/api/terraform/state/{stack}", json={"ID": "lock-2"})
    assert resp2.status_code == 423
    assert resp2.json()["ID"] == "lock-1"


def test_unlock(client: TestClient) -> None:
    stack = f"ulock-{uuid.uuid4().hex[:6]}"
    lock_id = "unlock-me"
    client.request("LOCK", f"/api/terraform/state/{stack}", json={"ID": lock_id})

    resp = client.request("UNLOCK", f"/api/terraform/state/{stack}", json={"ID": lock_id})
    assert resp.status_code == 200

    resp2 = client.request("LOCK", f"/api/terraform/state/{stack}", json={"ID": "new-lock"})
    assert resp2.status_code == 200


def test_unlock_wrong_id(client: TestClient) -> None:
    stack = f"wrong-{uuid.uuid4().hex[:6]}"
    client.request("LOCK", f"/api/terraform/state/{stack}", json={"ID": "correct-id"})

    resp = client.request("UNLOCK", f"/api/terraform/state/{stack}", json={"ID": "wrong-id"})
    assert resp.status_code == 409
    assert "Lock ID mismatch" in resp.json()["detail"]


def test_unlock_no_lock(client: TestClient) -> None:
    stack = f"nolock-{uuid.uuid4().hex[:6]}"
    resp = client.request("UNLOCK", f"/api/terraform/state/{stack}", json={"ID": "any"})
    assert resp.status_code == 200
    assert resp.json() == {}


def test_lock_unlock_cycle(client: TestClient) -> None:
    stack = f"cycle-{uuid.uuid4().hex[:6]}"
    for i in range(3):
        lock_id = f"cycle-lock-{i}"
        lock_resp = client.request("LOCK", f"/api/terraform/state/{stack}", json={"ID": lock_id})
        assert lock_resp.status_code == 200
        assert lock_resp.json()["ID"] == lock_id

        unlock_resp = client.request("UNLOCK", f"/api/terraform/state/{stack}", json={"ID": lock_id})
        assert unlock_resp.status_code == 200


def test_post_overwrites_state(client: TestClient) -> None:
    stack = f"overwrite-{uuid.uuid4().hex[:6]}"
    first = {"version": 1, "data": "alpha"}
    second = {"version": 2, "data": "beta"}

    client.post(f"/api/terraform/state/{stack}", json=first)
    client.post(f"/api/terraform/state/{stack}", json=second)

    get_resp = client.get(f"/api/terraform/state/{stack}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == second
