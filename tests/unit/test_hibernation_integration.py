"""Integration tests for HibernationController: hibernate→resume cycle, corrupt MAC, concurrent.

Tests the full lifecycle: dehydrate via controller.parked() → verify snapshot
on disk → handle exists → rehydrate → verify context survives round-trip.
Also tests tamper detection and concurrent async operations.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from general_ludd.agents.context import ContextMessage
from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationStore,
    IntegrityError,
)


def _make_snap(
    task_id: str = "INTEG-TEST",
    depth: int = 3,
    messages: list[ContextMessage] | None = None,
) -> AgentEnvironmentSnapshot:
    if messages is None:
        messages = [
            ContextMessage(
                role="user",
                content="integration test message",
                token_estimate=5,
                is_system=False,
                timestamp=1.0,
            ),
            ContextMessage(
                role="assistant",
                content="response from assistant",
                token_estimate=5,
                is_system=False,
                timestamp=2.0,
            ),
            ContextMessage(
                role="user",
                content="third message",
                token_estimate=3,
                is_system=False,
                timestamp=3.0,
            ),
            ContextMessage(
                role="user",
                content="fourth",
                token_estimate=1,
                is_system=False,
                timestamp=4.0,
            ),
            ContextMessage(
                role="user",
                content="fifth",
                token_estimate=1,
                is_system=False,
                timestamp=5.0,
            ),
            ContextMessage(
                role="assistant",
                content="sixth msg",
                token_estimate=2,
                is_system=False,
                timestamp=6.0,
            ),
            ContextMessage(
                role="user",
                content="seventh",
                token_estimate=1,
                is_system=False,
                timestamp=7.0,
            ),
            ContextMessage(
                role="user",
                content="eighth long message for depth",
                token_estimate=6,
                is_system=False,
                timestamp=8.0,
            ),
        ]
    return AgentEnvironmentSnapshot(
        task_id=task_id,
        agent_name="coder",
        depth=depth,
        workspace_path="/tmp/integration",
        messages=messages,
    )


class TestHibernateResumeCycle:
    def test_full_parked_cycle_dehydrates_and_rehydrates(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=2, min_context_messages=4
        )
        snap = _make_snap("TASK-CYCLE")

        async def _run():
            async with controller.parked(snap) as parked:
                assert parked.dehydrated
                assert parked.handle is not None
                assert parked.handle.task_id == "TASK-CYCLE"
                path = Path(parked.handle.path)
                assert path.exists()
                assert path.read_text(encoding="utf-8")
            assert parked.snapshot is not None
            assert parked.snapshot.task_id == "TASK-CYCLE"
            assert parked.snapshot.agent_name == "coder"
            assert len(parked.snapshot.messages) == 8
            assert parked.snapshot.messages[0].content == "integration test message"
            assert parked.snapshot.messages[-1].content == "eighth long message for depth"
            assert not path.exists()

        asyncio.run(_run())

    def test_parked_dehydrated_drops_original_reference(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=2, min_context_messages=4
        )
        snap = _make_snap("TASK-DROPREF")

        async def _run():
            async with controller.parked(snap) as parked:
                assert parked.dehydrated
                assert parked._original is None
            assert parked.snapshot is not None
            assert parked.snapshot.task_id == "TASK-DROPREF"

        asyncio.run(_run())

    def test_parked_skips_dehydrate_keeps_original(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=5, min_context_messages=4
        )
        snap = _make_snap("TASK-KEEPORIG", depth=2)

        async def _run():
            async with controller.parked(snap) as parked:
                assert not parked.dehydrated
                assert parked._original is not None
            assert parked.snapshot is not None
            assert parked.snapshot.task_id == "TASK-KEEPORIG"

        asyncio.run(_run())

    def test_shallow_snap_skips_dehydrate(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=5, min_context_messages=4
        )
        snap = _make_snap("TASK-SHALLOW", depth=2)

        async def _run():
            async with controller.parked(snap) as parked:
                assert not parked.dehydrated
                assert parked.handle is None
            assert parked.snapshot is not None
            assert parked.snapshot.task_id == "TASK-SHALLOW"

        asyncio.run(_run())

    def test_few_messages_skips_dehydrate(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=2, min_context_messages=20
        )
        snap = _make_snap("TASK-FEWMSG")

        async def _run():
            async with controller.parked(snap) as parked:
                assert not parked.dehydrated
            assert parked.snapshot is not None

        asyncio.run(_run())

    def test_discard_removes_snapshot_file(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-DISCARD")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        assert path.exists()
        store.discard(handle)
        assert not path.exists()

    def test_discard_missing_file_no_error(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-DISCARD2")
        handle = store.dehydrate(snap)
        store.discard(handle)
        store.discard(handle)


class TestCorruptMac:
    def test_tampered_payload_rejected(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-TAMPER")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        raw = path.read_text(encoding="utf-8")
        envelope = json.loads(raw)
        envelope["payload"] = '{"task_id":"evil","agent_name":"attacker","messages":[]}'
        path.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(IntegrityError, match="checksum mismatch"):
            store.hydrate(handle)

    def test_tampered_envelope_checksum_rejected(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-TAMPER2")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        raw = path.read_text(encoding="utf-8")
        envelope = json.loads(raw)
        envelope["checksum"] = "a" * 64
        path.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(IntegrityError, match="checksum mismatch"):
            store.hydrate(handle)

    def test_handle_checksum_mismatch_rejected(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-HANDLETAMPER")
        handle = store.dehydrate(snap)
        from general_ludd.agents.hibernation import HibernationHandle

        fake_handle = HibernationHandle(
            task_id=handle.task_id,
            path=handle.path,
            checksum="f" * 64,
            size_bytes=handle.size_bytes,
            depth=handle.depth,
        )
        with pytest.raises(IntegrityError, match="checksum mismatch"):
            store.hydrate(fake_handle)

    def test_non_json_envelope_rejected(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-NONJSON")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        path.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(IntegrityError, match="not valid JSON"):
            store.hydrate(handle)

    def test_missing_payload_field_rejected(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("TASK-BADENV")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        path.write_text('{"checksum": "abc", "schema_version": 2}', encoding="utf-8")
        with pytest.raises(IntegrityError, match="malformed snapshot"):
            store.hydrate(handle)

    def test_path_traversal_sanitized_to_safe_name(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("../../etc/passwd")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        assert path.parent == tmp_path.resolve()
        assert ".." not in path.name
        assert "/" not in path.name
        assert path.exists()

    def test_path_with_special_chars_sanitized(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("task with spaces/and\0nulls")
        handle = store.dehydrate(snap)
        path = Path(handle.path)
        assert path.parent == tmp_path.resolve()
        assert " " not in path.name
        assert "/" not in path.name
        assert path.exists()


class TestConcurrentRequests:
    def test_concurrent_dehydrate_hydrate_distinct_tasks(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        num_tasks = 20
        snaps = [_make_snap(f"CONC-{i:03d}") for i in range(num_tasks)]

        async def _cycle(snap: AgentEnvironmentSnapshot):
            handle = await store.dehydrate_async(snap)
            restored = await store.hydrate_async(handle)
            return restored

        async def _run():
            results = await asyncio.gather(
                *(_cycle(snap) for snap in snaps)
            )
            return results

        restored = asyncio.run(_run())
        assert len(restored) == num_tasks
        for i, snap in enumerate(restored):
            assert snap.task_id == f"CONC-{i:03d}"
            assert snap.agent_name == "coder"
            assert len(snap.messages) == 8

    def test_concurrent_parked_cycles_independent(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        controller = HibernationController(
            store, min_depth=2, min_context_messages=4
        )
        num_tasks = 10
        snaps = [_make_snap(f"PARK-{i:03d}") for i in range(num_tasks)]

        async def _parked_cycle(snap: AgentEnvironmentSnapshot):
            async with controller.parked(snap) as parked:
                assert parked.dehydrated
                handle_path = Path(parked.handle.path)
                assert handle_path.exists()
            assert parked.snapshot is not None
            assert parked.snapshot.task_id == snap.task_id
            assert not handle_path.exists()
            return parked.snapshot

        async def _run():
            return await asyncio.gather(
                *(_parked_cycle(snap) for snap in snaps)
            )

        results = asyncio.run(_run())
        assert len(results) == num_tasks
        ids = {r.task_id for r in results}
        assert len(ids) == num_tasks
        for i in range(num_tasks):
            assert f"PARK-{i:03d}" in ids

    def test_concurrent_reads_same_snapshot(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        snap = _make_snap("CONC-SAME")
        handle = store.dehydrate(snap)

        async def _read():
            return await store.hydrate_async(handle)

        async def _run():
            return await asyncio.gather(*(_read() for _ in range(10)))

        results = asyncio.run(_run())
        for r in results:
            assert r.task_id == "CONC-SAME"
            assert len(r.messages) == 8

    def test_concurrent_dehydrate_same_task_id_races(self, tmp_path: Path):
        store = HibernationStore(base_dir=str(tmp_path))
        num_copies = 10
        snaps = [_make_snap("CONC-SAMEID") for _ in range(num_copies)]

        async def _write(snap: AgentEnvironmentSnapshot):
            return await store.dehydrate_async(snap)

        async def _run():
            return await asyncio.gather(
                *(_write(snap) for snap in snaps), return_exceptions=True
            )

        results = asyncio.run(_run())
        success = sum(1 for r in results if not isinstance(r, BaseException))
        assert success >= 1
        handles = [r for r in results if not isinstance(r, BaseException)]
        restored = store.hydrate(handles[0])
        assert restored.task_id == "CONC-SAMEID"
