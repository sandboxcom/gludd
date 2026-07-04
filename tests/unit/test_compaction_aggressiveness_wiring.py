"""Tests for CompactionAggressivenessController wiring in daemon and router."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.controllers.compaction_aggressiveness import (
    CompactionAggressivenessController,
)
from general_ludd.routers.compaction_aggressiveness import register


@pytest.fixture
def app_with_controller():
    app = FastAPI()
    app.state._compaction_aggressiveness_controller = (
        CompactionAggressivenessController()
    )
    register(app, {})
    return app


@pytest.fixture
def client(app_with_controller):
    return TestClient(app_with_controller)


def test_aggressiveness_status_returns_controller_params(client):
    r = client.get("/admin/compaction/aggressiveness-status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert isinstance(body["floor"], float)
    assert isinstance(body["min_samples"], int)
    assert isinstance(body["max_level"], int)


def test_aggressiveness_status_default_values(client):
    r = client.get("/admin/compaction/aggressiveness-status")
    body = r.json()
    assert body["floor"] == 0.9
    assert body["min_samples"] == 20
    assert body["max_level"] >= 0


def test_aggressiveness_status_custom_params():
    app = FastAPI()
    app.state._compaction_aggressiveness_controller = (
        CompactionAggressivenessController(floor=0.8, min_samples=50, max_level=3)
    )
    register(app, {})
    c = TestClient(app)

    r = c.get("/admin/compaction/aggressiveness-status")
    body = r.json()
    assert body["floor"] == 0.8
    assert body["min_samples"] == 50
    assert body["max_level"] == 3


def test_no_controller_returns_unavailable():
    app = FastAPI()
    register(app, {})
    c = TestClient(app)

    r = c.get("/admin/compaction/aggressiveness-status")
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_controller_via_app_state():
    """Verify the real Controller class is importable and constructable."""
    ctrl = CompactionAggressivenessController()
    assert ctrl.floor == 0.9
    assert ctrl.min_samples == 20
    assert ctrl.max_level >= 0
