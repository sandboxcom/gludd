"""Deep tests for HashTable and ConsistentHashRing — put/get/delete,
collision resolution, rehashing, consistent hashing ring, virtual nodes,
and load distribution.
"""

from __future__ import annotations

import pytest

from general_ludd.hash_table import ConsistentHashRing, HashTable


class TestHashTableBasic:
    def test_put_and_get(self) -> None:
        ht: HashTable[str, int] = HashTable()
        ht.put("a", 1)
        ht.put("b", 2)
        assert ht.get("a") == 1
        assert ht.get("b") == 2

    def test_put_overwrites_existing_key(self) -> None:
        ht: HashTable[str, str] = HashTable()
        ht.put("k", "v1")
        ht.put("k", "v2")
        assert ht.get("k") == "v2"
        assert ht.size == 1

    def test_get_missing_key_raises(self) -> None:
        ht: HashTable[str, int] = HashTable()
        with pytest.raises(KeyError, match="missing"):
            ht.get("missing")

    def test_delete_removes_key(self) -> None:
        ht: HashTable[str, int] = HashTable()
        ht.put("x", 10)
        ht.put("y", 20)
        ht.delete("x")
        assert not ht.contains("x")
        assert ht.contains("y")
        assert ht.size == 1

    def test_delete_missing_key_raises(self) -> None:
        ht: HashTable[str, int] = HashTable()
        with pytest.raises(KeyError, match="absent"):
            ht.delete("absent")

    def test_contains(self) -> None:
        ht: HashTable[str, int] = HashTable()
        ht.put("hello", 42)
        assert ht.contains("hello")
        assert not ht.contains("world")

    def test_capacity_default(self) -> None:
        ht: HashTable[str, int] = HashTable()
        assert ht.capacity >= 1
        assert ht.size == 0

    def test_capacity_custom(self) -> None:
        ht: HashTable[str, int] = HashTable(capacity=16)
        assert ht.capacity == 16
        assert ht.size == 0


class TestHashTableCollisionResolution:
    def test_multiple_keys_in_same_bucket(self) -> None:
        ht: HashTable[object, int] = HashTable()

        class CollidingKey:
            def __init__(self, label: str) -> None:
                self.label = label

            def __hash__(self) -> int:
                return 7

            def __eq__(self, other: object) -> bool:
                return isinstance(other, CollidingKey) and self.label == other.label

        k1 = CollidingKey("a")
        k2 = CollidingKey("b")
        k3 = CollidingKey("c")
        ht.put(k1, 1)
        ht.put(k2, 2)
        ht.put(k3, 3)
        assert ht.get(k1) == 1
        assert ht.get(k2) == 2
        assert ht.get(k3) == 3
        assert ht.size == 3

    def test_collision_delete_maintains_chain(self) -> None:
        ht: HashTable[object, int] = HashTable()

        class CollidingKey:
            def __init__(self, label: str) -> None:
                self.label = label

            def __hash__(self) -> int:
                return 7

            def __eq__(self, other: object) -> bool:
                return isinstance(other, CollidingKey) and self.label == other.label

        k1 = CollidingKey("a")
        k2 = CollidingKey("b")
        k3 = CollidingKey("c")
        ht.put(k1, 1)
        ht.put(k2, 2)
        ht.put(k3, 3)
        ht.delete(k2)
        assert ht.get(k1) == 1
        assert ht.get(k3) == 3
        assert not ht.contains(k2)
        assert ht.size == 2

    def test_collision_update_existing_in_chain(self) -> None:
        ht: HashTable[object, int] = HashTable()

        class CollidingKey:
            def __init__(self, label: str) -> None:
                self.label = label

            def __hash__(self) -> int:
                return 7

            def __eq__(self, other: object) -> bool:
                return isinstance(other, CollidingKey) and self.label == other.label

        k1 = CollidingKey("a")
        k2 = CollidingKey("b")
        ht.put(k1, 1)
        ht.put(k2, 2)
        ht.put(k2, 99)
        assert ht.get(k2) == 99
        assert ht.size == 2


class TestHashTableRehashing:
    def test_rehashes_when_load_factor_exceeded(self) -> None:
        ht: HashTable[int, int] = HashTable(capacity=4)
        for i in range(20):
            ht.put(i, i * 10)
        assert ht.capacity > 4
        assert ht.size == 20
        for i in range(20):
            assert ht.get(i) == i * 10

    def test_rehash_preserves_collided_keys(self) -> None:
        ht: HashTable[object, int] = HashTable(capacity=4)

        class CollidingKey:
            def __init__(self, label: str) -> None:
                self.label = label

            def __hash__(self) -> int:
                return 7

            def __eq__(self, other: object) -> bool:
                return isinstance(other, CollidingKey) and self.label == other.label

        keys = [CollidingKey(chr(ord("a") + i)) for i in range(10)]
        for i, k in enumerate(keys):
            ht.put(k, i)
        # after rehash with capacity increase, all keys still accessible
        for i, k in enumerate(keys):
            assert ht.get(k) == i
        assert ht.size == 10
        assert ht.capacity > 4

    def test_load_factor_below_threshold_no_rehash(self) -> None:
        ht: HashTable[int, int] = HashTable(capacity=32)
        for i in range(10):
            ht.put(i, i)
        assert ht.capacity == 32


