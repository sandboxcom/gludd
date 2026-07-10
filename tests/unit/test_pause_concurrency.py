"""Concurrency tests for PauseController's lock-free is_paused() (D7.1).

``is_paused()`` is read without ``self._lock`` (it's a hot path called from
the gateway / dispatcher / event loop). Safety depends on the membership sets
being copy-on-write frozensets that are only ever REBOUND (never mutated in
place) under the lock, after a successful persist — see
``pause_controller.py``'s ``_rebind_add`` / ``_rebind_discard``.

This test hammers ``is_paused()`` from several reader threads while a writer
thread churns pause()/resume() on overlapping keys, and asserts:

  * no exception ever escapes a reader (no torn-set access, no KeyError, etc),
  * once all threads finish, the final membership frozensets agree exactly
    with ``list_paused()`` (the lock-guarded, authoritative view) — i.e. the
    lock-free hot path and the locked authoritative path never diverge.

NOTE on sizing: reader threads run a BOUNDED number of passes (not an
open-ended ``while True`` spin) and yield (``time.sleep(0)``) between passes.
An earlier version of this test used unbounded busy-spin readers, which
starved the writer thread of GIL time badly enough to blow past pytest's
global timeout — a scheduling artifact of the test harness, not a bug in the
controller. Bounded + yielding readers give deterministic, fast termination
while still exercising the same lock-free read path under real contention.
"""

from __future__ import annotations

import threading
import time

import pytest

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore

N_READER_THREADS = 4
N_TARGETS = 3
WRITER_ITERATIONS = 25
READ_PASSES_PER_READER = 300


@pytest.fixture
def controller(tmp_path):
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    return PauseController(store=store)


def _target_ids(prefix: str, n: int) -> list[str]:
    return [f"{prefix}-{i}" for i in range(n)]


def test_concurrent_is_paused_reads_survive_writer_churn(controller):
    project_ids = _target_ids("proj", N_TARGETS)
    model_ids = _target_ids("model", N_TARGETS)

    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def reader() -> None:
        try:
            for _ in range(READ_PASSES_PER_READER):
                for pid in project_ids:
                    controller.is_paused("project", pid)
                for mid in model_ids:
                    controller.is_paused("model", mid)
                # Explicit yield point: bounds how long a tight CPU loop can
                # monopolize the GIL, so the writer's file I/O keeps making
                # progress even on a heavily loaded / low-core-count box.
                time.sleep(0)
        except BaseException as exc:  # must capture EVERYTHING, not just Exception
            with errors_lock:
                errors.append(exc)

    def writer() -> None:
        try:
            for i in range(WRITER_ITERATIONS):
                pid = project_ids[i % N_TARGETS]
                mid = model_ids[i % N_TARGETS]
                controller.pause("project", pid, reason=f"churn-{i}")
                controller.pause("model", mid, reason=f"churn-{i}")
                # Churn: resume every other pass so add/remove interleave.
                if i % 2 == 0:
                    controller.resume("project", pid)
                    controller.resume("model", mid)
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(N_READER_THREADS)]
    writer_thread = threading.Thread(target=writer)

    for t in readers:
        t.start()
    writer_thread.start()

    writer_thread.join(timeout=60)
    assert not writer_thread.is_alive(), "writer thread did not finish in time"
    for t in readers:
        t.join(timeout=60)
        assert not t.is_alive(), "reader thread did not finish in time"

    assert errors == [], f"reader/writer threads raised: {errors!r}"

    # Final-state consistency: the lock-free membership frozensets must agree
    # EXACTLY with the lock-guarded authoritative list_paused() view.
    records = controller.list_paused()
    paused_project_ids = {r.target_id for r in records if r.kind == "project"}
    paused_model_ids = {r.target_id for r in records if r.kind == "model"}

    assert controller._paused_projects == frozenset(paused_project_ids)
    assert controller._paused_models == frozenset(paused_model_ids)

    # And is_paused() must agree, target by target, with that same view.
    for pid in project_ids:
        assert controller.is_paused("project", pid) == (pid in paused_project_ids)
    for mid in model_ids:
        assert controller.is_paused("model", mid) == (mid in paused_model_ids)


def test_concurrent_pause_resume_no_lost_updates_across_disjoint_keys(controller):
    """A stronger check: N writer threads each own a DISJOINT key and
    pause/resume it repeatedly with no other thread touching that key. Since
    keys are disjoint, every writer's final state must be deterministic
    (paused, since the last iteration is odd and leaves it paused), proving
    the persist-then-rebind discipline never loses or duplicates an update
    under real concurrent access (not just single-writer churn).
    """
    n_writers = 4
    iterations = 20
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def own_writer(idx: int) -> None:
        target = f"disjoint-{idx}"
        try:
            for i in range(iterations):
                controller.pause("project", target, reason=f"iter-{i}")
                if i % 2 == 0:
                    controller.resume("project", target)
                # else: leave paused this round -> re-pause is idempotent next loop
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=own_writer, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "writer thread did not finish in time"

    assert errors == [], f"writer threads raised: {errors!r}"

    # Every disjoint target must be in its final state (paused, since the
    # LAST iteration index is iterations-1 which is odd -> not resumed after
    # the last pause) both in the hot-path set and the authoritative list.
    records = controller.list_paused()
    paused_ids = {r.target_id for r in records}
    for idx in range(n_writers):
        target = f"disjoint-{idx}"
        assert target in paused_ids
        assert controller.is_paused("project", target) is True
    assert controller._paused_projects == frozenset(paused_ids)
