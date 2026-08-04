"""Deep Cartesian tree tests: build, RMQ via LCA, Euler tour, edge cases.

Covers build_cartesian_tree, EulerTour, cartesian_rmq, inorder, and
inorder_indices with 18 tests including edge cases and property checks.
"""

from __future__ import annotations

import random

import pytest

from general_ludd.algorithms.cartesian_tree import (
    CartesianNode,
    EulerTour,
    build_cartesian_tree,
    cartesian_rmq,
    cartesian_rmq_value,
    inorder,
    inorder_indices,
)


def _naive_rmq_index(arr: list[int], i: int, j: int) -> int:
    lo, hi = (i, j) if i <= j else (j, i)
    best = lo
    for k in range(lo, hi + 1):
        if arr[k] < arr[best]:
            best = k
    return best


def _is_min_heap(node: CartesianNode | None) -> bool:
    if node is None:
        return True
    if node.left is not None and node.left.value < node.value:
        return False
    if node.right is not None and node.right.value < node.value:
        return False
    return _is_min_heap(node.left) and _is_min_heap(node.right)


# ── build_cartesian_tree ──────────────────────────────────────────────


def test_ct_empty_array_returns_none():
    assert build_cartesian_tree([]) is None


def test_ct_single_element():
    root = build_cartesian_tree([42])
    assert root is not None
    assert root.index == 0
    assert root.value == 42
    assert root.left is None
    assert root.right is None


def test_ct_two_elements_ascending():
    root = build_cartesian_tree([10, 20])
    assert root is not None
    assert root.value == 10
    assert root.left is None
    assert root.right is not None
    assert root.right.value == 20


def test_ct_two_elements_descending():
    root = build_cartesian_tree([20, 10])
    assert root is not None
    assert root.value == 10
    assert root.left is not None
    assert root.left.value == 20
    assert root.right is None


def test_ct_sorted_ascending():
    arr = [1, 2, 3, 4, 5]
    root = build_cartesian_tree(arr)
    assert root is not None
    assert root.value == 1
    current = root
    for v in arr[1:]:
        assert current.right is not None
        assert current.left is None
        current = current.right
        assert current.value == v


def test_ct_sorted_descending():
    arr = [5, 4, 3, 2, 1]
    root = build_cartesian_tree(arr)
    assert root is not None
    assert root.value == 1
    current = root
    for v in [2, 3, 4, 5]:
        assert current.left is not None
        assert current.right is None
        current = current.left
        assert current.value == v


def test_ct_min_heap_property_random():
    rng = random.Random(42)
    for _ in range(50):
        arr = [rng.randint(-1000, 1000) for _ in range(rng.randint(1, 200))]
        root = build_cartesian_tree(arr)
        assert root is not None
        assert _is_min_heap(root)


def test_ct_inorder_preserves_array():
    rng = random.Random(7)
    for _ in range(30):
        arr = [rng.randint(-500, 500) for _ in range(rng.randint(1, 100))]
        root = build_cartesian_tree(arr)
        assert inorder(root) == list(arr)


def test_ct_inorder_indices_monotonic():
    rng = random.Random(13)
    for _ in range(20):
        arr = [rng.randint(-100, 100) for _ in range(rng.randint(1, 80))]
        root = build_cartesian_tree(arr)
        indices = inorder_indices(root)
        assert indices == list(range(len(arr)))


def test_ct_parent_pointers_consistent():
    arr = [7, 2, 8, 1, 6, 4, 3, 5, 9]
    root = build_cartesian_tree(arr)
    assert root is not None

    def _check(n: CartesianNode | None) -> None:
        if n is None:
            return
        if n.left is not None:
            assert n.left.parent is n
            _check(n.left)
        if n.right is not None:
            assert n.right.parent is n
            _check(n.right)

    _check(root)


# ── EulerTour ─────────────────────────────────────────────────────────


def test_et_empty_root():
    tour = EulerTour(None)
    assert tour.root_index is None
    assert tour.tour_sequence() == []


