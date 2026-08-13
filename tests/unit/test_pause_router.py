"""Tests for routers/pause.py — pause/resume API endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.routers.pause import register


@pytest.fixture
def app_with_pause(tmp_path):
    app = FastAPI()
    app.state._pause_controller = PauseController(
        store=PauseStore(base_dir=str(tmp_path / "pause_store"))
    )
    register(app, {})
    return app


@pytest.fixture
def client(app_with_pause):
    from fastapi.testclient import TestClient
    return TestClient(app_with_pause)


def test_list_paused_empty(client):
    r = client.get("/api/pause")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["paused"] == []


def test_pause_and_list_project(client):
    r = client.post("/api/pause/project", json={"target_id": "proj-1", "reason": "testing"})
    assert r.status_code == 200
    assert r.json()["paused"] is True
    assert r.json()["kind"] == "project"

    r = client.get("/api/pause")
    assert r.json()["count"] == 1


def test_pause_model_and_list(client):
    client.post("/api/pause/model", json={"target_id": "model-1", "reason": "testing"})
    r = client.get("/api/pause")
    assert r.json()["count"] == 1
    assert r.json()["paused"][0]["kind"] == "model"


@pytest.mark.parametrize(
    ("resource", "kind", "target_id"),
    [
        ("tasks", "task", "task-1"),
        ("agents", "agent", "agent-1"),
        ("infra", "infra", "deployment-1"),
    ],
)
def test_entity_pause_status_resume_lifecycle(client, resource, kind, target_id):
    pause_response = client.post(
        f"/api/{resource}/{target_id}/pause",
        json={"reason": f"pause {kind}"},
    )

    assert pause_response.status_code == 200
    assert pause_response.json() == {
        "paused": True,
        "kind": kind,
        "target_id": target_id,
        "paused_at": pause_response.json()["paused_at"],
        "reason": f"pause {kind}",
    }

    status_response = client.get("/api/pause/status")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["count"] == 1
    assert status["paused"][0]["target_id"] == target_id
    assert status["by_type"][kind][0]["reason"] == f"pause {kind}"

    resume_response = client.post(f"/api/{resource}/{target_id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json() == {
        "resumed": True,
        "kind": kind,
        "target_id": target_id,
        "paused_at": pause_response.json()["paused_at"],
    }

    repeated_resume = client.post(f"/api/{resource}/{target_id}/resume")
    assert repeated_resume.status_code == 200
    assert repeated_resume.json() == {
        "resumed": False,
        "target_id": target_id,
        "message": "was not paused",
    }


def test_resume_project(client):
    client.post("/api/pause/project", json={"target_id": "proj-2"})
    r = client.post("/api/resume/project", json={"target_id": "proj-2"})
    assert r.json()["resumed"] is True

    r = client.get("/api/pause")
    assert r.json()["count"] == 0


def test_resume_not_paused_idempotent(client):
    r = client.post("/api/resume/model", json={"target_id": "not-paused"})
    assert r.status_code == 200
    assert r.json()["resumed"] is False


def test_pause_idempotent(client):
    r1 = client.post("/api/pause/project", json={"target_id": "proj-3"})
    r2 = client.post("/api/pause/project", json={"target_id": "proj-3"})
    assert r1.json()["paused_at"] == r2.json()["paused_at"]


def test_no_controller_returns_safe_defaults(tmp_path):
    app = FastAPI()
    register(app, {})
    from fastapi.testclient import TestClient
    c = TestClient(app)

    assert c.get("/api/pause").json() == {"paused": [], "count": 0}
    assert c.get("/api/pause/status").json() == {"paused": [], "count": 0, "by_type": {}}
    assert c.post("/api/pause/project", json={"target_id": "x"}).json()["paused"] is False
    assert c.post("/api/resume/project", json={"target_id": "x"}).json()["resumed"] is False
