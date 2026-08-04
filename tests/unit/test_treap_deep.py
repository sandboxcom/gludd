"""Deep tests for treap (BST split/merge + implicit treap)."""

from __future__ import annotations

import random

from general_ludd.algorithms.treap import (
    ImplicitNode,
    TreapNode,
    implicit_delete_at,
    implicit_get,
    implicit_insert_at,
    implicit_merge,
    implicit_push_back,
    implicit_range_query,
    implicit_size,
    implicit_split,
    implicit_to_list,
    treap_contains,
    treap_delete,
    treap_get,
    treap_insert,
    treap_items,
    treap_merge,
    treap_split,
)


class TestTreapSplitMerge:
    def test_split_empty(self) -> None:
        a, b = treap_split(None, 5)
        assert a is None
        assert b is None

    def test_split_all_go_left(self) -> None:
        root = TreapNode(3, "x")
        a, b = treap_split(root, 10)
        assert a is not None
        assert b is None
        assert treap_items(a) == [(3, "x")]

    def test_split_all_go_right(self) -> None:
        root = TreapNode(7, "x")
        a, b = treap_split(root, 2)
        assert a is None
        assert b is not None
        assert treap_items(b) == [(7, "x")]

    def test_split_boundary(self) -> None:
        root = treap_insert(treap_insert(None, 2, "b"), 5, "e")
        a, b = treap_split(root, 3)
        assert treap_items(a) == [(2, "b")]
        assert treap_items(b) == [(5, "e")]

    def test_merge_disjoint(self) -> None:
        left = treap_insert(None, 1, "one")
        right = treap_insert(None, 9, "nine")
        merged = treap_merge(left, right)
        assert treap_items(merged) == [(1, "one"), (9, "nine")]

    def test_merge_with_none(self) -> None:
        root = TreapNode(4, "v")
        assert treap_merge(root, None) is root
        assert treap_merge(None, root) is root

    def test_merge_many(self) -> None:
        left = treap_insert(treap_insert(None, 1, "a"), 3, "b")
        right = treap_insert(treap_insert(None, 7, "c"), 9, "d")
        merged = treap_merge(left, right)
        assert treap_items(merged) == [(1, "a"), (3, "b"), (7, "c"), (9, "d")]


class TestTreapInsertDeleteContains:
    def test_insert_into_empty(self) -> None:
        root = treap_insert(None, 10, "x")
        assert treap_items(root) == [(10, "x")]

    def test_insert_and_contains_many(self) -> None:
        root: TreapNode[int, str] | None = None
        keys = [5, 2, 8, 1, 3, 7, 9]
        for k in keys:
            root = treap_insert(root, k, str(k))
        assert treap_items(root) == [(k, str(k)) for k in sorted(keys)]
        for k in keys:
            assert treap_contains(root, k)
        assert not treap_contains(root, 0)
        assert not treap_contains(root, 10)

    def test_insert_overwrite(self) -> None:
        root = treap_insert(None, 5, "first")
        root = treap_insert(root, 5, "second")
        assert treap_items(root) == [(5, "second")]
        assert treap_get(root, 5) == "second"

    def test_get_default(self) -> None:
        root = treap_insert(None, 1, "hi")
        assert treap_get(root, 99) is None
        assert treap_get(root, 99, "fallback") == "fallback"

    def test_delete_leaf(self) -> None:
        root = treap_insert(None, 7, "x")
        root = treap_delete(root, 7)
        assert root is None

    def test_delete_non_existent(self) -> None:
        root = treap_insert(treap_insert(None, 1, "a"), 2, "b")
        root = treap_delete(root, 99)
        assert treap_items(root) == [(1, "a"), (2, "b")]

    def test_delete_many(self) -> None:
        root: TreapNode[int, str] | None = None
        for k in range(20):
            root = treap_insert(root, k, str(k))
        for k in [3, 7, 11, 15, 19]:
            root = treap_delete(root, k)
        expected = [k for k in range(20) if k not in (3, 7, 11, 15, 19)]
        assert treap_items(root) == [(k, str(k)) for k in expected]

    def test_random_insert_delete_contains(self) -> None:
        rng = random.Random(42)
        ref: set[int] = set()
        root: TreapNode[int, int] | None = None
        for _ in range(500):
            op = rng.randint(0, 2)
            k = rng.randint(0, 99)
            if op == 0:
                root = treap_insert(root, k, k)
                ref.add(k)
            elif op == 1 and ref:
                k = rng.choice(sorted(ref))
                root = treap_delete(root, k)
                ref.discard(k)
            else:
                assert treap_contains(root, k) == (k in ref)
        expected = sorted(ref)
        assert treap_items(root) == [(k, k) for k in expected]


