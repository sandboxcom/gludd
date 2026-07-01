"""Tests for agent-environment hibernation (dehydrate/hydrate for deep recursion).

Covers: JSON round-trip fidelity, the tiny-handle memory reclaim, SHA-256
integrity/tamper rejection, path-traversal jailing, the ``parked()`` policy
context manager, and a nested deep-recursion unwind where each ancestor is
dehydrated while its descendant runs and rehydrated on the way back up.
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
    HibernationError,
    HibernationStore,
    IntegrityError,
    messages_from_dicts,
)


def _make_messages(n: int) -> list[ContextMessage]:
    return [
        ContextMessage(
            role="user" if i % 2 else "assistant",
            content=f"message-{i} " + ("x" * 200),
            token_estimate=50,
            is_system=False,
            timestamp=float(i),
        )
        for i in range(n)
    ]


def _make_snapshot(
    task_id: str = "TASK-1",
    depth: int = 5,
    n_messages: int = 12,
) -> AgentEnvironmentSnapshot:
    return AgentEnvironmentSnapshot(
        task_id=task_id,
        agent_name="coder",
        parent_task_id="TASK-0",
        invoker_name="primary",
        depth=depth,
        workspace_path="/tmp/gludd-workspace",
        model_profile="default",
        prompt_profile="coder",
        messages=_make_messages(n_messages),
        scratch={"cursor": "line-42", "goal": "implement feature"},
        created_at=123.0,
    )


# --------------------------------------------------------------------------- #
# Round-trip fidelity                                                          #
# --------------------------------------------------------------------------- #


class TestRoundTrip:
    def test_dehydrate_hydrate_is_faithful(self, tmp_path):
        store = HibernationStore(tmp_path)
        original = _make_snapshot()

        handle = store.dehydrate(original)
        restored = store.hydrate(handle)

        assert restored == original
        assert restored.messages == original.messages
        assert restored.scratch == original.scratch
        assert restored.depth == original.depth

    def test_snapshot_file_lands_under_base_dir(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())
        assert handle.path.startswith(str(store.base_dir))
        assert Path(handle.path).exists()

    def test_empty_context_round_trips(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _make_snapshot(n_messages=0)
        assert store.hydrate(store.dehydrate(snap)) == snap


# --------------------------------------------------------------------------- #
# Memory reclaim — the handle is tiny relative to the dehydrated context       #
# --------------------------------------------------------------------------- #


class TestHandleIsLightweight:
    def test_handle_is_far_smaller_than_context(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _make_snapshot(n_messages=200)  # large context

        handle = store.dehydrate(snap)
        # The in-RAM handle is a few fixed fields; its JSON form must be orders
        # of magnitude smaller than the on-disk context it points at.
        handle_bytes = len(handle.model_dump_json())
        assert handle_bytes < 512
        assert handle.size_bytes > handle_bytes * 20


# --------------------------------------------------------------------------- #
# Integrity / tamper rejection                                                 #
# --------------------------------------------------------------------------- #


class TestIntegrity:
    def test_tampered_payload_is_rejected(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())

        # Rewrite the payload AND recompute a matching envelope checksum — a
        # naive integrity scheme would accept this.  The trusted handle checksum
        # must still reject it.
        path = Path(handle.path)
        forged = _make_snapshot(task_id="TASK-1")
        forged.scratch["injected"] = "evil"
        payload = forged.model_dump_json()
        import hashlib

        forged_sum = hashlib.sha256(payload.encode()).hexdigest()
        path.write_text(
            json.dumps(
                {"schema_version": 1, "checksum": forged_sum, "payload": payload}
            )
        )

        with pytest.raises(IntegrityError):
            store.hydrate(handle)

    def test_corrupt_json_is_rejected(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())
        Path(handle.path).write_text("{not json")
        with pytest.raises(IntegrityError):
            store.hydrate(handle)

    def test_missing_file_raises_hibernation_error(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())
        store.discard(handle)
        with pytest.raises(HibernationError):
            store.hydrate(handle)


# --------------------------------------------------------------------------- #
# Path-traversal jailing                                                       #
# --------------------------------------------------------------------------- #


class TestPathJail:
    def test_traversal_task_id_is_confined(self, tmp_path):
        from pathlib import Path

        store = HibernationStore(tmp_path)
        snap = _make_snapshot(task_id="../../etc/passwd")

        handle = store.dehydrate(snap)

        # The dangerous id is sanitized to a filename that stays directly under
        # base; nothing is written outside the jail...
        written = Path(handle.path).resolve()
        assert written.parent == store.base_dir
        assert ".." not in written.name
        assert not (tmp_path.parent.parent / "etc" / "passwd").exists()
        # ...and the original id still round-trips faithfully in the payload.
        assert store.hydrate(handle).task_id == "../../etc/passwd"

    def test_sanitizer_collision_ids_do_not_clobber(self, tmp_path):
        # "a/b" and "a_b" both sanitize to the same stem "a_b"; without a
        # RAW-id hash suffix they would share one file and silently clobber.
        store = HibernationStore(tmp_path)
        snap_slash = _make_snapshot(task_id="a/b")
        snap_under = _make_snapshot(task_id="a_b")

        h_slash = store.dehydrate(snap_slash)
        h_under = store.dehydrate(snap_under)

        # Distinct files on disk — no clobber.
        assert h_slash.path != h_under.path
        assert Path(h_slash.path).exists()
        assert Path(h_under.path).exists()
        assert len(list(store.base_dir.glob("*.snapshot.json"))) == 2

        # Both hydrate back to their own original task_id.
        assert store.hydrate(h_slash).task_id == "a/b"
        assert store.hydrate(h_under).task_id == "a_b"


# --------------------------------------------------------------------------- #
# messages_from_dicts bridge                                                   #
# --------------------------------------------------------------------------- #


class TestMessageBridge:
    def test_converts_raw_dicts_and_drops_extras(self, tmp_path):
        raw = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi", "timestamp": 9.0},
            {"role": "assistant", "content": "hello", "raw_response": object()},
        ]
        msgs = messages_from_dicts(raw)
        assert [m.role for m in msgs] == ["system", "user", "assistant"]
        assert msgs[0].is_system is True
        assert msgs[1].timestamp == 9.0
        assert msgs[2].content == "hello"
        # Result is a clean list[ContextMessage] that survives a snapshot.
        store = HibernationStore(tmp_path)
        snap = _make_snapshot(n_messages=0)
        snap.messages.extend(msgs)
        assert store.hydrate(store.dehydrate(snap)).messages == msgs


# --------------------------------------------------------------------------- #
# Controller policy                                                            #
# --------------------------------------------------------------------------- #


class TestShouldDehydrate:
    def test_deep_and_heavy_is_dehydrated(self, tmp_path):
        ctrl = HibernationController(
            HibernationStore(tmp_path), min_depth=3, min_context_messages=8
        )
        assert ctrl.should_dehydrate(_make_snapshot(depth=5, n_messages=12))

    def test_shallow_is_not_dehydrated(self, tmp_path):
        ctrl = HibernationController(
            HibernationStore(tmp_path), min_depth=3, min_context_messages=8
        )
        assert not ctrl.should_dehydrate(_make_snapshot(depth=1, n_messages=99))

    def test_light_context_is_not_dehydrated(self, tmp_path):
        ctrl = HibernationController(
            HibernationStore(tmp_path), min_depth=3, min_context_messages=8
        )
        assert not ctrl.should_dehydrate(_make_snapshot(depth=9, n_messages=2))


# --------------------------------------------------------------------------- #
# parked() context manager                                                     #
# --------------------------------------------------------------------------- #


class TestParked:
    async def test_parked_dehydrates_then_rehydrates(self, tmp_path):
        store = HibernationStore(tmp_path)
        ctrl = HibernationController(store, min_depth=3, min_context_messages=8)
        env = _make_snapshot(depth=5, n_messages=12)

        async with ctrl.parked(env) as parked:
            assert parked.dehydrated is True
            # While parked, the snapshot lives on disk, not in this frame.
            assert Path(parked.handle.path).exists()
            assert parked.snapshot is None

        assert parked.snapshot == env
        # The disk snapshot is cleaned up once resumed.
        assert not Path(parked.handle.path).exists()

    async def test_parked_below_threshold_stays_resident(self, tmp_path):
        store = HibernationStore(tmp_path)
        ctrl = HibernationController(store, min_depth=3, min_context_messages=8)
        env = _make_snapshot(depth=1, n_messages=2)

        async with ctrl.parked(env) as parked:
            assert parked.dehydrated is False
            assert not list(store.base_dir.glob("*.snapshot.json"))

        assert parked.snapshot is env  # same object, never round-tripped

    async def test_parked_rehydrates_even_if_body_raises(self, tmp_path):
        store = HibernationStore(tmp_path)
        ctrl = HibernationController(store, min_depth=3, min_context_messages=8)
        env = _make_snapshot(depth=5, n_messages=12)

        with pytest.raises(ValueError, match="boom"):
            async with ctrl.parked(env) as parked:
                assert parked.dehydrated is True
                raise ValueError("boom")

        # finally-block resume ran; disk cleaned up.
        assert not (store.base_dir / "TASK-1.snapshot.json").exists()


# --------------------------------------------------------------------------- #
# Deep-recursion unwind — the headline scenario                                #
# --------------------------------------------------------------------------- #


class TestDeepRecursion:
    async def test_ten_deep_ancestors_dehydrate_and_restore(self, tmp_path):
        """A 10-deep chain: each ancestor is dehydrated while its descendant
        runs, and every one rehydrates to its exact pre-park state on unwind."""
        store = HibernationStore(tmp_path)
        ctrl = HibernationController(store, min_depth=1, min_context_messages=1)
        depth_total = 10
        resumed: list[AgentEnvironmentSnapshot] = []

        async def recurse(level: int) -> None:
            env = _make_snapshot(
                task_id=f"TASK-{level}", depth=level, n_messages=level + 2
            )
            async with ctrl.parked(env) as parked:
                if level < depth_total:
                    await recurse(level + 1)
                # At the deepest point, every shallower ancestor's snapshot is
                # on disk and this frame holds only handles.
                if level == depth_total:
                    live = list(store.base_dir.glob("*.snapshot.json"))
                    assert len(live) == depth_total  # levels 1..10 all parked
            assert parked.snapshot is not None
            resumed.append(parked.snapshot)

        await recurse(1)

        # Every level restored, innermost-first, each with its own depth/context.
        assert [s.depth for s in resumed] == list(range(depth_total, 0, -1))
        for s in resumed:
            assert len(s.messages) == s.depth + 2
        # All disk snapshots cleaned up after the full unwind.
        assert list(store.base_dir.glob("*.snapshot.json")) == []


# --------------------------------------------------------------------------- #
# Async store wrappers                                                         #
# --------------------------------------------------------------------------- #


class TestAsyncWrappers:
    async def test_async_round_trip(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _make_snapshot()
        handle = await store.dehydrate_async(snap)
        restored = await store.hydrate_async(handle)
        assert restored == snap
        await store.discard_async(handle)
        assert not Path(handle.path).exists()


# --------------------------------------------------------------------------- #
# Threshold boundary (>=, not >)                                               #
# --------------------------------------------------------------------------- #


class TestThresholdBoundary:
    def test_at_threshold_is_dehydrated(self, tmp_path):
        ctrl = HibernationController(
            HibernationStore(tmp_path), min_depth=3, min_context_messages=8
        )
        # Exactly at both thresholds must dehydrate (>=, guards a > regression).
        assert ctrl.should_dehydrate(_make_snapshot(depth=3, n_messages=8))


# --------------------------------------------------------------------------- #
# Keyed integrity — a different store instance cannot hydrate                   #
# --------------------------------------------------------------------------- #


class TestKeyedIntegrity:
    def test_foreign_store_cannot_hydrate(self, tmp_path):
        """The MAC is keyed by a per-instance random key, so a second store
        (different key) rejects the first store's file even though the file is
        byte-for-byte valid and its own envelope checksum is internally
        consistent — proving integrity does not rely on the trusted handle
        alone once keying is in play."""
        store_a = HibernationStore(tmp_path)
        handle = store_a.dehydrate(_make_snapshot())

        # A fresh store over the SAME dir has a different ephemeral key.
        store_b = HibernationStore(tmp_path)
        with pytest.raises(IntegrityError):
            store_b.hydrate(handle)


# --------------------------------------------------------------------------- #
# Concurrency                                                                  #
# --------------------------------------------------------------------------- #


class TestConcurrency:
    async def test_two_agents_dehydrate_concurrently(self, tmp_path):
        store = HibernationStore(tmp_path)
        a = _make_snapshot(task_id="TASK-A", depth=4, n_messages=10)
        b = _make_snapshot(task_id="TASK-B", depth=7, n_messages=20)

        ha, hb = await asyncio.gather(
            store.dehydrate_async(a), store.dehydrate_async(b)
        )
        assert ha.path != hb.path  # distinct files, no clobber

        ra, rb = await asyncio.gather(
            store.hydrate_async(ha), store.hydrate_async(hb)
        )
        assert ra == a and rb == b
        assert {p.name for p in store.base_dir.glob("*.snapshot.json")} == {
            Path(ha.path).name,
            Path(hb.path).name,
        }


# --------------------------------------------------------------------------- #
# Payload edge cases — unicode, large multibyte                                #
# --------------------------------------------------------------------------- #


class TestPayloadEdgeCases:
    def test_unicode_content_round_trips(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _make_snapshot(n_messages=0)
        snap.messages.append(
            ContextMessage(
                role="user",
                content="héllo 世界 🌍 ‮RTL‬ écombining ✅",
                token_estimate=5,
            )
        )
        restored = store.hydrate(store.dehydrate(snap))
        assert restored == snap
        assert restored.messages[0].content == snap.messages[0].content

    def test_large_multibyte_content_round_trips(self, tmp_path):
        store = HibernationStore(tmp_path)
        snap = _make_snapshot(n_messages=0)
        big = "λ" * 200_000
        snap.messages.append(
            ContextMessage(role="user", content=big, token_estimate=len(big) // 4)
        )
        handle = store.dehydrate(snap)
        restored = store.hydrate(handle)
        assert restored.messages[0].content == big
        # size_bytes is the on-disk envelope length: at least the utf-8 cost.
        assert handle.size_bytes >= len(big.encode("utf-8"))


# --------------------------------------------------------------------------- #
# messages_from_dicts coercion edges                                          #
# --------------------------------------------------------------------------- #


class TestMessageBridgeEdges:
    def test_none_and_missing_fields_are_coerced(self):
        raw: list[dict[str, object]] = [
            {"role": "assistant", "content": None},
            {"role": "user"},  # content key absent
            {"role": "system", "content": 0},  # falsy non-str
            {"role": "user", "content": None, "timestamp": None},  # None ts
        ]
        msgs = messages_from_dicts(raw)
        assert [m.content for m in msgs] == ["", "", "0", ""]
        assert all(isinstance(m.content, str) for m in msgs)
        assert msgs[2].is_system is True
        assert msgs[3].timestamp == 0.0  # None timestamp coerced, no crash

    def test_non_numeric_string_timestamp_falls_back_to_zero(self):
        # A non-numeric string must NOT raise ValueError out of float(); it
        # falls back to 0.0.  A numeric string, however, is coerced faithfully.
        msgs = messages_from_dicts(
            [
                {"role": "user", "content": "a", "timestamp": "not-a-number"},
                {"role": "user", "content": "b", "timestamp": "123.5"},
            ]
        )
        assert msgs[0].timestamp == 0.0  # bad coercion swallowed, no crash
        assert msgs[1].timestamp == 123.5  # numeric string parses


# --------------------------------------------------------------------------- #
# Discard idempotency                                                          #
# --------------------------------------------------------------------------- #


class TestDiscardIdempotent:
    def test_discard_twice_is_a_noop(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())
        store.discard(handle)
        store.discard(handle)  # already gone — must not raise
        assert not Path(handle.path).exists()

    async def test_discard_async_twice_is_a_noop(self, tmp_path):
        store = HibernationStore(tmp_path)
        handle = store.dehydrate(_make_snapshot())
        await store.discard_async(handle)
        await store.discard_async(handle)
        assert not Path(handle.path).exists()


# --------------------------------------------------------------------------- #
# Default directory resolution                                                 #
# --------------------------------------------------------------------------- #


class TestDefaultDir:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        from general_ludd.agents.hibernation import default_hibernation_dir

        monkeypatch.setenv("GLUDD_HIBERNATION_DIR", str(tmp_path / "hib"))
        assert default_hibernation_dir() == tmp_path / "hib"

    def test_xdg_fallback(self, tmp_path, monkeypatch):
        from general_ludd.agents.hibernation import default_hibernation_dir

        monkeypatch.delenv("GLUDD_HIBERNATION_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert default_hibernation_dir() == tmp_path / "xdg" / "general-ludd" / "hibernation"
