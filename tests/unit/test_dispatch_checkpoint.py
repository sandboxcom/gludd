"""B3.1.5 — agent hydration/dehydration for crash-resume.

The Wave-13 design: snapshots used an ephemeral per-process MAC key, so a
writer crash abandoned every in-flight dispatch. The durable store keys the
MAC from a long-lived file so a fresh process can re-verify and rehydrate
snapshots written by its dead predecessor, and the dispatch lifecycle writes
checkpoint snapshots at three boundaries (pre-model, per-tool-iter,
clear-on-persist) so an interrupted dispatch resumes instead of silently
dropping.
"""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.agents.context import ContextMessage
from general_ludd.agents.dispatch_checkpoint import (
    CheckpointManager,
    DispatchState,
    DurableHibernationStore,
)
from general_ludd.agents.hibernation import (
    SCHEMA_VERSION,
    AgentEnvironmentSnapshot,
    HibernationStore,
    IntegrityError,
)
from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _dispatch_state(**overrides: object) -> DispatchState:
    base: dict[str, object] = {
        "todo_id": "TODO-1",
        "resolved_model_profile": "default",
        "resolved_prompt_profile": "coder",
        "prompt_text": "implement the thing",
        "phase_marker": "pre_model",
        "tool_iterations": 0,
        "accumulated_messages": [
            ContextMessage(role="user", content="go", token_estimate=1),
        ],
        "lease_holder_id": "writer-1",
    }
    base.update(overrides)
    return DispatchState.model_validate(base)


def _snapshot_with_dispatch(**overrides: object) -> AgentEnvironmentSnapshot:
    snap = AgentEnvironmentSnapshot(
        task_id="TODO-1",
        agent_name="coder",
        depth=2,
        messages=[
            ContextMessage(role="user", content="hi", token_estimate=1),
        ],
    )
    if "dispatch_state" in overrides:
        snap.dispatch_state = overrides["dispatch_state"]  # type: ignore[assignment]
    return snap


# --------------------------------------------------------------------------- #
# 1. DispatchState round-trip                                                  #
# --------------------------------------------------------------------------- #
class TestDispatchStateRoundTrip:
    def test_dispatch_state_round_trips(self):
        state = _dispatch_state()
        raw = state.model_dump_json()
        restored = DispatchState.model_validate_json(raw)
        assert restored == state
        assert restored.todo_id == "TODO-1"
        assert restored.phase_marker == "pre_model"
        assert restored.lease_holder_id == "writer-1"
        assert len(restored.accumulated_messages) == 1


