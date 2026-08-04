"""Deep tests for splay tree — splay, insert, search, delete, split, merge."""

from __future__ import annotations

import random

from general_ludd.algorithms.splay_tree import (
    SplayNode,
    splay,
    splay_contains,
    splay_delete,
    splay_get,
    splay_insert,
    splay_items,
    splay_merge,
    splay_split,
)


def _keys(root: SplayNode | None) -> list[int]:
    return [k for k, _ in splay_items(root)]


def _vals(root: SplayNode | None) -> list[str]:
    return [v for _, v in splay_items(root)]


class TestSplayRotations:
    def test_splay_single_node(self) -> None:
        root = SplayNode(5, "a")
        result = splay(root, 5)
        assert result.key == 5
        assert result.left is None
        assert result.right is None

    def test_splay_zig_left(self) -> None:
        root = SplayNode(5, "a", left=SplayNode(3, "b"))
        result = splay(root, 3)
        assert result.key == 3
        assert result.right is not None
        assert result.right.key == 5

    def test_splay_zig_right(self) -> None:
        root = SplayNode(3, "a", right=SplayNode(5, "b"))
        result = splay(root, 5)
        assert result.key == 5
        assert result.left is not None
        assert result.left.key == 3

    def test_splay_zig_zig_left_left(self) -> None:
        root = SplayNode(7, "a", left=SplayNode(5, "b", left=SplayNode(3, "c")))
        result = splay(root, 3)
        assert result.key == 3
        assert _keys(result) == [3, 5, 7]

    def test_splay_zig_zig_right_right(self) -> None:
        root = SplayNode(3, "a", right=SplayNode(5, "b", right=SplayNode(7, "c")))
        result = splay(root, 7)
        assert result.key == 7
        assert _keys(result) == [3, 5, 7]

    def test_splay_zig_zag_left_right(self) -> None:
        root = SplayNode(7, "a", left=SplayNode(3, "b", right=SplayNode(5, "c")))
        result = splay(root, 5)
        assert result.key == 5
        assert _keys(result) == [3, 5, 7]

    def test_splay_zig_zag_right_left(self) -> None:
        root = SplayNode(3, "a", right=SplayNode(7, "b", left=SplayNode(5, "c")))
        result = splay(root, 5)
        assert result.key == 5
        assert _keys(result) == [3, 5, 7]

    def test_splay_missing_key_splays_closest(self) -> None:
        root = splay_insert(None, 10, "a")
        root = splay_insert(root, 20, "b")
        root = splay_insert(root, 30, "c")
        result = splay(root, 25)
        assert result.key in (20, 30)

    def test_splay_deep_tree(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(20):
            root = splay_insert(root, k, str(k))
        result = splay(root, 0)
        assert result.key == 0
        assert _keys(result) == list(range(20))


class TestSplayInsertSearch:
    def test_insert_into_empty(self) -> None:
        root = splay_insert(None, 10, "x")
        assert root.key == 10
        assert root.val == "x"
        assert _keys(root) == [10]

    def test_insert_splays_inserted_key(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in [5, 3, 7, 2, 4, 6, 8]:
            root = splay_insert(root, k, str(k))
        root = splay_insert(root, 1, "one")
        assert root.key == 1

    def test_insert_overwrite(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_insert(root, 5, "b")
        assert root.key == 5
        assert root.val == "b"
        assert _keys(root) == [5]

    def test_search_updates_root(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(10):
            root = splay_insert(root, k, str(k))
        result, root = splay_get(root, 0)
        assert result == "0"
        assert root is not None
        assert root.key == 0

    def test_search_missing_returns_none(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in [2, 4, 6]:
            root = splay_insert(root, k, str(k))
        result, root = splay_get(root, 3)
        assert result is None
        assert root is not None

    def test_contains(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(1, 100, 2):
            root = splay_insert(root, k, str(k))
        found, root = splay_contains(root, 51)
        assert found is True
        found, root = splay_contains(root, 52)
        assert found is False

    def test_insert_many_always_valid_bst(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(100):
            root = splay_insert(root, k, str(k))
        assert _keys(root) == list(range(100))


class TestSplayDelete:
    def test_delete_leaf(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_insert(root, 3, "b")
        root = splay_delete(root, 3)
        assert root is not None
        assert _keys(root) == [5]

    def test_delete_root_only_node(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_delete(root, 5)
        assert root is None

    def test_delete_node_with_one_child(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_insert(root, 3, "b")
        root = splay_insert(root, 7, "c")
        root = splay_delete(root, 5)
        assert root is not None
        assert 5 not in _keys(root)
        assert _keys(root) == [3, 7]

    def test_delete_node_with_two_children(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_insert(root, 3, "b")
        root = splay_insert(root, 7, "c")
        root = splay_insert(root, 4, "d")
        root = splay_delete(root, 5)
        assert root is not None
        assert 5 not in _keys(root)
        assert sorted(_keys(root)) == [3, 4, 7]

    def test_delete_missing_does_nothing(self) -> None:
        root = splay_insert(None, 5, "a")
        root = splay_insert(root, 3, "b")
        root = splay_delete(root, 99)
        assert _keys(root) == [3, 5]

    def test_delete_all_one_by_one(self) -> None:
        root: SplayNode[int, str] | None = None
        keys = list(range(10))
        for k in keys:
            root = splay_insert(root, k, str(k))
        random.shuffle(keys)
        for k in keys:
            root = splay_delete(root, k)
        assert root is None

    def test_delete_from_empty(self) -> None:
        assert splay_delete(None, 1) is None


class TestSplaySplit:
    def test_split_empty(self) -> None:
        a, b = splay_split(None, 5)
        assert a is None
        assert b is None

    def test_split_all_left(self) -> None:
        root = splay_insert(None, 3, "a")
        a, b = splay_split(root, 10)
        assert _keys(a) == [3]
        assert b is None

    def test_split_all_right(self) -> None:
        root = splay_insert(None, 7, "a")
        a, b = splay_split(root, 2)
        assert a is None
        assert _keys(b) == [7]

    def test_split_boundary(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in [2, 5, 8]:
            root = splay_insert(root, k, str(k))
        a, b = splay_split(root, 5)
        assert _keys(a) == [2, 5]
        assert _keys(b) == [8]

    def test_split_large(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(50):
            root = splay_insert(root, k, str(k))
        a, b = splay_split(root, 24)
        assert _keys(a) == list(range(25))
        assert _keys(b) == list(range(25, 50))


class TestSplayMerge:
    def test_merge_with_none(self) -> None:
        root = SplayNode(5, "a")
        assert splay_merge(root, None) is root
        assert splay_merge(None, root) is root
        assert splay_merge(None, None) is None

    def test_merge_disjoint(self) -> None:
        left = splay_insert(None, 2, "a")
        right = splay_insert(None, 8, "b")
        merged = splay_merge(left, right)
        assert _keys(merged) == [2, 8]

    def test_merge_many(self) -> None:
        left: SplayNode[int, str] | None = None
        for k in range(0, 10):
            left = splay_insert(left, k, str(k))
        right: SplayNode[int, str] | None = None
        for k in range(10, 20):
            right = splay_insert(right, k, str(k))
        merged = splay_merge(left, right)
        assert _keys(merged) == list(range(20))

    def test_split_then_merge_roundtrip(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(20):
            root = splay_insert(root, k, str(k))
        a, b = splay_split(root, 9)
        merged = splay_merge(a, b)
        assert _keys(merged) == list(range(20))


class TestSplayPropertyChecks:
    def test_bst_invariant_after_many_inserts(self) -> None:
        root: SplayNode[int, str] | None = None
        keys = list(range(100))
        random.shuffle(keys)
        for k in keys:
            root = splay_insert(root, k, str(k))
        assert _keys(root) == list(range(100))

    def test_bst_invariant_after_insert_delete_interleave(self) -> None:
        root: SplayNode[int, str] | None = None
        present: set[int] = set()
        for _ in range(200):
            op = random.choice(["insert", "delete"])
            if op == "insert":
                k = random.randint(0, 99)
                root = splay_insert(root, k, str(k))
                present.add(k)
            elif present:
                k = random.choice(list(present))
                root = splay_delete(root, k)
                present.discard(k)
        assert _keys(root) == sorted(present)

    def test_recent_access_stays_near_root(self) -> None:
        root: SplayNode[int, str] | None = None
        for k in range(20):
            root = splay_insert(root, k, str(k))
        _, root = splay_get(root, 15)
        assert root is not None
        assert root.key == 15
        _, root = splay_get(root, 7)
        assert root is not None
        assert root.key == 7
