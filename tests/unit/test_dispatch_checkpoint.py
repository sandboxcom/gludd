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
        "project_id": "project-a",
        "queue": "core",
        "todo_version": 7,
        "resume_shard_id": "project-a:TODO-1",
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
        assert restored.project_id == "project-a"
        assert restored.resume_shard_id == "project-a:TODO-1"
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
        store_a.dehydrate(_snapshot_with_dispatch())

        with pytest.raises(IntegrityError):
            DurableHibernationStore(base_dir, key_file=key_b)

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

    def test_durable_store_replaces_invalid_key_length(self, tmp_path):
        key_file = tmp_path / "hibernation.key"
        key_file.write_bytes(b"short")

        DurableHibernationStore(tmp_path / "snaps", key_file=key_file)

        assert len(key_file.read_bytes()) == 32

    def test_durable_store_rejects_invalid_key_when_snapshots_exist(self, tmp_path):
        key_file = tmp_path / "hibernation.key"
        snapshots = tmp_path / "snaps"
        store = DurableHibernationStore(snapshots, key_file=key_file)
        manager = CheckpointManager(store)
        manager.checkpoint(
            _snapshot_with_dispatch(dispatch_state=_dispatch_state()),
            phase="pre_model",
        )
        key_file.write_bytes(b"lost-key")

        with pytest.raises(IntegrityError, match="prior checkpoints"):
            DurableHibernationStore(snapshots, key_file=key_file)


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

        mgr.clear(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
        )

        assert mgr.list_interrupted() == []
        # Clearing an already-cleared task is a no-op.
        mgr.clear(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
        )

    def test_same_todo_id_in_two_projects_has_distinct_checkpoints(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        project_a = _snapshot_with_dispatch(dispatch_state=_dispatch_state())
        project_b = _snapshot_with_dispatch(
            dispatch_state=_dispatch_state(
                project_id="project-b",
                resume_shard_id="project-b:TODO-1",
            )
        )

        mgr.checkpoint(project_a, phase="pre_model")
        mgr.checkpoint(project_b, phase="pre_model")

        interrupted = mgr.list_interrupted()
        assert {
            (snap.task_id, snap.dispatch_state.project_id)
            for snap in interrupted
            if snap.dispatch_state is not None
        } == {("TODO-1", "project-a"), ("TODO-1", "project-b")}

    def test_tampered_checkpoint_is_not_actionable(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        mgr.checkpoint(
            _snapshot_with_dispatch(dispatch_state=_dispatch_state()),
            phase="pre_model",
        )
        path = next(store.base_dir.glob("*.snapshot.json"))
        envelope = json.loads(path.read_text())
        payload = json.loads(envelope["payload"])
        payload["dispatch_state"]["project_id"] = "project-b"
        payload["dispatch_state"]["resume_shard_id"] = "project-b:TODO-1"
        envelope["payload"] = json.dumps(payload)
        path.write_text(json.dumps(envelope))

        assert mgr.list_interrupted() == []

    def test_no_checkpoints_no_resume(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        # Empty store: list_interrupted is a no-op-ish empty list.
        assert mgr.list_interrupted() == []

    def test_store_property_and_malformed_checkpoints_are_safe(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        mgr = CheckpointManager(store)
        assert mgr.store is store

        (store.base_dir / "bad-json.snapshot.json").write_text("{")
        (store.base_dir / "not-object.snapshot.json").write_text("[]")
        (store.base_dir / "bad-payload.snapshot.json").write_text(
            json.dumps({"payload": 7})
        )
        (store.base_dir / "bad-snapshot.snapshot.json").write_text(
            json.dumps({"payload": "{}"})
        )
        legacy = _snapshot_with_dispatch(dispatch_state=None)
        payload = legacy.model_dump_json()
        (store.base_dir / "legacy.snapshot.json").write_text(
            json.dumps({"payload": payload})
        )

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

    def test_resume_shard_has_one_durable_owner(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager_a = CheckpointManager(store)
        manager_b = CheckpointManager(store)
        snap = _snapshot_with_dispatch(dispatch_state=_dispatch_state())
        manager_a.checkpoint(snap, phase="pre_model")

        assert manager_a.claim_resume(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        assert not manager_b.claim_resume(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-b",
        )
        assert not manager_b.release_resume_claim(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-b",
        )
        assert manager_a.release_resume_claim(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        assert manager_b.claim_resume(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-b",
        )

    def test_clear_removes_resume_shard_claim(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)
        snap = _snapshot_with_dispatch(dispatch_state=_dispatch_state())
        manager.checkpoint(snap, phase="pre_model")
        assert manager.claim_resume(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )

        manager.clear(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
        )

        assert manager.claim_resume(
            snap.task_id,
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-b",
        )

    def test_resume_claim_refresh_is_scope_bound_and_stale_claim_is_replaced(
        self, tmp_path
    ):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)

        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        with pytest.raises(ValueError, match="resume shard"):
            manager.claim_resume(
                "TODO-1",
                project_id="project-a",
                shard_id="project-a:other",
                owner_id="writer-a",
            )

        claim_path = manager._resume_claim_path("project-a:TODO-1")
        claim_path.write_text(json.dumps({"owner_id": "dead", "expires_at": 0}))
        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-b",
        )

    def test_resume_claim_rejects_invalid_tokens_and_ttl(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)

        with pytest.raises(ValueError, match="task_id"):
            manager.claim_resume(
                "",
                project_id="project-a",
                shard_id="shard",
                owner_id="writer",
            )
        with pytest.raises(ValueError, match="ttl_seconds"):
            manager.claim_resume(
                "TODO-1",
                project_id="project-a",
                shard_id="project-a:TODO-1",
                owner_id="writer",
                ttl_seconds=True,
            )
        with pytest.raises(ValueError, match="resume shard"):
            manager.claim_resume(
                "TODO-1",
                project_id="project-a",
                shard_id="project-b:TODO-1",
                owner_id="writer",
            )

    def test_resume_claim_recovers_corrupt_record_and_release_missing(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)
        claim_path = manager._resume_claim_path("project-a:TODO-1")
        claim_path.write_text("{")

        assert not manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        claim_path.unlink()
        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id="project-a:TODO-1",
            owner_id="writer-a",
        )
        assert not manager.release_resume_claim(
            "missing",
            project_id="project-a",
            shard_id="project-a:missing",
            owner_id="writer-a",
        )

    def test_stale_owner_cannot_release_restarted_owner_claim(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)
        shard_id = "project-a:TODO-1"
        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id=shard_id,
            owner_id="writer-old",
        )
        manager._resume_claim_path(shard_id).write_text(
            json.dumps({"owner_id": "writer-old", "expires_at": 0})
        )
        assert manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id=shard_id,
            owner_id="writer-new",
        )

        assert not manager.release_resume_claim(
            "TODO-1",
            project_id="project-a",
            shard_id=shard_id,
            owner_id="writer-old",
        )
        assert not manager.claim_resume(
            "TODO-1",
            project_id="project-a",
            shard_id=shard_id,
            owner_id="writer-third",
        )

    def test_mark_resumed_without_bus_is_observable_noop(self, tmp_path, caplog):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)
        caplog.set_level("INFO", logger="general_ludd.agents.dispatch_checkpoint")

        manager.mark_resumed("TODO-1", phase="pre_model")

        assert "dispatch resumed (no bus)" in caplog.text


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

    def test_invalid_spool_sidecars_fail_safe(self, tmp_path):
        store = DurableHibernationStore(tmp_path / "snaps", key_file=tmp_path / "k")
        manager = CheckpointManager(store)
        sidecar = manager.spool_sidecar_path("TODO-1")

        sidecar.write_text("{")
        assert manager.read_spool_offset("TODO-1") is None
        sidecar.write_text(json.dumps({"offset": -1}))
        assert manager.read_spool_offset("TODO-1") is None


# --------------------------------------------------------------------------- #
# 7. SCHEMA_VERSION constant is bumped                                         #
# --------------------------------------------------------------------------- #
class TestSchemaVersion:
    def test_schema_version_is_2(self):
        assert SCHEMA_VERSION == 2
