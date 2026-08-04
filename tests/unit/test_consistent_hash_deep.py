"""Deep tests for ConsistentHashRing — weighted virtual nodes, distribution,
key migration, preference lists, and edge cases.
"""

from __future__ import annotations

import pytest

from general_ludd.distributed.consistent_hash import ConsistentHashRing


def _keys(n: int, prefix: str = "k") -> list[str]:
    return [f"{prefix}{i}" for i in range(n)]


# ------------------------------------------------------------------ construction


class TestConstruction:
    def test_default_virtual_nodes(self) -> None:
        ring = ConsistentHashRing()
        assert ring.virtual_nodes == 64
        assert len(ring) == 0

    def test_custom_virtual_nodes(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=150)
        assert ring.virtual_nodes == 150

    def test_rejects_zero_virtual_nodes(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            ConsistentHashRing(virtual_nodes=0)

    def test_rejects_negative_virtual_nodes(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            ConsistentHashRing(virtual_nodes=-5)


# ------------------------------------------------------------------ add / remove


class TestAddRemove:
    def test_add_single_node(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        assert len(ring) == 1
        assert "a" in ring

    def test_add_multiple_nodes(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        assert len(ring) == 3

    def test_add_duplicate_is_noop(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("a")
        assert len(ring) == 1

    def test_remove_node(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("b")
        ring.remove_node("a")
        assert len(ring) == 1
        assert "a" not in ring
        assert "b" in ring

    def test_remove_unknown_node_raises(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(KeyError, match="unknown"):
            ring.remove_node("ghost")

    def test_nodes_property_returns_tuple(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("x")
        ring.add_node("y")
        assert isinstance(ring.nodes, tuple)
        assert set(ring.nodes) == {"x", "y"}


# ------------------------------------------------------------------ weight


class TestWeight:
    def test_weight_controls_virtual_point_count(self) -> None:
        vn = 20
        ring = ConsistentHashRing(virtual_nodes=vn)
        ring.add_node("light", weight=1)
        ring.add_node("heavy", weight=3)
        dist = ring.point_distribution()
        assert dist["light"] == vn
        assert dist["heavy"] == 3 * vn

    def test_set_weight_updates_point_count(self) -> None:
        vn = 10
        ring = ConsistentHashRing(virtual_nodes=vn)
        ring.add_node("n", weight=1)
        assert ring.point_distribution()["n"] == vn
        ring.set_weight("n", weight=4)
        assert ring.point_distribution()["n"] == 4 * vn

    def test_set_weight_same_value_is_noop(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("n", weight=2)
        before = list(ring.point_distribution().values())
        ring.set_weight("n", weight=2)
        after = list(ring.point_distribution().values())
        assert before == after

    def test_set_weight_unknown_raises(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(KeyError, match="unknown"):
            ring.set_weight("x", weight=2)

    def test_rejects_zero_weight(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(ValueError, match=">= 1"):
            ring.add_node("n", weight=0)

    def test_rejects_negative_weight(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(ValueError, match=">= 1"):
            ring.add_node("n", weight=-3)

    def test_weight_of_returns_weight(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("n", weight=5)
        assert ring.weight_of("n") == 5


# ------------------------------------------------------------------ lookup


class TestLookup:
    def test_get_node_is_deterministic(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("b")
        first = ring.get_node("mykey")
        for _ in range(50):
            assert ring.get_node("mykey") == first

    def test_get_node_on_empty_ring_raises(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(RuntimeError, match="empty"):
            ring.get_node("k")

    def test_get_nodes_preference_list(self) -> None:
        ring = ConsistentHashRing()
        for nid in ("a", "b", "c"):
            ring.add_node(nid)
        prefs = ring.get_nodes("mykey", count=2)
        assert len(prefs) == 2
        assert len(set(prefs)) == 2
        assert prefs[0] != prefs[1]

    def test_get_nodes_more_than_available(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("b")
        prefs = ring.get_nodes("k", count=5)
        assert len(prefs) == 2  # only 2 distinct nodes exist

    def test_get_nodes_rejects_zero_count(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        with pytest.raises(ValueError, match=">= 1"):
            ring.get_nodes("k", count=0)


# ------------------------------------------------------------------ distribution & balance


class TestDistribution:
    def test_keys_spread_across_nodes(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        for nid in ("a", "b", "c", "d"):
            ring.add_node(nid)
        keys = _keys(2000)
        dist = ring.key_distribution(keys)
        assert len(dist) == 4
        total = sum(dist.values())
        assert total == 2000

    def test_balance_ratio_decreases_with_more_virtual_nodes(self) -> None:
        keys = _keys(2000)
        ratio_low = _balance_for_vn(8, keys)
        ratio_high = _balance_for_vn(256, keys)
        assert ratio_high < ratio_low

    def test_balance_ratio_empty_ring(self) -> None:
        ring = ConsistentHashRing()
        assert ring.balance_ratio([]) == 0.0

    def test_balance_ratio_single_node(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        assert ring.balance_ratio(_keys(100)) == 0.0

    def test_heavier_node_gets_more_keys_on_average(self) -> None:
        vn = 64
        ring = ConsistentHashRing(virtual_nodes=vn)
        ring.add_node("light", weight=1)
        ring.add_node("heavy", weight=3)
        keys = _keys(5000)
        dist = ring.key_distribution(keys)
        assert dist["heavy"] > dist["light"]


# ------------------------------------------------------------------ migration


class TestMigration:
    def test_migration_count_increases_after_remove(self) -> None:
        ring1 = ConsistentHashRing(virtual_nodes=64)
        ring1.add_node("a")
        ring1.add_node("b")
        ring1.add_node("c")
        keys = _keys(1000)

        ring2 = ConsistentHashRing(virtual_nodes=64)
        ring2.add_node("a")
        ring2.add_node("c")  # b removed

        migrated = ring2.migration_count(ring1, keys)
        assert migrated > 0

    def test_migration_count_zero_when_no_change(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("a")
        ring.add_node("b")
        keys = _keys(500)
        assert ring.migration_count(ring, keys) == 0

    def test_migration_after_add(self) -> None:
        ring1 = ConsistentHashRing(virtual_nodes=64)
        ring1.add_node("a")
        ring2 = ConsistentHashRing(virtual_nodes=64)
        ring2.add_node("a")
        ring2.add_node("b")  # added
        keys = _keys(1000)
        assert ring2.migration_count(ring1, keys) > 0

    def test_only_keys_owned_by_removed_node_migrate(self) -> None:
        ring1 = ConsistentHashRing(virtual_nodes=128)
        ring1.add_node("a")
        ring1.add_node("b")
        keys = _keys(1000, "data")

        ring2 = ConsistentHashRing(virtual_nodes=128)
        ring2.add_node("a")  # b removed

        ring2.migration_count(ring1, keys)
        # keys that were on "a" should stay on "a"
        for k in keys:
            if ring1.get_node(k) == "a":
                assert ring2.get_node(k) == "a"


# ------------------------------------------------------------------ edge cases


class TestEdgeCases:
    def test_single_node_owns_all_keys(self) -> None:
        ring = ConsistentHashRing()
        ring.add_node("only")
        keys = _keys(500)
        dist = ring.key_distribution(keys)
        assert dist == {"only": 500}

    def test_many_nodes_each_get_some_keys(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        for i in range(10):
            ring.add_node(f"n{i}")
        keys = _keys(2000)
        dist = ring.key_distribution(keys)
        assert len(dist) == 10
        assert all(v > 0 for v in dist.values())

    def test_incremental_add_then_remove_restores_original(self) -> None:
        vn = 64
        ring = ConsistentHashRing(virtual_nodes=vn)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")

        ring.add_node("d")
        ring.remove_node("d")

        assert len(ring) == 3
        assert set(ring.nodes) == {"a", "b", "c"}


# ------------------------------------------------------------------ helpers


def _balance_for_vn(vn: int, keys: list[str]) -> float:
    ring = ConsistentHashRing(virtual_nodes=vn)
    for nid in ("a", "b", "c", "d", "e"):
        ring.add_node(nid)
    return ring.balance_ratio(keys)
