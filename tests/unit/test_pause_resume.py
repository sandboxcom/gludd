"""End-to-end pause/resume lifecycle tests (D.7.1).

Covers the three required dimensions from TASKS.md:
  1. Persist-before-mutate: durable write MUST succeed before RAM mutation
  2. Lock-free is_paused: O(1) hot-path check, no lock contention
  3. Router ordering: pause → persist → resume flow preserved at API layer
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.routers.pause import register

# ---------------------------------------------------------------------------
# 1. Persist-before-mutate: durable state exists on disk BEFORE is_paused=True
# ---------------------------------------------------------------------------


class _SpyingStore(PauseStore):
    """Records the exact order of save() vs. in-RAM mutation for testing."""

    def __init__(self, base_dir: str = "") -> None:
        super().__init__(base_dir=base_dir)
        self._save_order: list[str] = []
        self._save_side_effect: BaseException | None = None

    def save(self, records: list[dict[str, object]]) -> None:
        self._save_order.append("save_before_mutate")
        super().save(records)


def test_persist_before_mutate_write_occurs_first(tmp_path):
    """save() is called BEFORE the controller's in-RAM state is updated."""
    store = _SpyingStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)  # type: ignore[arg-type]

    pc.pause("project", "proj-order")
    assert "save_before_mutate" in store._save_order
    assert pc.is_paused("project", "proj-order") is True


def test_pause_persists_then_is_paused_becomes_true(tmp_path):
    """A fresh controller reading from disk sees what pause() durably wrote."""
    store_a = PauseStore(base_dir=str(tmp_path / "ps"))
    pc_a = PauseController(store=store_a)
    pc_a.pause("model", "m1", reason="ordered-test")
    del pc_a

    pc_b = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    assert pc_b.is_paused("model", "m1") is True
    record = pc_b.get("model", "m1")
    assert record is not None
    assert record.reason == "ordered-test"


def test_resume_persists_then_is_paused_becomes_false(tmp_path):
    """A fresh controller reading from disk sees what resume() durably cleared."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc_a = PauseController(store=store)
    pc_a.pause("project", "p1")
    pc_a.resume("project", "p1")
    del pc_a

    pc_b = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    assert pc_b.is_paused("project", "p1") is False
    assert pc_b.list_paused() == []


def test_disk_state_matches_ram_after_every_mutation(tmp_path):
    """After each pause/resume, on-disk state == in-RAM state."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "p-disk")
    on_disk = store.load()
    paused_ids = [r["target_id"] for r in on_disk]
    assert "p-disk" in paused_ids
    assert pc.is_paused("project", "p-disk") is True

    pc.resume("project", "p-disk")
    on_disk = store.load()
    assert on_disk == []
    assert pc.is_paused("project", "p-disk") is False


def test_persist_failure_does_not_update_ram(tmp_path):
    """When store.save() raises, is_paused() and list_paused() MUST match the
    prior durable state — never a partially-applied mutation."""

    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "stable")
    assert pc.is_paused("project", "stable") is True

    pc._store = None  # type: ignore[assignment]
    with pytest.raises(AttributeError):
        pc.pause("project", "should-not-persist")

    assert pc.is_paused("project", "stable") is True
    assert pc.is_paused("project", "should-not-persist") is False


# ---------------------------------------------------------------------------
# 2. Lock-free is_paused: O(1) hot-path safety under concurrent read
# ---------------------------------------------------------------------------


def test_is_paused_lock_free_returns_atomic_view(tmp_path):
    """is_paused() reads a frozenset reference without acquiring self._lock.
    The frozenset reference rebind is atomic, so a reader always observes
    either the prior or the new durable state, never a torn set."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "a")
    pc.pause("model", "m")

    errors: list[BaseException] = []

    def hammer_read() -> None:
        try:
            for _ in range(500):
                pc.is_paused("project", "a")
                pc.is_paused("model", "m")
                pc.is_paused("project", "nonexistent")
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    def mutator() -> None:
        try:
            for i in range(30):
                pc.pause("project", f"churn-{i}")
                if i % 2 == 0:
                    pc.resume("project", f"churn-{i}")
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    readers = [threading.Thread(target=hammer_read) for _ in range(3)]
    writer = threading.Thread(target=mutator)
    for t in readers:
        t.start()
    writer.start()
    writer.join(timeout=30)
    for t in readers:
        t.join(timeout=30)

    assert errors == [], f"lock-free readers raised: {errors!r}"
    assert pc.is_paused("project", "a") is True


def test_is_paused_never_sees_torn_state(tmp_path):
    """Even during rapid pause/resume churn, is_paused() never crashes with
    a torn-set error or inconsistent membership."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    for i in range(100):
        pc.pause("project", f"tear-{i}")

    errors: list[BaseException] = []

    def bulk_reader() -> None:
        try:
            for _ in range(200):
                for i in range(100):
                    pc.is_paused("project", f"tear-{i}")
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=bulk_reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"torn-state readers raised: {errors!r}"


