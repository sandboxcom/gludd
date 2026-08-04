"""Deep B+ tree tests: insert, search, delete, split/merge, range scan, invariants."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.storage.bptree import BPlusTree


class TestEmptyTree:
    def test_search_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert tree.search(1) is None

    def test_len_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert len(tree) == 0

    def test_contains_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert 1 not in tree

    def test_keys_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert tree.keys() == []

    def test_items_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert tree.items() == []

    def test_range_scan_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert tree.search_range(0, 100) == []

    def test_delete_empty(self) -> None:
        tree = BPlusTree(order=4)
        assert tree.delete(1) is False


class TestInsertSearch:
    def test_insert_one(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(5, "five")
        assert tree.search(5) == "five"
        assert len(tree) == 1

    def test_insert_duplicate_updates_value(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "original")
        tree.insert(1, "updated")
        assert tree.search(1) == "updated"
        assert len(tree) == 1

    def test_multiple_inserts_search_all(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(20):
            tree.insert(i, i * 10)
        for i in range(20):
            assert tree.search(i) == i * 10
        assert len(tree) == 20

    def test_insert_negative_keys(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(-5, "neg")
        tree.insert(0, "zero")
        tree.insert(5, "pos")
        assert tree.search(-5) == "neg"
        assert tree.search(0) == "zero"
        assert tree.search(5) == "pos"

    def test_search_missing_key(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "a")
        assert tree.search(2) is None


class TestLeafSplits:
    def test_insert_causes_leaf_split_order4(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(5):
            tree.insert(i, str(i))
        for i in range(5):
            assert tree.search(i) == str(i)
        assert len(tree) == 5

    def test_ascending_insert_many_splits(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(50):
            tree.insert(i, f"val-{i}")
        assert len(tree) == 50
        for i in range(50):
            assert tree.search(i) == f"val-{i}"

    def test_descending_insert_many_splits(self) -> None:
        tree = BPlusTree(order=4)
        for i in reversed(range(50)):
            tree.insert(i, f"v-{i}")
        assert len(tree) == 50
        for i in range(50):
            assert tree.search(i) == f"v-{i}"

    def test_interleaved_insert_splits(self) -> None:
        tree = BPlusTree(order=4)
        values = [50, 10, 70, 30, 90, 20, 60, 40, 80, 0, 100]
        for v in values:
            tree.insert(v, f"x-{v}")
        assert len(tree) == len(values)
        for v in values:
            assert tree.search(v) == f"x-{v}"


class TestSearchRange:
    def test_range_single_key(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(10):
            tree.insert(i, i * 2)
        assert tree.search_range(5, 5) == [(5, 10)]

    def test_range_spanning_multiple_leaves(self) -> None:
        tree = BPlusTree(order=3)
        for i in range(20):
            tree.insert(i, f"val-{i}")
        r = tree.search_range(0, 19)
        assert len(r) == 20
        assert r[0] == (0, "val-0")
        assert r[-1] == (19, "val-19")

    def test_range_subset_within_leaf(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(10):
            tree.insert(i, i)
        assert tree.search_range(3, 7) == [(i, i) for i in range(3, 8)]

    def test_range_bounds_outside_data(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(5, "a")
        tree.insert(10, "b")
        assert tree.search_range(0, 3) == []
        assert tree.search_range(20, 25) == []
        assert tree.search_range(0, 100) == [(5, "a"), (10, "b")]

    def test_range_open_lower_bound(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(5):
            tree.insert(i, i)
        assert tree.search_range(-999, 2) == [(0, 0), (1, 1), (2, 2)]

    def test_range_reverse_order_bounds(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(5):
            tree.insert(i, i)
        assert tree.search_range(3, 1) == []


class TestSimpleDelete:
    def test_delete_only_key(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "a")
        assert tree.delete(1) is True
        assert tree.search(1) is None
        assert len(tree) == 0

    def test_delete_nonexistent(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "a")
        assert tree.delete(2) is False
        assert len(tree) == 1

    def test_delete_from_leaf_no_underflow(self) -> None:
        tree = BPlusTree(order=3)
        for i in range(4):
            tree.insert(i, f"val-{i}")
        assert tree.delete(1) is True
        assert tree.search(1) is None
        assert len(tree) == 3
        for i in [0, 2, 3]:
            assert tree.search(i) is not None


class TestDeleteBorrowMerge:
    def test_delete_causes_borrow_from_right(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(10):
            tree.insert(i, i)
        assert tree.delete(0) is True
        assert tree.search(0) is None
        for i in range(1, 10):
            assert tree.search(i) == i

    def test_delete_causes_borrow_from_left(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(10):
            tree.insert(i, i)
        assert tree.delete(9) is True
        assert tree.search(9) is None
        for i in range(9):
            assert tree.search(i) == i

    def test_delete_causes_merge(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(8):
            tree.insert(i, i)
        for i in range(3):
            tree.delete(i)
        assert len(tree) == 5
        for i in range(3, 8):
            assert tree.search(i) == i

    def test_delete_all_keys_one_by_one(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(20):
            tree.insert(i, i)
        for i in range(20):
            assert tree.delete(i) is True
        assert len(tree) == 0
        assert tree.keys() == []

    def test_delete_descending_order(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(15):
            tree.insert(i, i)
        for i in reversed(range(15)):
            assert tree.delete(i) is True
        assert len(tree) == 0

    def test_delete_from_internal_with_predecessor(self) -> None:
        tree = BPlusTree(order=3)
        for i in range(15):
            tree.insert(i, chrd(97 + i % 26))
        assert tree.delete(7) is True
        assert tree.search(7) is None
        for i in range(15):
            if i != 7:
                assert tree.search(i) is not None


class TestStringKeys:
    def test_string_keys_insert_search(self) -> None:
        tree = BPlusTree(order=4)
        words = ["apple", "banana", "cherry", "date", "elderberry"]
        for i, w in enumerate(words):
            tree.insert(w, i)
        assert tree.search("cherry") == 2
        assert tree.search("banana") == 1

    def test_string_range_scan(self) -> None:
        tree = BPlusTree(order=4)
        words = ["ape", "bat", "cat", "dog", "eel", "fox"]
        for i, w in enumerate(words):
            tree.insert(w, i)
        rows = tree.search_range("bat", "eel")
        assert rows == [("bat", 1), ("cat", 2), ("dog", 3), ("eel", 4)]

    def test_string_delete_then_search(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert("a", 1)
        tree.insert("b", 2)
        tree.insert("c", 3)
        assert tree.delete("b") is True
        assert tree.search("b") is None
        assert tree.search("a") == 1
        assert tree.search("c") == 3


class TestStructuralInvariants:
    @staticmethod
    def _all_at_same_depth(node, depth: int = 0) -> bool:
        if node.is_leaf:
            return True
        first_child_depth: int | None = None

        def _check(n, d: int) -> bool:
            nonlocal first_child_depth
            if n.is_leaf:
                if first_child_depth is None:
                    first_child_depth = d
                return d == first_child_depth
            return all(_check(child, d + 1) for child in n.children)

        return _check(node, depth)

    def test_all_leaves_same_depth(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(100):
            tree.insert(i, i)
        assert self._all_at_same_depth(tree._root)

    def test_leaf_chain_integrity(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(50):
            tree.insert(i, i)
        leaf = tree._leftmost_leaf()
        keys_seen: list[Any] = []
        while leaf is not None:
            keys_seen.extend(leaf.keys)
            leaf = leaf.next_leaf
        assert keys_seen == sorted(keys_seen)
        assert len(keys_seen) == 50

    def test_leaf_chain_after_deletes(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(30):
            tree.insert(i, i)
        for i in range(5, 15):
            tree.delete(i)
        leaf = tree._leftmost_leaf()
        keys_seen: list[Any] = []
        while leaf is not None:
            keys_seen.extend(leaf.keys)
            leaf = leaf.next_leaf
        assert keys_seen == sorted(keys_seen)
        for k in keys_seen:
            assert tree.search(k) is not None


class TestLargeScale:
    def test_insert_1000_keys_sequential(self) -> None:
        tree = BPlusTree(order=8)
        N = 1000
        for i in range(N):
            tree.insert(i, i * 3)
        assert len(tree) == N
        assert tree.search(0) == 0
        assert tree.search(N - 1) == (N - 1) * 3
        assert tree.search(N // 2) == (N // 2) * 3

    def test_insert_delete_interleave(self) -> None:
        tree = BPlusTree(order=5)
        for i in range(100):
            tree.insert(i, i)
        for i in range(0, 100, 2):
            assert tree.delete(i) is True
        for i in range(100):
            if i % 2 == 0:
                assert tree.search(i) is None
            else:
                assert tree.search(i) == i
        assert len(tree) == 50

    def test_items_returns_all_sorted(self) -> None:
        tree = BPlusTree(order=4)
        for x in [50, 10, 70, 30, 90, 20, 60, 40, 80]:
            tree.insert(x, x * 10)
        result = tree.items()
        assert result == sorted(result, key=lambda p: p[0])
        assert [k for k, _ in result] == sorted([10, 20, 30, 40, 50, 60, 70, 80, 90])

    def test_keys_returns_all_sorted(self) -> None:
        tree = BPlusTree(order=4)
        for x in [30, 10, 20, 50, 40]:
            tree.insert(x, 0)
        assert tree.keys() == [10, 20, 30, 40, 50]


class TestOrderVariants:
    def test_order3_small_fanout(self) -> None:
        tree = BPlusTree(order=3)
        for i in range(50):
            tree.insert(i, i)
        assert len(tree) == 50

    def test_order5_medium_fanout(self) -> None:
        tree = BPlusTree(order=5)
        for i in range(100):
            tree.insert(i, i)
        assert len(tree) == 100
        for i in range(100):
            assert tree.search(i) == i

    def test_order_invalid_too_small(self) -> None:
        with pytest.raises(ValueError, match="order must be >= 3"):
            BPlusTree(order=2)

    def test_order_invalid_negative(self) -> None:
        with pytest.raises(ValueError):
            BPlusTree(order=-1)


class TestEdgeCases:
    def test_contains(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(5, "v")
        assert 5 in tree
        assert 6 not in tree

    def test_update_existing_key_maintains_size(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "a")
        tree.insert(2, "b")
        tree.insert(1, "aa")
        assert len(tree) == 2
        assert tree.search(1) == "aa"

    def test_range_spanning_empty_gap(self) -> None:
        tree = BPlusTree(order=4)
        tree.insert(1, "a")
        tree.insert(100, "b")
        assert tree.search_range(1, 100) == [(1, "a"), (100, "b")]
        assert tree.search_range(50, 60) == []

    def test_delete_last_key_collapses_root(self) -> None:
        tree = BPlusTree(order=4)
        for i in range(10):
            tree.insert(i, i)
        for i in range(9):
            tree.delete(i)
        assert len(tree) == 1
        assert tree.search(9) == 9
        tree.delete(9)
        assert len(tree) == 0
        assert tree.search(9) is None


def chrd(n: int) -> str:
    return chr(97 + n)
