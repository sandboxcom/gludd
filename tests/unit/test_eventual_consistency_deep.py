"""Deep eventual-consistency tests: read-repair, hinted handoff, Merkle tree
anti-entropy sync.  15+ tests exercising quorum reads, stale-replica repair,
hint buffering/replay/expiry, tree construction, divergence detection, and
round-trip sync.
"""

from __future__ import annotations

from general_ludd.distributed.eventual_consistency import (
    DataStore,
    HintedHandoff,
    MerkleTree,
    merkle_sync,
    read_repair,
)
from general_ludd.distributed.vector_clock import VectorClock

# ── DataStore ─────────────────────────────────────────────────────────────────


class TestDataStore:
    def test_put_and_get(self) -> None:
        store = DataStore("a")
        store.put("k1", 42)
        vv = store.get("k1")
        assert vv is not None
        assert vv.value == 42
        assert vv.version["a"] == 1

    def test_update_newer_version_wins(self) -> None:
        store = DataStore("a")
        store.put("x", "old", VectorClock({"a": 1}))
        replaced = store.update("x", "new", VectorClock({"a": 2}))
        assert replaced is True
        assert store.get("x").value == "new"  # type: ignore[union-attr]

    def test_update_concurrent_merges(self) -> None:
        store = DataStore("b")
        store.put("x", "v1", VectorClock({"a": 2}))
        store.update("x", "v2", VectorClock({"b": 1}))
        vv = store.get("x")
        assert vv is not None
        assert vv.version["a"] == 2
        assert vv.version["b"] == 1

    def test_update_older_version_rejected(self) -> None:
        store = DataStore("a")
        store.put("x", "current", VectorClock({"a": 5}))
        replaced = store.update("x", "stale", VectorClock({"a": 3}))
        assert replaced is False
        assert store.get("x").value == "current"  # type: ignore[union-attr]

    def test_list_keys_sorted(self) -> None:
        store = DataStore("a")
        store.put("z", 1)
        store.put("a", 2)
        store.put("m", 3)
        assert store.list_keys() == ["a", "m", "z"]


# ── Read-Repair ───────────────────────────────────────────────────────────────


class TestReadRepair:
    def test_all_replicas_agree(self) -> None:
        a, b, c = DataStore("a"), DataStore("b"), DataStore("c")
        vc = VectorClock({"a": 1})
        a.put("k", "val", vc)
        b.put("k", "val", vc)
        c.put("k", "val", vc)
        result = read_repair("k", {"a": a, "b": b, "c": c}, quorum=2)
        assert result.value == "val"
        assert result.quorum_met is True
        assert result.repairs == []

    def test_repairs_stale_replica(self) -> None:
        a, b, c = DataStore("a"), DataStore("b"), DataStore("c")
        a.put("k", "new", VectorClock({"a": 3}))
        b.put("k", "old", VectorClock({"a": 1}))
        c.put("k", "old", VectorClock({"a": 1}))
        result = read_repair("k", {"a": a, "b": b, "c": c}, quorum=2)
        assert result.value == "new"
        assert set(result.repairs) == {"b", "c"}
        assert b.get("k").value == "new"  # type: ignore[union-attr]
        assert c.get("k").value == "new"  # type: ignore[union-attr]

    def test_repairs_missing_replica(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "v", VectorClock({"a": 1}))
        result = read_repair("k", {"a": a, "b": b}, quorum=1)
        assert result.repairs == ["b"]
        assert b.get("k") is not None
        assert b.get("k").value == "v"  # type: ignore[union-attr]

    def test_quorum_not_met_empty(self) -> None:
        a, b, c = DataStore("a"), DataStore("b"), DataStore("c")
        result = read_repair("nonexistent", {"a": a, "b": b, "c": c}, quorum=2)
        assert result.quorum_met is False
        assert result.value is None

    def test_quorum_not_met_insufficient_responses(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "v", VectorClock({"a": 1}))
        result = read_repair("k", {"a": a, "b": b}, quorum=2)
        assert result.quorum_met is False

    def test_repairs_no_change_when_concurrent(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "v_a", VectorClock({"a": 1}))
        b.put("k", "v_b", VectorClock({"b": 1}))
        result = read_repair("k", {"a": a, "b": b}, quorum=2)
        assert result.quorum_met is True
        assert result.repairs == []


# ── Hinted Handoff ────────────────────────────────────────────────────────────


