"""Unit tests for PauseController — pause/resume lifecylce, idempotency."""

from __future__ import annotations

from general_ludd.controllers.pause_controller import PauseController, PauseRecord


class FakePauseStore:
    def __init__(self):
        self.saved: list[list[dict]] = []
        _pre_existing = {
            "kind": "project",
            "target_id": "pre-existing",
            "paused_at": 100.0,
            "reason": "",
            "last_state": {},
            "resources": {},
            "agent_handles": [],
            "quiesce_status": "none",
            "quiesce_errors": [],
        }
        self.loaded: list[list[dict]] = [[_pre_existing]]

    def load(self):
        return self.loaded[-1]

    def save(self, records):
        self.saved.append(records)


class TestPauseRecord:
    def test_defaults(self):
        r = PauseRecord(kind="project", target_id="p1", paused_at=0.0)
        assert r.kind == "project"
        assert r.target_id == "p1"
        assert r.paused_at == 0.0
        assert r.reason == ""
        assert r.last_state == {}
        assert r.resources == {}
        assert r.agent_handles == []
        assert r.quiesce_status == "none"
        assert r.quiesce_errors == []

    def test_model_validate_roundtrip(self):
        r = PauseRecord(
            kind="model",
            target_id="m1",
            paused_at=1234.5,
            reason="testing",
            last_state={"key": "val"},
        )
        d = r.model_dump()
        r2 = PauseRecord.model_validate(d)
        assert r2.kind == "model"
        assert r2.target_id == "m1"
        assert r2.paused_at == 1234.5
        assert r2.reason == "testing"
        assert r2.last_state == {"key": "val"}


class TestPauseController:
    def test_init_builds_from_store(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        assert ctrl.is_paused("project", "pre-existing")

    def test_pause_adds_record(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        r = ctrl.pause("project", "my-proj", reason="manual")
        assert r.kind == "project"
        assert r.target_id == "my-proj"
        assert r.reason == "manual"
        assert ctrl.is_paused("project", "my-proj")

    def test_pause_persists_before_returning(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("model", "gpt4")
        assert len(store.saved) >= 1

    def test_resume_removes_record(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("project", "p")
        assert ctrl.is_paused("project", "p")
        result = ctrl.resume("project", "p")
        assert result is not None
        assert not ctrl.is_paused("project", "p")

    def test_pause_idempotent(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        len(store.saved)
        r1 = ctrl.pause("project", "p", reason="first")
        save_count_after_first = len(store.saved)
        r2 = ctrl.pause("project", "p", reason="second")
        assert r1 is r2
        assert len(store.saved) == save_count_after_first

    def test_resume_idempotent(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        assert ctrl.resume("project", "nonexistent") is None

    def test_is_paused_different_kinds(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("project", "p")
        ctrl.pause("model", "m")
        assert ctrl.is_paused("project", "p")
        assert ctrl.is_paused("model", "m")
        assert not ctrl.is_paused("project", "m")
        assert not ctrl.is_paused("model", "p")

    def test_list_paused(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("project", "p1")
        ctrl.pause("model", "m1")
        paused = ctrl.list_paused()
        assert len(paused) >= 2

    def test_get_returns_record(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("project", "p-findme")
        rec = ctrl.get("project", "p-findme")
        assert rec is not None
        assert rec.target_id == "p-findme"

    def test_get_returns_none_for_unknown(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        assert ctrl.get("project", "nope") is None
