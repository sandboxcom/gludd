"""Tests for #35 SLICE 3 — resource capture into PauseRecord."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.routers.pause import register


def test_pause_captures_resources(tmp_path):
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    record = pc.pause(
        "project",
        "proj-1",
        reason="maintenance",
        resources={"spend_usd": 12.50, "active_leases": 3},
        last_state={"phase": "reconcile"},
    )

    assert record.resources == {"spend_usd": 12.50, "active_leases": 3}
    assert record.last_state == {"phase": "reconcile"}
    assert record.kind == "project"
    assert record.reason == "maintenance"


def test_pause_captures_empty_resources_by_default(tmp_path):
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    record = pc.pause("model", "m1")

    assert record.resources == {}
    assert record.last_state == {}


def test_resume_returns_captured_state(tmp_path):
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    pc.pause("project", "p1", resources={"key": "val"})
    record = pc.resume("project", "p1")

    assert record is not None
    assert record.resources == {"key": "val"}


def test_pause_idempotent_preserves_first_capture(tmp_path):
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    r1 = pc.pause("project", "p1", resources={"first": True})
    r2 = pc.pause("project", "p1", resources={"second": True})

    assert r1.resources == {"first": True}
    assert r2.resources == {"first": True}  # idempotent preserves first
    assert r1.paused_at == r2.paused_at


# ---------------------------------------------------------------------------
# Router-level tests — resource capture through the REST endpoint
# ---------------------------------------------------------------------------

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


def test_endpoint_pause_project_captures_resources_from_request(app_with_pause, client):
    """POST /api/pause/project with resources → PauseRecord.resources is NOT empty."""
    r = client.post(
        "/api/pause/project",
        json={
            "target_id": "proj-42",
            "reason": "maintenance",
            "resources": {"spend_usd": 12.50, "active_leases": 3},
            "last_state": {"phase": "reconcile"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["paused"] is True

    # Verify the PauseController stored the resources — access it directly
    pc = app_with_pause.state._pause_controller
    record = pc.get("project", "proj-42")
    assert record is not None
    assert record.resources == {"spend_usd": 12.50, "active_leases": 3}, (
        f"Expected resources to be captured from request, got {record.resources}"
    )
    assert record.last_state == {"phase": "reconcile"}, (
        f"Expected last_state to be captured from request, got {record.last_state}"
    )


def test_endpoint_pause_project_resources_empty_by_default(app_with_pause, client):
    """POST /api/pause/project without resources → resources should be {}."""
    r = client.post(
        "/api/pause/project",
        json={"target_id": "proj-99", "reason": "testing"},
    )
    assert r.status_code == 200
    assert r.json()["paused"] is True

    pc = app_with_pause.state._pause_controller
    record = pc.get("project", "proj-99")
    assert record is not None
    assert record.resources == {}
    assert record.last_state == {}

    # FIXME: When the daemon is hooked up, resources should NOT be empty when
    # app.state carries daemon state (spend facet, process registry, etc.).
    # For now, the test captures the current default — empty — and serves as
    # a documentation guardrail: the day someone wires the daemon to collect
    # actual state, this test must be updated to assert non-empty resources.


# ---------------------------------------------------------------------------
# SLICE 3b — agent-handle quiescing on project pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quiesce_project_no_dispatcher(tmp_path):
    """quiesce_project with None dispatcher gracefully returns []."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)
    handles = await pc.quiesce_project("proj-1", None, None)
    assert handles == []
    assert isinstance(handles, list)


def test_pause_stores_agent_handles(tmp_path):
    """pause() accepts agent_handles and stores them in the record."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)
    record = pc.pause("project", "proj-1", agent_handles=["h1", "h2"])
    assert record.agent_handles == ["h1", "h2"]


def test_pause_default_agent_handles_empty(tmp_path):
    """pause() without agent_handles defaults to empty list."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)
    record = pc.pause("project", "proj-1")
    assert record.agent_handles == []


@pytest.mark.asyncio
async def test_quiesce_then_pause_pipeline(tmp_path):
    """Full pipeline: quiesce_project → pause stores the handles."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)
    handles = await pc.quiesce_project("proj-1", None, None)
    record = pc.pause("project", "proj-1", agent_handles=handles)
    assert record.agent_handles == []