class TestImplicitSplitMerge:
    def test_split_empty(self) -> None:
        a, b = implicit_split(None, 0)
        assert a is None and b is None

    def test_split_at_zero(self) -> None:
        root = _build_implicit(["a", "b", "c"])
        a, b = implicit_split(root, 0)
        assert implicit_to_list(a) == []
        assert implicit_to_list(b) == ["a", "b", "c"]

    def test_split_at_end(self) -> None:
        root = _build_implicit(["a", "b", "c"])
        a, b = implicit_split(root, 3)
        assert implicit_to_list(a) == ["a", "b", "c"]
        assert implicit_to_list(b) == []

    def test_split_middle(self) -> None:
        root = _build_implicit(["x", "y", "z", "w"])
        a, b = implicit_split(root, 2)
        assert implicit_to_list(a) == ["x", "y"]
        assert implicit_to_list(b) == ["z", "w"]

    def test_merge_disjoint(self) -> None:
        a = _build_implicit([1, 2])
        b = _build_implicit([3, 4])
        m = implicit_merge(a, b)
        assert implicit_to_list(m) == [1, 2, 3, 4]

    def test_merge_with_none(self) -> None:
        node = ImplicitNode("solo")
        assert implicit_merge(None, node) is node
        assert implicit_merge(node, None) is node


class TestImplicitOps:
    def test_push_back(self) -> None:
        root: ImplicitNode[str] | None = None
        for ch in ["a", "b", "c"]:
            root = implicit_push_back(root, ch)
        assert implicit_to_list(root) == ["a", "b", "c"]

    def test_insert_at(self) -> None:
        root = _build_implicit(["a", "c"])
        root = implicit_insert_at(root, 1, "b")
        assert implicit_to_list(root) == ["a", "b", "c"]

    def test_insert_at_front(self) -> None:
        root = _build_implicit(["b", "c"])
        root = implicit_insert_at(root, 0, "a")
        assert implicit_to_list(root) == ["a", "b", "c"]

    def test_delete_at(self) -> None:
        root = _build_implicit(["a", "x", "b"])
        root = implicit_delete_at(root, 1)
        assert implicit_to_list(root) == ["a", "b"]

    def test_delete_at_bounds(self) -> None:
        root = _build_implicit(["a"])
        root = implicit_delete_at(root, 0)
        assert root is None

    def test_get(self) -> None:
        root = _build_implicit([10, 20, 30])
        assert implicit_get(root, 0) == 10
        assert implicit_get(root, 1) == 20
        assert implicit_get(root, 2) == 30
        assert implicit_get(root, -1) is None
        assert implicit_get(root, 99) is None
        assert implicit_get(None, 0) is None

    def test_size(self) -> None:
        assert implicit_size(None) == 0
        root = _build_implicit(["x", "y", "z"])
        assert implicit_size(root) == 3

    def test_random_ops_match_list(self) -> None:
        rng = random.Random(99)
        ref: list[int] = []
        root: ImplicitNode[int] | None = None
        for _ in range(500):
            op = rng.randint(0, 3)
            if op == 0:
                v = rng.randint(0, 999)
                pos = rng.randint(0, len(ref))
                ref.insert(pos, v)
                root = implicit_insert_at(root, pos, v)
            elif op == 1 and ref:
                pos = rng.randint(0, len(ref) - 1)
                del ref[pos]
                root = implicit_delete_at(root, pos)
            elif op == 2:
                pos = rng.randint(-1, len(ref) + 2)
                expected = ref[pos] if 0 <= pos < len(ref) else None
                assert implicit_get(root, pos) == expected
            else:
                assert implicit_to_list(root) == ref
                assert implicit_size(root) == len(ref)
        assert implicit_to_list(root) == ref

    def test_range_query_sum(self) -> None:
        root = _build_implicit([3, 1, 4, 1, 5, 9, 2, 6])
        total = implicit_range_query(root, 2, 5, lambda a, b: a + b, 0)
        assert total == 10  # 4 + 1 + 5

    def test_range_query_empty(self) -> None:
        root = _build_implicit([1, 2, 3])
        assert implicit_range_query(root, 2, 2, lambda a, b: a + b, 0) == 0
        assert implicit_range_query(root, 3, 5, lambda a, b: a + b, 0) == 0

    def test_range_query_full(self) -> None:
        root = _build_implicit([7, 8, 9])
        total = implicit_range_query(root, 0, 3, lambda a, b: a * b, 1)
        assert total == 504  # 7 * 8 * 9


class TestEdgeCases:
    def test_large_ordered_batch(self) -> None:
        root: TreapNode[int, int] | None = None
        for i in range(200):
            root = treap_insert(root, i, i)
        for i in range(0, 200, 2):
            root = treap_delete(root, i)
        expected = [(i, i) for i in range(1, 200, 2)]
        assert treap_items(root) == expected

    def test_stress_split_merge(self) -> None:
        rng = random.Random(7)
        root: TreapNode[int, int] | None = None
        for _ in range(300):
            k = rng.randint(0, 199)
            root = treap_insert(root, k, k)
        for _ in range(100):
            k = rng.randint(0, 199)
            root = treap_delete(root, k)
        for _ in range(100):
            k = rng.randint(0, 199)
            root = treap_insert(root, k, k)
        items = treap_items(root)
        keys = [k for k, _ in items]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))


def _build_implicit(values: list[int | str]) -> ImplicitNode[int | str]:
    root: ImplicitNode[int | str] | None = None
    for v in values:
        root = implicit_push_back(root, v)
    assert root is not None
    return root


def _build_implicit_int(values: list[int]) -> ImplicitNode[int]:
    root: ImplicitNode[int] | None = None
    for v in values:
        root = implicit_push_back(root, v)
    assert root is not None
    return root
