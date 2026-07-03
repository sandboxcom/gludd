"""Tests for #35 SLICE 3 — resource capture into PauseRecord."""

from __future__ import annotations

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore


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
