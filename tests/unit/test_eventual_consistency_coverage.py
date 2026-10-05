"""Symmetry and fallback contracts for eventual-consistency primitives."""

from __future__ import annotations

from general_ludd.distributed.eventual_consistency import (
    DataStore,
    HintedHandoff,
    MerkleNode,
    MerkleTree,
    merkle_sync,
    read_repair,
)
from general_ludd.distributed.vector_clock import VectorClock


def test_store_version_state_and_default_read_quorum() -> None:
    first = DataStore("first")
    second = DataStore("second")
    third = DataStore("third")
    version = first.put("key", "value")
    second.put("key", "value", version)

    result = read_repair("key", {"first": first, "second": second, "third": third})

    assert first.get_version("key") == version
    assert first.get_version("missing") is None
    assert first.state()["key"].value == "value"
    assert result.quorum_met is True
    assert result.repairs == ["third"]


def test_handoff_retains_recent_hint_and_skips_unknown_target() -> None:
    handoff = HintedHandoff("coordinator")
    handoff.mark_unreachable("offline")
    handoff.record_if_unreachable(
        "offline",
        "key",
        "value",
        VectorClock({"coordinator": 1}),
    )

    assert handoff.pending_hints("offline")[0].key == "key"
    assert handoff.deliver_all({"different": DataStore("different")}) == 0
    assert handoff.expire_hints(60) == 0
    assert handoff.hint_count("offline") == 1


def test_merkle_compare_is_symmetric_for_empty_tree() -> None:
    empty = MerkleTree([])
    populated = MerkleTree([("key", "value", VectorClock({"node": 1}))])

    assert empty.compare(empty) == set()
    assert empty.compare(populated) == {"key"}
    assert populated.compare(empty) == {"key"}


def test_merkle_internal_none_boundaries_collect_existing_keys() -> None:
    tree = MerkleTree([])
    leaf = MerkleNode(hash="leaf", key_range=("key", "key"))
    branch = MerkleNode(hash="branch", left=leaf)
    result: set[str] = set()

    assert tree._build_tree([], 1, 0) is None
    tree._diff_nodes(None, None, result)
    tree._diff_nodes(None, branch, result)
    tree._diff_nodes(branch, None, result)

    assert result == {"key"}


def test_merkle_sync_pulls_key_from_second_store() -> None:
    first = DataStore("first")
    second = DataStore("second")
    second.put("key", "value", VectorClock({"second": 1}))

    actions = merkle_sync(first, second)

    assert actions == {"key": ("pull", "equal")}
    replicated = first.get("key")
    assert replicated is not None
    assert replicated.value == "value"