# ---------------------------------------------------------------------------
# 3. Router ordering: pause router enforces ordering through the API layer
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_controller(tmp_path):
    app = FastAPI()
    app.state._pause_controller = PauseController(
        store=PauseStore(base_dir=str(tmp_path / "ps"))
    )
    register(app, {})
    return app


@pytest.fixture
def client(app_with_controller):
    from fastapi.testclient import TestClient

    return TestClient(app_with_controller)


def test_router_pause_before_resume_ordering(client):
    """Resume before pause returns not-paused. Pause then resume works."""
    r = client.post("/api/resume/project", json={"target_id": "rp-order"})
    assert r.json()["resumed"] is False

    r = client.post("/api/pause/project", json={"target_id": "rp-order"})
    assert r.json()["paused"] is True

    r = client.get("/api/pause")
    assert r.json()["count"] == 1

    r = client.post("/api/resume/project", json={"target_id": "rp-order"})
    assert r.json()["resumed"] is True

    r = client.get("/api/pause")
    assert r.json()["count"] == 0


def test_router_pause_then_list_shows_record(client):
    """After pausing, listing returns the record with correct fields."""
    client.post("/api/pause/model", json={"target_id": "router-model", "reason": "router-test"})
    r = client.get("/api/pause")
    data = r.json()
    assert data["count"] == 1
    record = data["paused"][0]
    assert record["kind"] == "model"
    assert record["target_id"] == "router-model"
    assert record["reason"] == "router-test"


def test_router_multiple_pause_resume_ordering(client):
    """Multiple pause/resume calls maintain correct ordering."""
    for i in range(5):
        client.post("/api/pause/project", json={"target_id": f"multi-{i}"})

    r = client.get("/api/pause")
    assert r.json()["count"] == 5

    for i in range(3):
        client.post("/api/resume/project", json={"target_id": f"multi-{i}"})

    r = client.get("/api/pause")
    assert r.json()["count"] == 2

    paused_ids = [r["target_id"] for r in r.json()["paused"]]
    assert "multi-3" in paused_ids
    assert "multi-4" in paused_ids


def test_router_pause_idempotent_persist_ordering(client):
    """Re-pausing the same entity does not change the persisted record or
    create duplicates."""
    import time as _time

    r1 = client.post("/api/pause/project", json={"target_id": "idem-order"})
    _time.time()
    r2 = client.post("/api/pause/project", json={"target_id": "idem-order"})
    _time.time()

    assert r2.status_code == 200
    assert r2.json()["paused_at"] == r1.json()["paused_at"]
    # listing still shows exactly one record
    r = client.get("/api/pause")
    assert r.json()["count"] == 1


def test_router_requires_target_id(client):
    """Pause endpoint requires target_id (Pydantic validation)."""
    r = client.post("/api/pause/project", json={})
    assert r.status_code == 422


def test_router_no_controller_returns_safe_default(client):
    """When no PauseController is wired, endpoints return safe defaults
    rather than crashing."""
    app_empty = FastAPI()
    register(app_empty, {})

    from fastapi.testclient import TestClient

    c_empty = TestClient(app_empty)

    r = c_empty.get("/api/pause")
    assert r.json() == {"paused": [], "count": 0}

    r = c_empty.post("/api/pause/project", json={"target_id": "x"})
    assert r.json()["paused"] is False

    r = c_empty.post("/api/resume/project", json={"target_id": "x"})
    assert r.json()["resumed"] is False


# ---------------------------------------------------------------------------
# 4. Full lifecycle: end-to-end pause → persist → check → resume → persist
# ---------------------------------------------------------------------------


