"""Deep shard allocation/routing tests for ConsistentHashRing — ring integrity,
virtual-node spread, load-balance statistics, add/remove minimal disruption,
deterministic placement, edge cases, stress, and starvation.
"""

from __future__ import annotations

import math
from collections import Counter

import pytest

from general_ludd.hash_table import ConsistentHashRing


class TestRingIntegrity:
    def test_ring_order_is_monotonically_increasing(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        positions = ring._ring
        for i in range(1, len(positions)):
            assert positions[i - 1] < positions[i], f"ring out of order at index {i}"

    def test_no_duplicate_hash_positions(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=128)
        for n in (f"node-{i}" for i in range(10)):
            ring.add_node(n)
        assert len(set(ring._ring)) == len(ring._ring), "duplicate hash positions on ring"

    def test_node_map_contains_only_valid_positions(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("x")
        ring.add_node("y")
        ring_positions = set(ring._ring)
        for h in ring._node_map:
            assert h in ring_positions, f"orphan hash {h} not on ring"

    def test_every_ring_position_mapped_to_exactly_one_node(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=40)
        ring.add_node("p")
        ring.add_node("q")
        assert len(ring._ring) == len(ring._node_map)
        for h in ring._ring:
            assert h in ring._node_map


class TestAddRemoveDisruption:
    def test_removing_node_only_remaps_keys_that_belonged_to_it(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        keys = [f"k{i}" for i in range(2000)]
        before = {k: ring.get_node(k) for k in keys}
        ring.remove_node("b")
        after = {k: ring.get_node(k) for k in keys}
        remapped = {k for k in keys if before[k] != after[k]}
        only_b_keys = {k for k, n in before.items() if n == "b"}
        assert remapped.issubset(only_b_keys), f"keys remapped from non-removed nodes: {remapped - only_b_keys}"
        assert len(remapped) > 0, "no keys remapped; removal was a no-op"

    def test_adding_node_only_steals_keys_from_its_successor_range(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("a")
        ring.add_node("b")
        keys = [f"k{i}" for i in range(2000)]
        before = {k: ring.get_node(k) for k in keys}
        ring.add_node("c")
        after = {k: ring.get_node(k) for k in keys}
        remapped = {k for k in keys if before[k] != after[k]}
        assert all(after[k] == "c" for k in remapped), "keys remapped to a node other than the new one"
        assert len(remapped) > 0, "new node stole no keys"

    def test_minimal_disruption_upper_bound_on_add(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=100)
        ring.add_node("n1")
        ring.add_node("n2")
        ring.add_node("n3")
        keys = [f"k{i}" for i in range(5000)]
        before = {k: ring.get_node(k) for k in keys}
        ring.add_node("n4")
        after = {k: ring.get_node(k) for k in keys}
        changes = sum(1 for k in keys if before[k] != after[k])
        fraction = changes / len(keys)
        assert fraction < 0.45, f"too many keys remapped on add: {fraction:.1%}"

    def test_minimal_disruption_upper_bound_on_remove(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=100)
        ring.add_node("n1")
        ring.add_node("n2")
        ring.add_node("n3")
        ring.add_node("n4")
        keys = [f"k{i}" for i in range(5000)]
        before = {k: ring.get_node(k) for k in keys}
        ring.remove_node("n4")
        after = {k: ring.get_node(k) for k in keys}
        changes = sum(1 for k in keys if before[k] != after[k])
        fraction = changes / len(keys)
        assert fraction < 0.45, f"too many keys remapped on remove: {fraction:.1%}"


class TestVirtualNodeDistribution:
    def test_virtual_nodes_spread_across_full_hash_space(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("a")
        positions = ring._ring
        max_hash = int(math.pow(2, 128)) - 1
        segments = 5
        seg_width = max_hash / segments
        buckets: list[int] = [0] * segments
        for p in positions:
            idx = min(int(p / seg_width), segments - 1)
            buckets[idx] += 1
        assert all(b > 0 for b in buckets), f"some hash-space segments empty: {buckets}"

    def test_more_virtual_nodes_improve_balance(self) -> None:
        low_vn = ConsistentHashRing(virtual_nodes=8)
        high_vn = ConsistentHashRing(virtual_nodes=256)
        for n in ("a", "b", "c"):
            low_vn.add_node(n)
            high_vn.add_node(n)
        n_keys = 5000
        low_counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}
        high_counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}
        for i in range(n_keys):
            key = f"key-{i}"
            low_counts[low_vn.get_node(key)] += 1
            high_counts[high_vn.get_node(key)] += 1

        def max_deviation(counts: dict[str, int]) -> float:
            expected = n_keys / 3
            return max(abs(c - expected) / expected for c in counts.values())

        assert max_deviation(high_counts) < max_deviation(low_counts), (
            f"more virtual nodes did not improve balance: "
            f"low={max_deviation(low_counts):.3f} high={max_deviation(high_counts):.3f}"
        )

    def test_virtual_nodes_per_node_match_constructor_arg(self) -> None:
        for vn in (1, 16, 64, 200):
            ring = ConsistentHashRing(virtual_nodes=vn)
            ring.add_node("n1")
            ring.add_node("n2")
            assert ring.node_count == 2
            assert len(ring._ring) == vn * 2
            counts = Counter(ring._node_map.values())
            assert counts["n1"] == vn
            assert counts["n2"] == vn


class TestLoadBalance:
    def test_chi_squared_within_bounds(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=512)
        nodes = [f"n{i}" for i in range(5)]
        for n in nodes:
            ring.add_node(n)
        n_keys = 10000
        counts: dict[str, int] = {n: 0 for n in nodes}
        for i in range(n_keys):
            counts[ring.get_node(f"key-{i}")] += 1
        expected = n_keys / len(nodes)
        chi2 = sum((c - expected) ** 2 / expected for c in counts.values())
        assert chi2 < 80.0, f"chi-squared too high: {chi2:.1f}"

    def test_no_starved_nodes(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        for n in (f"shard-{i}" for i in range(10)):
            ring.add_node(n)
        seen: set[str] = set()
        for i in range(5000):
            seen.add(ring.get_node(f"k{i}"))
        assert len(seen) == 10, f"starved nodes: only {len(seen)}/10 received keys"

    def test_single_node_gets_all_keys(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        ring.add_node("only")
        for i in range(200):
            assert ring.get_node(f"key-{i}") == "only"

    def test_uniformity_improves_with_key_count(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=64)
        for n in ("a", "b", "c"):
            ring.add_node(n)

        def max_dev(n: int) -> float:
            c: dict[str, int] = {"a": 0, "b": 0, "c": 0}
            for i in range(n):
                c[ring.get_node(f"k{i}")] += 1
            expected = n / 3
            return max(abs(v - expected) / expected for v in c.values())

        assert max_dev(10000) < max_dev(300), "more keys did not improve uniformity"


class TestDeterminism:
    def test_identical_rings_produce_identical_routes(self) -> None:
        keys = [f"key-{i}" for i in range(500)]
        r1 = ConsistentHashRing(virtual_nodes=64)
        r2 = ConsistentHashRing(virtual_nodes=64)
        for n in ("x", "y", "z"):
            r1.add_node(n)
            r2.add_node(n)
        for k in keys:
            assert r1.get_node(k) == r2.get_node(k), f"divergent routing for {k}"

    def test_same_node_order_different_vn_yields_different_placement(self) -> None:
        r32 = ConsistentHashRing(virtual_nodes=32)
        r128 = ConsistentHashRing(virtual_nodes=128)
        for n in ("a", "b"):
            r32.add_node(n)
            r128.add_node(n)
        differing = 0
        for i in range(500):
            if r32.get_node(f"k{i}") != r128.get_node(f"k{i}"):
                differing += 1
        assert differing > 0, "different virtual node counts produced identical rings"


class TestStress:
    def test_many_nodes_many_keys(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        nodes = [f"n{i}" for i in range(200)]
        for n in nodes:
            ring.add_node(n)
        assert ring.node_count == 200
        for i in range(2000):
            node = ring.get_node(f"key-{i}")
            assert node.startswith("n"), f"bad node: {node}"

    def test_rapid_add_remove_cycle(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        ring.add_node("a")
        ring.add_node("b")
        for _ in range(20):
            ring.add_node("temp")
            node = ring.get_node("probe")
            assert node in {"a", "b", "temp"}
            ring.remove_node("temp")
            node = ring.get_node("probe")
            assert node in {"a", "b"}

    def test_ring_stays_sorted_after_many_operations(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=16)
        active: list[str] = []
        for i in range(30):
            name = f"node-{i}"
            ring.add_node(name)
            active.append(name)
        for _ in range(10):
            if active:
                to_remove = active.pop(0)
                ring.remove_node(to_remove)
        positions = ring._ring
        for i in range(1, len(positions)):
            assert positions[i - 1] < positions[i], "ring sort broken after churn"


class TestEdgeCases:
    def test_remove_last_node_leaves_empty_ring(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=16)
        ring.add_node("only")
        ring.remove_node("only")
        assert ring.node_count == 0
        assert len(ring._ring) == 0
        with pytest.raises(ValueError, match="empty"):
            ring.get_node("any")

    def test_single_virtual_node(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=1)
        ring.add_node("solo")
        ring.add_node("duo")
        assert len(ring._ring) == 2
        assert ring.node_count == 2
        result = ring.get_node("test")
        assert result in {"solo", "duo"}

    def test_very_large_virtual_nodes(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=2000)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        result = ring.get_node("any-key")
        assert result in {"a", "b", "c"}
        assert len(ring._ring) == 6000

    def test_key_hash_collision_does_not_break_routing(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        ring.add_node("a")
        ring.add_node("b")
        # same key must always return same node even across many lookups
        for _ in range(100):
            assert ring.get_node("fixed") == ring.get_node("fixed")

    def test_node_ids_with_special_characters(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=32)
        ring.add_node("10.0.0.1:8080")
        ring.add_node("shard-us-east-1")
        ring.add_node("node/with/slashes")
        node = ring.get_node("my-data")
        assert node in {"10.0.0.1:8080", "shard-us-east-1", "node/with/slashes"}
