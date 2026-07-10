"""Persist-before-mutate ordering tests for PauseController (D7.1).

These tests exercise the failure-mode discipline described in
``controllers/pause_controller.py``: ``pause()`` / ``resume()`` must write the
candidate record set to the store BEFORE touching any in-RAM state. A store
failure must therefore:

  * propagate (raise) out of pause()/resume(), AND
  * leave is_paused()/list_paused()/get() reporting exactly the prior durable
    state — never a partially-applied mutation, AND
  * be invisible to a FRESH controller built on the same store (nothing was
    ever durably written).

A "crash-window" test additionally proves RAM/disk convergence: writing
directly to the store (bypassing the controller, simulating "persist
succeeded") and then constructing a fresh controller on that store converges
to the persisted state, matching what a real process restart would observe.
"""

from __future__ import annotations

import pytest

from general_ludd.controllers.pause_controller import PauseController, PauseRecord
from general_ludd.controllers.pause_store import PauseStore


class _FailingSaveStore:
    """A PauseStore look-alike whose save() always raises.

    Delegates load() to a real, empty (or pre-seeded) PauseStore instance so
    the fresh-controller-sees-nothing-durable assertions are checking real
    on-disk state, not merely an in-memory fake.
    """

    def __init__(self, real_store: PauseStore) -> None:
        self._real = real_store
        self.save_calls = 0

    def save(self, records: list[dict[str, object]]) -> None:
        self.save_calls += 1
        raise RuntimeError("simulated disk failure on save()")

    def load(self) -> list[dict[str, object]]:
        return self._real.load()


@pytest.fixture
def real_store(tmp_path):
    return PauseStore(base_dir=str(tmp_path / "pause_store"))


# ---------------------------------------------------------------------------
# pause() failure direction
# ---------------------------------------------------------------------------


def test_pause_raises_when_store_save_fails(real_store):
    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="simulated disk failure"):
        pc.pause("project", "proj-fail")

    assert failing.save_calls == 1


def test_pause_failure_leaves_is_paused_false(real_store):
    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        pc.pause("project", "proj-fail")

    assert pc.is_paused("project", "proj-fail") is False


def test_pause_failure_leaves_list_paused_empty(real_store):
    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        pc.pause("model", "model-fail")

    assert pc.list_paused() == []
    assert pc.get("model", "model-fail") is None


def test_pause_failure_not_durable_fresh_controller_sees_nothing(real_store):
    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        pc.pause("project", "proj-fail")

    # A fresh controller on the REAL (working) store must see nothing: the
    # failed save() never durably wrote the candidate record.
    fresh = PauseController(store=real_store)
    assert fresh.is_paused("project", "proj-fail") is False
    assert fresh.list_paused() == []


# ---------------------------------------------------------------------------
# resume() failure direction — mirror of pause(): stays paused (RAM + disk)
# ---------------------------------------------------------------------------


def test_resume_raises_when_store_save_fails(real_store):
    # Establish a genuinely paused, durable baseline via a WORKING controller.
    baseline = PauseController(store=real_store)
    baseline.pause("project", "proj-resume-fail")
    del baseline

    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]
    assert pc.is_paused("project", "proj-resume-fail") is True  # loaded from disk

    with pytest.raises(RuntimeError, match="simulated disk failure"):
        pc.resume("project", "proj-resume-fail")


def test_resume_failure_stays_paused_in_ram(real_store):
    baseline = PauseController(store=real_store)
    baseline.pause("model", "model-resume-fail")
    del baseline

    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        pc.resume("model", "model-resume-fail")

    assert pc.is_paused("model", "model-resume-fail") is True
    records = pc.list_paused()
    assert len(records) == 1
    assert records[0].target_id == "model-resume-fail"


def test_resume_failure_stays_paused_on_disk(real_store):
    baseline = PauseController(store=real_store)
    baseline.pause("project", "proj-resume-fail-disk")
    del baseline

    failing = _FailingSaveStore(real_store)
    pc = PauseController(store=failing)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        pc.resume("project", "proj-resume-fail-disk")

    # A fresh controller on the REAL store must ALSO see it still paused: the
    # failed save() never durably cleared the record.
    fresh = PauseController(store=real_store)
    assert fresh.is_paused("project", "proj-resume-fail-disk") is True


# ---------------------------------------------------------------------------
# Crash-window equivalence: save-succeeds-then-crash converges on restart
# ---------------------------------------------------------------------------


def test_crash_window_pause_converges_to_paused_on_restart(real_store):
    """Simulates a crash exactly after persist() succeeds but before the
    in-RAM update — by writing directly to the store, bypassing the
    controller entirely (equivalent to "save succeeded, process died before
    self._records was rebound"). A FRESH controller (the "restart") must
    converge to the durable (paused) state.
    """
    record = PauseRecord(kind="project", target_id="proj-crash", paused_at=1.0)
    real_store.save([record.model_dump()])

    fresh = PauseController(store=real_store)
    assert fresh.is_paused("project", "proj-crash") is True
    assert fresh.get("project", "proj-crash") is not None


def test_crash_window_resume_converges_to_resumed_on_restart(real_store):
    """Mirror of the pause crash-window test: a save() that clears the record
    (simulating resume()'s persist succeeding) must converge a fresh
    controller to "resumed" even though the same-process in-RAM state was
    never touched (because that process is gone — it's the fresh instance's
    job to converge)."""
    baseline = PauseController(store=real_store)
    baseline.pause("model", "model-crash")
    del baseline

    # Simulate resume()'s successful save() (candidate with the record
    # removed) directly against the store, bypassing any controller.
    real_store.save([])

    fresh = PauseController(store=real_store)
    assert fresh.is_paused("model", "model-crash") is False
    assert fresh.list_paused() == []