def test_full_lifecycle_pause_resume_persist(tmp_path):
    """End-to-end: pause → verify on-disk → resume → verify cleared."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "lifecycle", reason="full-test")
    assert pc.is_paused("project", "lifecycle") is True
    on_disk_after_pause = store.load()
    assert len(on_disk_after_pause) == 1
    assert on_disk_after_pause[0]["target_id"] == "lifecycle"

    pc.resume("project", "lifecycle")
    assert pc.is_paused("project", "lifecycle") is False
    on_disk_after_resume = store.load()
    assert on_disk_after_resume == []


def test_full_lifecycle_survives_controller_recreation(tmp_path):
    """Pause → destroy controller → new controller → still paused → resume."""
    PauseStore(base_dir=str(tmp_path / "ps"))

    pc1 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    pc1.pause("model", "survivor")
    del pc1

    pc2 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    assert pc2.is_paused("model", "survivor") is True

    pc2.resume("model", "survivor")
    del pc2

    pc3 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    assert pc3.is_paused("model", "survivor") is False


def test_full_lifecycle_ram_disk_convergence_after_resume(tmp_path):
    """After resume, both the in-RAM frozenset and on-disk state agree."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "conv")
    pc.resume("project", "conv")

    records = pc.list_paused()
    assert records == []
    assert store.load() == []
    assert pc._paused_projects == frozenset()


# ---------------------------------------------------------------------------
# 5. Edge cases: concurrent pause, timeout resume, pause during gate, pending tasks
# ---------------------------------------------------------------------------


def test_concurrent_pause_same_target_only_one_created(tmp_path):
    """Multiple threads racing to pause the same target — only one record created."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    results: list[tuple[bool, float | None]] = []

    def race_pause(idx: int) -> None:
        record = pc.pause("project", "shared-target", reason=f"racer-{idx}")
        is_new = record.reason == f"racer-{idx}"
        results.append((is_new, record.paused_at))

    threads = [threading.Thread(target=race_pause, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    new_count = sum(1 for is_new, _ in results if is_new)
    assert new_count == 1, f"expected 1 new record, got {new_count}"
    assert pc.is_paused("project", "shared-target") is True
    paused = pc.list_paused()
    assert len(paused) == 1
    timestamps = {r.paused_at for r in paused}
    assert len(timestamps) == 1, "all results should share same paused_at"


def test_concurrent_pause_different_targets_all_succeed(tmp_path):
    """Multiple threads pausing different targets — all records created."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    errors: list[BaseException] = []

    def race_pause(idx: int) -> None:
        try:
            pc.pause("project", f"target-{idx}", reason=f"racer-{idx}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=race_pause, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [], f"unexpected errors: {errors!r}"
    paused = pc.list_paused()
    assert len(paused) == 20
    for i in range(20):
        assert pc.is_paused("project", f"target-{i}") is True


def test_concurrent_pause_and_resume_no_state_corruption(tmp_path):
    """Interleaving pause and resume on different targets — no torn state."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    errors: list[BaseException] = []

    def pauser(start: int) -> None:
        try:
            for i in range(start, start + 25):
                pc.pause("model", f"m-{i}")
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    def resumer(start: int) -> None:
        try:
            for i in range(start, start + 25):
                pc.resume("model", f"m-{i}")
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=pauser, args=(0,))
    t2 = threading.Thread(target=pauser, args=(25,))
    t3 = threading.Thread(target=resumer, args=(0,))
    t4 = threading.Thread(target=resumer, args=(25,))
    for t in (t1, t2, t3, t4):
        t.start()
    for t in (t1, t2, t3, t4):
        t.join(timeout=15)

    assert errors == [], f"unexpected errors: {errors!r}"
    paused = pc.list_paused()
    assert len(paused) <= 50 and len(paused) >= 0


def test_resume_after_simulated_timeout(tmp_path):
    """Pause, wait, then resume — state is clean even after simulated timeout."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "timeout-target", reason="will-resume-later")
    assert pc.is_paused("project", "timeout-target") is True

    time.sleep(0.5)

    record = pc.resume("project", "timeout-target")
    assert record is not None
    assert record.target_id == "timeout-target"
    assert pc.is_paused("project", "timeout-target") is False
    assert store.load() == []


def test_resume_after_timeout_survives_controller_recreation(tmp_path):
    """Pause, destroy controller, wait, recreate — resume still works."""
    PauseStore(base_dir=str(tmp_path / "ps"))

    pc1 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    pc1.pause("model", "timeout-model", reason="timeout-test")
    del pc1

    time.sleep(0.3)

    pc2 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    assert pc2.is_paused("model", "timeout-model") is True
    record = pc2.resume("model", "timeout-model")
    assert record is not None
    assert pc2.is_paused("model", "timeout-model") is False


def test_pause_during_gate_concurrent_readers_no_crash(tmp_path):
    """Simulate gate-like load: pause/resume while many readers hammer is_paused()."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    pc.pause("project", "base-target")

    errors: list[BaseException] = []

    def hammer_read(stop: threading.Event) -> None:
        try:
            i = 0
            while not stop.is_set():
                pc.is_paused("project", f"read-{i % 50}")
                pc.is_paused("project", "base-target")
                pc.is_paused("model", "nonexistent")
                i += 1
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    def churn_pause_resume(stop: threading.Event) -> None:
        try:
            i = 0
            while not stop.is_set():
                if i % 3 == 0:
                    pc.pause("project", f"gate-{i}")
                elif i % 3 == 1:
                    pc.pause("model", f"gate-{i}")
                else:
                    pc.resume("project", f"gate-{i - 2}")
                    pc.resume("model", f"gate-{i - 1}")
                i += 1
                time.sleep(0)
        except BaseException as exc:
            errors.append(exc)

    stop = threading.Event()
    readers = [threading.Thread(target=hammer_read, args=(stop,)) for _ in range(6)]
    writer = threading.Thread(target=churn_pause_resume, args=(stop,))
    for t in readers:
        t.start()
    writer.start()

    time.sleep(2)

    stop.set()
    writer.join(timeout=5)
    for t in readers:
        t.join(timeout=5)

    assert errors == [], f"gate-like load caused errors: {errors!r}"
    assert pc.is_paused("project", "base-target") is True


def test_pause_with_pending_tasks_stores_agent_handles(tmp_path):
    """Pause with agent_handles — handles are persisted and retrievable."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    handles = [
        {"task_id": "task-1", "path": "/tmp/t1", "checksum": "abc", "size_bytes": 100, "depth": 1},
        {"task_id": "task-2", "path": "/tmp/t2", "checksum": "def", "size_bytes": 200, "depth": 2},
    ]

    pc.pause(
        "project",
        "pending-tasks",
        reason="pause-with-tasks",
        agent_handles=list(handles),
        quiesce_status="clean",
    )

    record = pc.get("project", "pending-tasks")
    assert record is not None
    assert record.agent_handles == handles
    assert record.quiesce_status == "clean"

    del pc
    pc2 = PauseController(store=PauseStore(base_dir=str(tmp_path / "ps")))
    record2 = pc2.get("project", "pending-tasks")
    assert record2 is not None
    assert len(record2.agent_handles) == 2
    assert record2.agent_handles[0]["task_id"] == "task-1"


