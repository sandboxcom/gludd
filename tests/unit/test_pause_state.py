"""Unit tests for entity-scoped pause/resume: tasks, agents, infra, projects, models."""

from __future__ import annotations

from general_ludd.controllers.pause_controller import PauseController, PauseRecord


class FakePauseStore:
    """Store with pre-loaded state for controller rebuild tests."""

    def __init__(self, preloaded: list[dict] | None = None):
        self.saved: list[list[dict]] = []
        self.loaded: list[list[dict]] = [[*(preloaded or [])]]

    def load(self):
        return self.loaded[-1]

    def save(self, records):
        self.saved.append(records)


def _base_record(**kw: object) -> dict:
    defaults: dict[str, object] = {
        "kind": "task",
        "target_id": "t1",
        "paused_at": 0.0,
        "reason": "",
        "last_state": {},
        "resources": {},
        "agent_handles": [],
        "quiesce_status": "none",
        "quiesce_errors": [],
    }
    defaults.update(kw)
    return defaults


class TestEntityScopedPause:
    """pause/resume for task, agent, infra kinds."""

    def test_pause_task(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        r = ctrl.pause("task", "task-1", reason="blocked")
        assert r.kind == "task"
        assert r.target_id == "task-1"
        assert ctrl.is_paused("task", "task-1")

    def test_resume_task(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "task-1")
        assert ctrl.is_paused("task", "task-1")
        result = ctrl.resume("task", "task-1")
        assert result is not None
        assert not ctrl.is_paused("task", "task-1")

    def test_pause_agent(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        r = ctrl.pause("agent", "agent-xyz", reason="draining")
        assert r.kind == "agent"
        assert ctrl.is_paused("agent", "agent-xyz")

    def test_resume_agent(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("agent", "agent-xyz")
        ctrl.resume("agent", "agent-xyz")
        assert not ctrl.is_paused("agent", "agent-xyz")

    def test_pause_infra(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        r = ctrl.pause("infra", "deploy-42", reason="maintenance")
        assert r.kind == "infra"
        assert ctrl.is_paused("infra", "deploy-42")

    def test_resume_infra(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("infra", "deploy-42")
        ctrl.resume("infra", "deploy-42")
        assert not ctrl.is_paused("infra", "deploy-42")


class TestCrossEntityIsolation:
    """pausing one entity type must not affect others."""

    def test_kinds_are_isolated(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "t")
        ctrl.pause("agent", "a")
        ctrl.pause("infra", "i")
        ctrl.pause("project", "p")
        ctrl.pause("model", "m")
        assert ctrl.is_paused("task", "t")
        assert ctrl.is_paused("agent", "a")
        assert ctrl.is_paused("infra", "i")
        assert ctrl.is_paused("project", "p")
        assert ctrl.is_paused("model", "m")
        assert not ctrl.is_paused("task", "a")
        assert not ctrl.is_paused("agent", "t")

    def test_resume_one_kind_does_not_affect_others(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "t")
        ctrl.pause("agent", "a")
        ctrl.resume("task", "t")
        assert not ctrl.is_paused("task", "t")
        assert ctrl.is_paused("agent", "a")


class TestListPausedWithFiltering:
    """list_paused() supports kind filtering and returns full list."""

    def test_list_all_returns_every_kind(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "t1")
        ctrl.pause("agent", "a1")
        ctrl.pause("infra", "i1")
        paused = ctrl.list_paused()
        kinds = {r.kind for r in paused}
        assert "task" in kinds
        assert "agent" in kinds
        assert "infra" in kinds

    def test_list_filtered_by_kind(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "t1")
        ctrl.pause("task", "t2")
        ctrl.pause("agent", "a1")
        tasks = ctrl.list_paused(kind="task")
        assert all(r.kind == "task" for r in tasks)
        assert len(tasks) == 2

    def test_list_filtered_empty(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        ctrl.pause("task", "t1")
        infra_list = ctrl.list_paused(kind="infra")
        assert infra_list == []


class TestRebuildFromStore:
    """controller rebuilds all entity kinds from persisted state."""

    def test_rebuild_all_kinds(self):
        preloaded = [
            _base_record(kind="task", target_id="t-pre"),
            _base_record(kind="agent", target_id="a-pre"),
            _base_record(kind="infra", target_id="i-pre"),
            _base_record(kind="project", target_id="p-pre"),
            _base_record(kind="model", target_id="m-pre"),
        ]
        store = FakePauseStore(preloaded=preloaded)
        ctrl = PauseController(store=store)
        assert ctrl.is_paused("task", "t-pre")
        assert ctrl.is_paused("agent", "a-pre")
        assert ctrl.is_paused("infra", "i-pre")
        assert ctrl.is_paused("project", "p-pre")
        assert ctrl.is_paused("model", "m-pre")


class TestPauseIdempotency:
    """re-pausing returns existing record, double-resume returns None."""

    def test_pause_task_idempotent(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        r1 = ctrl.pause("task", "t", reason="first")
        save_count = len(store.saved)
        r2 = ctrl.pause("task", "t", reason="second")
        assert r1 is r2
        assert len(store.saved) == save_count

    def test_resume_nonexistent_returns_none(self):
        store = FakePauseStore()
        ctrl = PauseController(store=store)
        assert ctrl.resume("infra", "nonexistent") is None


class TestPauseRecordDefaults:
    """PauseRecord validates with default fields."""

    def test_create_all_kinds(self):
        for kind in ("task", "agent", "infra", "project", "model"):
            r = PauseRecord(kind=kind, target_id="id1", paused_at=0.0)
            assert r.kind == kind
            assert r.reason == ""

    def test_reason_preserved(self):
        r = PauseRecord(kind="infra", target_id="deploy-1", paused_at=0.0, reason="maint window")
        assert r.reason == "maint window"