class TestHintedHandoff:
    def test_record_on_unreachable(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        vc = VectorClock({"a": 1})
        recorded = hh.record_if_unreachable("b", "k", "val", vc)
        assert recorded is True
        assert hh.hint_count("b") == 1

    def test_no_record_on_reachable(self) -> None:
        hh = HintedHandoff("a")
        vc = VectorClock({"a": 1})
        recorded = hh.record_if_unreachable("b", "k", "val", vc)
        assert recorded is False
        assert hh.total_hints() == 0

    def test_deliver_hints(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        vc = VectorClock({"a": 1})
        hh.record_if_unreachable("b", "k", "val", vc)
        target = DataStore("b")
        delivered = hh.deliver_hints("b", target)
        assert delivered == 1
        assert target.get("k").value == "val"  # type: ignore[union-attr]
        assert hh.hint_count("b") == 0

    def test_deliver_all(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        hh.mark_unreachable("c")
        hh.record_if_unreachable("b", "k1", "v1", VectorClock({"a": 1}))
        hh.record_if_unreachable("c", "k2", "v2", VectorClock({"a": 1}))
        db = {"b": DataStore("b"), "c": DataStore("c")}
        total = hh.deliver_all(db)
        assert total == 2
        assert db["b"].get("k1").value == "v1"  # type: ignore[union-attr]
        assert db["c"].get("k2").value == "v2"  # type: ignore[union-attr]

    def test_mark_reachable_clears_flag(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        assert "b" in hh.unreachable
        hh.mark_reachable("b")
        assert "b" not in hh.unreachable

    def test_expire_hints(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        hh.record_if_unreachable("b", "k", "old", VectorClock({"a": 1}))
        hh._hints["b"][0].timestamp -= 100
        removed = hh.expire_hints(30)
        assert removed == 1
        assert hh.total_hints() == 0

    def test_total_hints_aggregates(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        hh.mark_unreachable("c")
        hh.record_if_unreachable("b", "k1", "v1", VectorClock({"a": 1}))
        hh.record_if_unreachable("b", "k2", "v2", VectorClock({"a": 1}))
        hh.record_if_unreachable("c", "k3", "v3", VectorClock({"a": 1}))
        assert hh.total_hints() == 3


# ── Merkle Tree ───────────────────────────────────────────────────────────────


class TestMerkleTree:
    def test_empty_tree(self) -> None:
        tree = MerkleTree([])
        assert tree.root_hash() == ""
        assert tree.root is None

    def test_single_leaf(self) -> None:
        vc = VectorClock({"a": 1})
        tree = MerkleTree([("k", "v", vc)])
        assert tree.root is not None
        assert tree.root_hash() != ""
        assert tree.root.key_range == ("k", "k")

    def test_identical_trees_match(self) -> None:
        vc = VectorClock({"a": 1})
        items = [("k1", "v1", vc), ("k2", "v2", vc), ("k3", "v3", vc)]
        t1 = MerkleTree(items)
        t2 = MerkleTree(items)
        assert t1.root_hash() == t2.root_hash()
        assert t1.compare(t2) == set()

    def test_divergent_tree(self) -> None:
        vc = VectorClock({"a": 1})
        t1 = MerkleTree([("k1", "v1", vc), ("k2", "v2", vc)])
        t2 = MerkleTree([("k1", "v1", vc), ("k2", "different", vc)])
        diff = t1.compare(t2)
        assert "k2" in diff

    def test_extra_keys_divergent(self) -> None:
        vc_a = VectorClock({"a": 1})
        vc_b = VectorClock({"b": 1})
        t1 = MerkleTree([("k1", "v1", vc_a)])
        t2 = MerkleTree([("k2", "v2", vc_b)])
        diff = t1.compare(t2)
        assert "k1" in diff
        assert "k2" in diff


# ── Merkle Sync ───────────────────────────────────────────────────────────────


class TestMerkleSync:
    def test_sync_identical_stores(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        vc = VectorClock({"a": 1})
        a.put("k", "v", vc)
        b.put("k", "v", vc)
        actions = merkle_sync(a, b)
        assert actions == {}

    def test_sync_stale_replica_gets_update(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "new", VectorClock({"a": 3}))
        b.put("k", "old", VectorClock({"a": 1}))
        actions = merkle_sync(a, b)
        assert actions["k"] == ("equal", "pull")
        assert b.get("k").value == "new"  # type: ignore[union-attr]

    def test_sync_missing_key_on_one_side(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "v", VectorClock({"a": 1}))
        actions = merkle_sync(a, b)
        assert actions["k"] == ("equal", "pull")
        assert b.get("k").value == "v"  # type: ignore[union-attr]

    def test_sync_concurrent_values_reconciled(self) -> None:
        a, b = DataStore("a"), DataStore("b")
        a.put("k", "v_a", VectorClock({"a": 1}))
        b.put("k", "v_b", VectorClock({"b": 1}))
        actions = merkle_sync(a, b)
        assert "k" in actions
        va = a.get("k")
        vb = b.get("k")
        assert va is not None
        assert vb is not None
        assert va.version["a"] == 1
        assert va.version["b"] == 1
        assert vb.version["a"] == 1
        assert vb.version["b"] == 1


# ── Integration scenario ──────────────────────────────────────────────────────


class TestIntegration:
    def test_read_repair_after_update_propagates(self) -> None:
        a, b, c = DataStore("a"), DataStore("b"), DataStore("c")
        a.put("k", "v1", VectorClock({"a": 1}))
        b.put("k", "v1", VectorClock({"a": 1}))
        c.put("k", "v1", VectorClock({"a": 1}))
        a.put("k", "v2", VectorClock({"a": 2}))
        result = read_repair("k", {"a": a, "b": b, "c": c}, quorum=2)
        assert result.value == "v2"
        assert b.get("k").value == "v2"  # type: ignore[union-attr]
        assert c.get("k").value == "v2"  # type: ignore[union-attr]

    def test_hinted_handoff_then_merkle_sync(self) -> None:
        hh = HintedHandoff("a")
        hh.mark_unreachable("b")
        a = DataStore("a")
        b = DataStore("b")
        a.put("k", "val", VectorClock({"a": 1}))
        vv = a.get("k")
        assert vv is not None
        hh.record_if_unreachable("b", "k", vv.value, vv.version)
        hh.deliver_all({"b": b})
        assert b.get("k").value == "val"  # type: ignore[union-attr]
        actions = merkle_sync(a, b)
        assert "k" not in actions