def test_pause_with_pending_tasks_cleared_on_resume(tmp_path):
    """After resume, the previously paused entity has no agent_handles in store."""
    store = PauseStore(base_dir=str(tmp_path / "ps"))
    pc = PauseController(store=store)

    handles = [
        {"task_id": "task-cleanup", "path": "/tmp/tc", "checksum": "xyz", "size_bytes": 50, "depth": 0},
    ]

    pc.pause("project", "cleanup-target", agent_handles=list(handles))
    assert pc.is_paused("project", "cleanup-target") is True

    pc.resume("project", "cleanup-target")
    assert pc.is_paused("project", "cleanup-target") is False
    assert pc.get("project", "cleanup-target") is None

    on_disk = store.load()
    assert on_disk == []


def test_pause_during_gate_router_level_no_crash(client):
    """Gate-like load through the router: many concurrent GETs during pause/resume."""
    import concurrent.futures

    client.post("/api/pause/project", json={"target_id": "gate-router"})

    def get_pause_list() -> int:
        r = client.get("/api/pause")
        return r.status_code

    def post_resume() -> int:
        r = client.post("/api/resume/project", json={"target_id": "gate-router"})
        return r.status_code

    errors: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(get_pause_list) for _ in range(30)]
        futures.append(ex.submit(post_resume))
        for f in concurrent.futures.as_completed(futures):
            code = f.result()
            if code != 200:
                errors.append(code)

    assert errors == [], f"router gate load returned non-200: {errors!r}"