# --------------------------------------------------------------------------- #
# 2. Snapshot v2 carries dispatch_state                                        #
# --------------------------------------------------------------------------- #
class TestSnapshotV2:
    def test_snapshot_v2_with_dispatch_state_round_trips(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _snapshot_with_dispatch(dispatch_state=_dispatch_state())

        handle = store.dehydrate(snap)
        restored = store.hydrate(handle)

        assert restored.dispatch_state is not None
        assert restored.dispatch_state.todo_id == "TODO-1"
        assert restored.dispatch_state.phase_marker == "pre_model"
        assert restored.schema_version == 2

    def test_schema_v1_snapshot_still_hydrates(self, tmp_path):
        # A v1 snapshot written before dispatch_state existed must hydrate
        # cleanly with dispatch_state=None.
        store = HibernationStore(tmp_path)
        v1_payload = {
            "task_id": "LEGACY-1",
            "agent_name": "coder",
            "parent_task_id": None,
            "invoker_name": "",
            "depth": 0,
            "workspace_path": "",
            "model_profile": None,
            "prompt_profile": None,
            "messages": [],
            "scratch": {},
            "created_at": 0.0,
            "schema_version": 1,
        }
        payload_str = json.dumps(v1_payload)
        envelope = {
            "schema_version": 1,
            "checksum": store._checksum(payload_str),
            "payload": payload_str,
        }
        path = store._path_for("LEGACY-1")
        path.write_text(json.dumps(envelope))

        from general_ludd.agents.hibernation import HibernationHandle

        handle = HibernationHandle(
            task_id="LEGACY-1",
            path=str(path),
            checksum=envelope["checksum"],
            size_bytes=100,
            depth=0,
        )
        restored = store.hydrate(handle)
        assert restored.task_id == "LEGACY-1"
        assert restored.dispatch_state is None
        assert restored.schema_version == 1


# --------------------------------------------------------------------------- #
# 3. DurableHibernationStore                                                   #
# --------------------------------------------------------------------------- #
class TestDurableStore:
    def test_durable_store_survives_restart(self, tmp_path):
        key_file = tmp_path / "hibernation.key"
        base_dir = tmp_path / "snapshots"

        live_store = DurableHibernationStore(base_dir, key_file=key_file)
        snap = _snapshot_with_dispatch(dispatch_state=_dispatch_state())
        handle = live_store.dehydrate(snap)

        # Simulate writer crash: discard the live store (ephemeral state gone)
        # and construct a fresh store from the same key file + snapshot dir.
        del live_store
        restarted = DurableHibernationStore(base_dir, key_file=key_file)
        restored = restarted.hydrate(handle)

        assert restored.task_id == "TODO-1"
        assert restored.dispatch_state is not None
        assert restored.dispatch_state.prompt_text == "implement the thing"

    def test_durable_store_rejects_wrong_key(self, tmp_path):
        key_a = tmp_path / "a.key"
        key_b = tmp_path / "b.key"
        base_dir = tmp_path / "snapshots"

        store_a = DurableHibernationStore(base_dir, key_file=key_a)
        handle = store_a.dehydrate(_snapshot_with_dispatch())

        store_b = DurableHibernationStore(base_dir, key_file=key_b)
        with pytest.raises(IntegrityError):
            store_b.hydrate(handle)

    def test_durable_key_file_created_with_0600_perms(self, tmp_path):
        key_file = tmp_path / "subdir" / "hibernation.key"
        DurableHibernationStore(tmp_path / "snapshots", key_file=key_file)

        assert key_file.exists()
        if os.name == "posix":
            mode = stat.S_IMODE(key_file.stat().st_mode)
            assert mode == 0o600

    def test_durable_store_reuses_existing_key(self, tmp_path):
        key_file = tmp_path / "hibernation.key"
        s1 = DurableHibernationStore(tmp_path / "snaps", key_file=key_file)
        key_bytes_first = key_file.read_bytes()
        s2 = DurableHibernationStore(tmp_path / "snaps", key_file=key_file)
        # Same file contents — the second constructor MUST NOT regenerate.
        assert key_file.read_bytes() == key_bytes_first
        # And a snapshot from s1 hydrates under s2 (same key).
        handle = s1.dehydrate(_snapshot_with_dispatch())
        assert s2.hydrate(handle).task_id == "TODO-1"


# --------------------------------------------------------------------------- #
# 4. CheckpointManager — dehydrate/hydrate/list/clear                          #
# --------------------------------------------------------------------------- #
class TestCheckpointManager:
    def test_checkpoint_written_pre_model_call(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch(
            dispatch_state=_dispatch_state(phase_marker="pre_model"),
        )

        mgr.checkpoint(snap, phase="pre_model")

        interrupted = mgr.list_interrupted()
        assert len(interrupted) == 1
        restored = interrupted[0]
        assert restored.dispatch_state is not None
        assert restored.dispatch_state.phase_marker == "pre_model"

    def test_checkpoint_updated_per_tool_iteration(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch(
            dispatch_state=_dispatch_state(phase_marker="pre_model"),
        )
        mgr.checkpoint(snap, phase="pre_model")

        # Advance: model returned, we're now mid tool loop, iteration 2.
        snap.dispatch_state.phase_marker = "mid_tool_loop"
        snap.dispatch_state.tool_iterations = 2
        mgr.checkpoint(snap, phase="mid_tool_loop")

        restored = mgr.list_interrupted()[0]
        assert restored.dispatch_state.phase_marker == "mid_tool_loop"
        assert restored.dispatch_state.tool_iterations == 2

    def test_checkpoint_cleared_on_successful_persist(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch(dispatch_state=_dispatch_state())
        mgr.checkpoint(snap, phase="pre_model")
        assert mgr.list_interrupted()

        mgr.clear(snap.task_id)

        assert mgr.list_interrupted() == []
        # Clearing an already-cleared task is a no-op.
        mgr.clear(snap.task_id)

    def test_no_checkpoints_no_resume(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        # Empty store: list_interrupted is a no-op-ish empty list.
        assert mgr.list_interrupted() == []


# --------------------------------------------------------------------------- #
# 5. Resume behavior                                                           #
# --------------------------------------------------------------------------- #
class TestResume:
    def test_resume_skips_already_completed(self, tmp_path):
        """If the todo is COMPLETED in the DB, resume must skip it — the
        dispatch already finished, the checkpoint is stale."""
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch()
        mgr.checkpoint(snap, phase="pre_model")

        # DB says COMPLETED.
        todo_repo = MagicMock()
        completed_todo = MagicMock()
        completed_todo.status = "COMPLETED"
        completed_todo.todo_id = snap.task_id
        todo_repo.get_by_id = AsyncMock(return_value=completed_todo)

        resumed = mgr.list_interrupted()
        # Manager offers no actionable resumes when the only candidate is done.
        # filter_actionable_sync is the pure-sync helper: caller pre-resolves
        # per-todo status and passes a {todo_id: status} map.
        actionable = mgr.filter_actionable_sync(
            resumed, statuses={snap.task_id: "COMPLETED"}
        )
        assert actionable == []

    def test_resume_emits_observability_event(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        bus = EventBus()
        mgr = CheckpointManager(store, event_bus=bus)
        snap = _snapshot_with_dispatch()
        mgr.checkpoint(snap, phase="pre_model")

        events: list[Event] = []
        bus.subscribe("dispatch_resumed", events.append)

        mgr.mark_resumed(snap.task_id, phase="pre_model")

        assert len(events) == 1
        assert events[0].type == "dispatch_resumed"
        assert events[0].payload["todo_id"] == snap.task_id
        assert events[0].payload["phase"] == "pre_model"

    def test_bucket_lease_reacquired_on_resume(self, tmp_path):
        """A resumed dispatch carries lease_holder_id; resume reports it so the
        caller can re-acquire the bucket lease before re-running."""
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch(
            dispatch_state=_dispatch_state(
                lease_holder_id="writer-restarted-2",
            ),
        )
        mgr.checkpoint(snap, phase="pre_model")

        interrupted = mgr.list_interrupted()[0]
        assert interrupted.dispatch_state is not None
        # The lease holder is preserved on the snapshot, so the resume path can
        # re-acquire the lease for that holder before re-running.
        assert interrupted.dispatch_state.lease_holder_id == "writer-restarted-2"


# --------------------------------------------------------------------------- #
# 6. Spool offset sidecar                                                      #
# --------------------------------------------------------------------------- #
class TestSpoolSidecar:
    def test_spool_offset_persisted_to_sidecar(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch()

        # Simulate the writer child having drained the inbound spool up to
        # offset 4096; that offset must persist so a restarted child does not
        # re-apply envelopes 0..4095.
        mgr.write_spool_offset(snap.task_id, offset=4096)

        sidecar = mgr.spool_sidecar_path(snap.task_id)
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["offset"] == 4096

    def test_spool_offset_recovered_on_boot(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        snap = _snapshot_with_dispatch()
        mgr.write_spool_offset(snap.task_id, offset=2048)

        # A brand-new manager over the same base dir reads the sidecar.
        mgr2 = CheckpointManager(store)
        recovered = mgr2.read_spool_offset(snap.task_id)
        assert recovered == 2048

        # Missing sidecar → None (caller starts at offset 0).
        assert mgr2.read_spool_offset("UNKNOWN-TODO") is None


# --------------------------------------------------------------------------- #
# 7. SCHEMA_VERSION constant is bumped                                         #
# --------------------------------------------------------------------------- #
class TestSchemaVersion:
    def test_schema_version_is_2(self):
        assert SCHEMA_VERSION == 2
