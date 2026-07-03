"""SLICE 1 tests for the persistent PauseController + signed PauseStore.

Covers: restart survival (durable-keyed store), idempotent resume, fail-closed
integrity on tamper, mixed project/model listing, and degraded (no-key) mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.controllers.pause_controller import PauseController, PauseRecord
from general_ludd.controllers.pause_store import IntegrityError, PauseStore


def _store(tmp_path: Path) -> PauseStore:
    return PauseStore(base_dir=str(tmp_path / "pause"))


# ---------------------------------------------------------------------------
# Restart survival
# ---------------------------------------------------------------------------


def test_pause_survives_fresh_controller_via_durable_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    controller = PauseController(store=store)
    controller.pause("project", "p1", reason="maintenance")
    assert controller.is_paused("project", "p1") is True

    # A brand-new controller over the SAME store (simulating a restart) must
    # re-materialize the pause from disk — this only works if the MAC key is
    # durable, since load() fails closed on a key it cannot reproduce.
    fresh = PauseController(store=store)
    assert fresh.is_paused("project", "p1") is True
    record = fresh.get("project", "p1")
    assert record is not None
    assert record.reason == "maintenance"


def test_pause_survives_new_store_instance_same_base_dir(tmp_path: Path) -> None:
    base = str(tmp_path / "pause")
    first = PauseController(store=PauseStore(base_dir=base))
    first.pause("model", "gpt-x")

    # A completely fresh PauseStore pointed at the same base_dir reads the same
    # durable keyfile, so it can verify and load the prior state.
    second = PauseController(store=PauseStore(base_dir=base))
    assert second.is_paused("model", "gpt-x") is True


# ---------------------------------------------------------------------------
# Resume idempotency
# ---------------------------------------------------------------------------


def test_resume_clears_set_and_removes_record(tmp_path: Path) -> None:
    controller = PauseController(store=_store(tmp_path))
    controller.pause("project", "p1")

    removed = controller.resume("project", "p1")
    assert isinstance(removed, PauseRecord)
    assert removed.target_id == "p1"
    assert controller.is_paused("project", "p1") is False
    assert controller.get("project", "p1") is None


def test_double_resume_returns_none(tmp_path: Path) -> None:
    controller = PauseController(store=_store(tmp_path))
    controller.pause("project", "p1")
    assert controller.resume("project", "p1") is not None
    # Idempotent: resuming again is a no-op that reports "was not paused".
    assert controller.resume("project", "p1") is None


def test_resume_unknown_returns_none(tmp_path: Path) -> None:
    controller = PauseController(store=_store(tmp_path))
    assert controller.resume("model", "never-paused") is None


def test_resume_persists_so_restart_stays_resumed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    controller = PauseController(store=store)
    controller.pause("project", "p1")
    controller.resume("project", "p1")

    fresh = PauseController(store=store)
    assert fresh.is_paused("project", "p1") is False


# ---------------------------------------------------------------------------
# Fail-closed integrity
# ---------------------------------------------------------------------------


def test_tampered_payload_raises_integrity_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    controller = PauseController(store=store)
    controller.pause("project", "p1")

    # Tamper with the on-disk payload without re-signing (attacker lacks the
    # durable key). The MAC no longer matches -> load must fail closed.
    state_file = store.base_dir / "pause_state.json"
    original = state_file.read_text(encoding="utf-8")
    state_file.write_text(original.replace("p1", "p2"), encoding="utf-8")

    with pytest.raises(IntegrityError):
        store.load()


def test_missing_mac_with_key_raises_integrity_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    PauseController(store=store).pause("project", "p1")

    # A durable key exists but the MAC sidecar is gone: refuse (fail closed).
    (store.base_dir / "pause_state.json.mac").unlink()
    with pytest.raises(IntegrityError):
        store.load()


# ---------------------------------------------------------------------------
# Mixed listing / lookup
# ---------------------------------------------------------------------------


def test_list_paused_returns_project_and_model_records(tmp_path: Path) -> None:
    controller = PauseController(store=_store(tmp_path))
    controller.pause("project", "p1")
    controller.pause("model", "m1")

    records = controller.list_paused()
    assert len(records) == 2
    kinds = {(r.kind, r.target_id) for r in records}
    assert kinds == {("project", "p1"), ("model", "m1")}

    assert controller.is_paused("project", "p1") is True
    assert controller.is_paused("model", "m1") is True
    # A model and a project can share an id without colliding.
    assert controller.is_paused("model", "p1") is False
    assert controller.is_paused("project", "unpaused") is False


def test_pause_is_idempotent_and_preserves_paused_at(tmp_path: Path) -> None:
    controller = PauseController(store=_store(tmp_path))
    first = controller.pause("project", "p1", reason="first")
    again = controller.pause("project", "p1", reason="second")
    assert again.paused_at == first.paused_at
    assert again.reason == "first"
    assert len(controller.list_paused()) == 1


# ---------------------------------------------------------------------------
# Degraded (no durable key) mode
# ---------------------------------------------------------------------------


def test_degraded_mode_without_key_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    # Force degraded mode: no durable key could be established.
    monkeypatch.setattr(store, "_mac_key", None, raising=True)

    controller = PauseController(store=store)
    controller.pause("project", "p1")
    assert controller.is_paused("project", "p1") is True

    # load() must not crash and must return the records without verification;
    # no MAC sidecar is written in degraded mode.
    assert store.load() == [
        r.model_dump() for r in controller.list_paused()
    ]
    assert not (store.base_dir / "pause_state.json.mac").exists()

    fresh = PauseController(store=store)
    assert fresh.is_paused("project", "p1") is True