class TestHashTableEdgeCases:
    def test_empty_table_contains_nothing(self) -> None:
        ht: HashTable[str, int] = HashTable()
        assert ht.size == 0
        assert not ht.contains("anything")
        with pytest.raises(KeyError):
            ht.get("anything")

    def test_string_keys(self) -> None:
        ht: HashTable[str, str] = HashTable()
        ht.put("hello", "world")
        ht.put("foo", "bar")
        ht.put("", "empty-key")
        assert ht.get("") == "empty-key"
        assert ht.get("hello") == "world"

    def test_int_keys(self) -> None:
        ht: HashTable[int, str] = HashTable()
        ht.put(0, "zero")
        ht.put(-1, "minus one")
        ht.put(2**31, "large")
        assert ht.get(0) == "zero"
        assert ht.get(-1) == "minus one"

    def test_tuple_keys(self) -> None:
        ht: HashTable[tuple[int, str], int] = HashTable()
        ht.put((1, "a"), 10)
        ht.put((2, "b"), 20)
        assert ht.get((1, "a")) == 10
        assert ht.get((2, "b")) == 20

    def test_large_number_of_entries(self) -> None:
        ht: HashTable[int, int] = HashTable()
        n = 1000
        for i in range(n):
            ht.put(i, i * i)
        assert ht.size == n
        for i in range(n):
            assert ht.get(i) == i * i

    def test_zero_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            HashTable[int, int](capacity=0)

    def test_delete_all_and_reuse(self) -> None:
        ht: HashTable[str, int] = HashTable()
        ht.put("a", 1)
        ht.put("b", 2)
        ht.delete("a")
        ht.delete("b")
        assert ht.size == 0
        ht.put("c", 3)
        assert ht.get("c") == 3
        assert ht.size == 1


class TestConsistentHashRing:
    def test_add_and_get_node(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        ring.add_node("node-a")
        ring.add_node("node-b")
        ring.add_node("node-c")
        node = ring.get_node("my-key")
        assert node in {"node-a", "node-b", "node-c"}

    def test_same_key_returns_same_node(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        ring.add_node("node-a")
        ring.add_node("node-b")
        n1 = ring.get_node("stable-key")
        n2 = ring.get_node("stable-key")
        assert n1 == n2

    def test_remove_node(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        ring.add_node("node-a")
        ring.add_node("node-b")
        ring.remove_node("node-a")
        node = ring.get_node("any-key")
        assert node == "node-b"

    def test_remove_unknown_node_raises(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        with pytest.raises(ValueError, match="unknown"):
            ring.remove_node("ghost")

    def test_virtual_nodes_spread_on_ring(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing(virtual_nodes=100)
        ring.add_node("node-a")
        ring.add_node("node-b")
        ring.add_node("node-c")
        result = ring.get_node("test-key")
        assert result in {"node-a", "node-b", "node-c"}

    def test_load_distribution_is_reasonably_even(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing(virtual_nodes=128)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}
        n_keys = 3000
        for i in range(n_keys):
            node = ring.get_node(f"key-{i}")
            counts[node] += 1
        expected = n_keys / 3
        for node_count in counts.values():
            deviation = abs(node_count - expected) / expected
            assert deviation < 0.30, f"{node_count} deviates {deviation:.1%} from {expected}"

    def test_adding_node_shifts_minimal_keys(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("a")
        ring.add_node("b")
        keys = [f"k{i}" for i in range(1000)]
        before: dict[str, str] = {k: ring.get_node(k) for k in keys}
        ring.add_node("c")
        after: dict[str, str] = {k: ring.get_node(k) for k in keys}
        changes = sum(1 for k in keys if before[k] != after[k])
        fraction = changes / len(keys)
        assert fraction < 0.55, f"too many keys remapped: {fraction:.1%}"

    def test_removing_node_redistributes_keys(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing(virtual_nodes=64)
        ring.add_node("a")
        ring.add_node("b")
        ring.add_node("c")
        keys = [f"k{i}" for i in range(1000)]
        before: dict[str, str] = {k: ring.get_node(k) for k in keys}
        ring.remove_node("c")
        after: dict[str, str] = {k: ring.get_node(k) for k in keys}
        changes = sum(1 for k in keys if before[k] != after[k])
        fraction = changes / len(keys)
        assert fraction < 0.55, f"too many keys remapped: {fraction:.1%}"

    def test_empty_ring_get_node_raises(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        with pytest.raises(ValueError, match="empty"):
            ring.get_node("key")

    def test_add_duplicate_node_is_idempotent(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing()
        ring.add_node("n1")
        ring.add_node("n1")
        assert ring.node_count == 1

    def test_virtual_nodes_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="virtual_nodes"):
            ConsistentHashRing(virtual_nodes=0)

    def test_many_nodes_all_reachable(self) -> None:
        ring: ConsistentHashRing = ConsistentHashRing(virtual_nodes=20)
        nodes = {f"n{i}" for i in range(50)}
        for n in nodes:
            ring.add_node(n)
        seen: set[str] = set()
        for i in range(500):
            seen.add(ring.get_node(f"key-{i}"))
        assert seen == nodes