def test_et_single_node():
    root = build_cartesian_tree([99])
    tour = EulerTour(root)
    assert tour.root_index == 0
    seq = tour.tour_sequence()
    assert len(seq) == 1
    assert seq[0] == (0, 0)


def test_et_first_occurrence_all_nodes():
    arr = [4, 1, 7, 3, 5, 2, 6]
    root = build_cartesian_tree(arr)
    tour = EulerTour(root)
    for i in range(len(arr)):
        fo = tour.first_occurrence(i)
        assert fo >= 0
        assert tour.tour_sequence()[fo][1] == i


def test_et_two_nodes_lca():
    arr = [5, 3, 7, 1, 6]
    root = build_cartesian_tree(arr)
    tour = EulerTour(root)
    assert tour.lca_index(0, 4) == _naive_rmq_index(arr, 0, 4)
    assert tour.lca_index(1, 3) == _naive_rmq_index(arr, 1, 3)


# ── cartesian_rmq ─────────────────────────────────────────────────────


def test_crmq_single_range():
    arr = [10, 5, 8, 3, 12, 7]
    assert cartesian_rmq(arr, 0, 0) == 0
    assert cartesian_rmq(arr, 5, 5) == 5


def test_crmq_empty_array_raises():
    with pytest.raises(ValueError, match="empty array"):
        cartesian_rmq([], 0, 0)


@pytest.mark.parametrize(
    "arr",
    [
        [5, 2, 8, 1, 9, 3, 7],
        [1, 2, 3, 4, 5],
        [5, 4, 3, 2, 1],
        [3, 1, 4, 1, 5, 9, 2, 6],
        [42],
        [2, 2, 2, 2],
    ],
)
def test_crmq_agrees_with_naive(arr: list[int]):
    n = len(arr)
    for i in range(n):
        for j in range(i, n):
            expected = _naive_rmq_index(arr, i, j)
            got = cartesian_rmq(arr, i, j)
            assert got == expected, f"arr={arr}, i={i}, j={j}"


def test_crmq_swapped_order():
    arr = [9, 4, 7, 2, 8, 1, 5]
    assert cartesian_rmq(arr, 5, 1) == _naive_rmq_index(arr, 1, 5)


def test_crmq_value_wrapper():
    arr = [8, 3, 6, 1, 7, 4, 9]
    for i in range(len(arr)):
        for j in range(i, len(arr)):
            assert cartesian_rmq_value(arr, i, j) == min(arr[i : j + 1])


# ── Stress tests ──────────────────────────────────────────────────────


def test_ct_stress_large_random():
    rng = random.Random(123)
    for size in [100, 500, 1000, 2000]:
        arr = [rng.randint(-10000, 10000) for _ in range(size)]
        root = build_cartesian_tree(arr)
        assert root is not None
        assert _is_min_heap(root)
        assert inorder(root) == list(arr)
        tour = EulerTour(root)
        for _ in range(200):
            i = rng.randint(0, size - 1)
            j = rng.randint(0, size - 1)
            expected = _naive_rmq_index(arr, i, j)
            assert tour.lca_index(i, j) == expected


def test_ct_depth_monotonic():
    arr = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 8]
    root = build_cartesian_tree(arr)
    tour = EulerTour(root)
    depths = [d for d, _ in tour.tour_sequence()]
    for i in range(len(depths) - 1):
        assert abs(depths[i + 1] - depths[i]) == 1, f"Euler tour depth change must be ±1 at step {i}"


def test_ct_tour_size_formula():
    rng = random.Random(99)
    for n in range(1, 50):
        arr = [rng.randint(0, 100) for _ in range(n)]
        root = build_cartesian_tree(arr)
        tour = EulerTour(root)
        seq = tour.tour_sequence()
        assert 2 * n - 1 <= len(seq) <= 2 * n - 1, f"Euler tour for n={n} should have 2n-1 entries, got {len(seq)}"


def test_ct_branching_tree():
    arr = [10, 2, 8, 1, 5, 9, 3, 7, 4, 6]
    root = build_cartesian_tree(arr)
    assert root is not None
    assert root.value == 1
    left = root.left
    right = root.right
    assert left is not None and right is not None
    assert left.value == 2
    assert right.value == 3
