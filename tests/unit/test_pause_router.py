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
    assert c.post("/api/pause/project", json={"target_id": "x"}).json()["paused"] is False
    assert c.post("/api/resume/project", json={"target_id": "x"}).json()["resumed"] is False
