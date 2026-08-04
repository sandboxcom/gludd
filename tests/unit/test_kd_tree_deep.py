"""Deep KD-tree tests: build, nearest neighbor, k-NN, range search, edge cases."""

from __future__ import annotations

import math

import pytest

from general_ludd.algorithms.kd_tree import KDNode, KDTree, build_kdtree

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _euclidean(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False)))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_single_point(self) -> None:
        tree = KDTree([(1, 2)])
        assert tree.k == 2
        assert tree._root is not None
        assert tree._root.point == (1, 2)
        assert tree._root.left is None
        assert tree._root.right is None

    def test_two_points(self) -> None:
        tree = KDTree([(4, 5), (1, 2)])
        assert tree._root is not None

    def test_many_points(self) -> None:
        pts = [(i, i * 2) for i in range(50)]
        tree = KDTree(pts)
        assert tree.k == 2

    def test_empty_points_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one point"):
            KDTree([])

    def test_mismatched_dimensions(self) -> None:
        with pytest.raises(ValueError, match="same dimensionality"):
            KDTree([(1, 2), (3, 4, 5)])

    def test_3d_points(self) -> None:
        tree = KDTree([(1, 2, 3), (4, 5, 6), (7, 8, 9)])
        assert tree.k == 3

    def test_4d_points(self) -> None:
        tree = KDTree([(0, 0, 0, 0), (1, 1, 1, 1)])
        assert tree.k == 4

    def test_factory_function(self) -> None:
        tree = build_kdtree([(0, 1), (2, 3)])
        assert isinstance(tree, KDTree)
        assert tree.k == 2

    def test_build_preserves_all_points(self) -> None:
        pts = [(i % 7, (i * 3) % 11) for i in range(100)]

        def collect(node: KDNode[int] | None) -> list[tuple[int, ...]]:
            if node is None:
                return []
            return [node.point, *collect(node.left), *collect(node.right)]

        tree = KDTree(pts)
        gathered = sorted(collect(tree._root))
        expected = sorted(pts)
        assert gathered == expected


# ---------------------------------------------------------------------------
# nearest neighbour
# ---------------------------------------------------------------------------


class TestNearestNeighbor:
    def test_exact_point(self) -> None:
        tree = KDTree([(0, 0), (5, 5), (10, 10)])
        assert tree.nearest((5, 5)) == (5, 5)

    def test_between_points(self) -> None:
        tree = KDTree([(0, 0), (10, 0)])
        assert tree.nearest((5, 0)) in {(0, 0), (10, 0)}

    def test_far_target(self) -> None:
        tree = KDTree([(0, 0), (1, 1)])
        result = tree.nearest((100, 100))
        assert result is not None
        assert result in {(0, 0), (1, 1)}

    def test_dimension_mismatch(self) -> None:
        tree = KDTree([(0, 0)])
        with pytest.raises(ValueError, match="dimensionality mismatch"):
            tree.nearest((0, 0, 0))

    def test_3d_nearest(self) -> None:
        tree = KDTree([(0, 0, 0), (10, 10, 10), (5, 0, 0)])
        assert tree.nearest((5, 1, 0)) == (5, 0, 0)

    def test_4d_nearest(self) -> None:
        tree = KDTree([(0, 0, 0, 0), (1, 1, 1, 1), (5, 0, 0, 0)])
        assert tree.nearest((5, 0.1, 0, 0)) == (5, 0, 0, 0)

    def test_tie_distances(self) -> None:
        tree = KDTree([(0, 0), (0, 10)])
        result = tree.nearest((0, 5))
        assert result in {(0, 0), (0, 10)}

    def test_negative_coords(self) -> None:
        tree = KDTree([(-5, -5), (5, 5), (-3, 4)])
        assert tree.nearest((-5, -5)) == (-5, -5)

    def test_large_set(self) -> None:
        pts = [(float(i), float(i + 1)) for i in range(200)]
        tree = KDTree(pts)
        result = tree.nearest((49.5, 50.5))
        assert result is not None
        d = _euclidean(result, (49.5, 50.5))
        for pt in pts:
            assert _euclidean(pt, (49.5, 50.5)) >= d - 1e-9


# ---------------------------------------------------------------------------
# k nearest neighbours
# ---------------------------------------------------------------------------


class TestKNN:
    def test_k1(self) -> None:
        tree = KDTree([(0, 0), (10, 0), (0, 10)])
        result = tree.knn((1, 0), k=1)
        assert len(result) == 1
        assert result[0] == (0, 0)

    def test_k3(self) -> None:
        tree = KDTree([(0, 0), (1, 0), (0, 1), (10, 10)])
        result = tree.knn((0, 0), k=3)
        assert len(result) == 3
        assert (0, 0) in result

    def test_k_equals_n(self) -> None:
        pts = [(i, i) for i in range(10)]
        tree = KDTree(pts)
        result = tree.knn((0, 0), k=10)
        assert len(result) == 10
        assert result[0] == (0, 0)
        for i in range(1, 10):
            d_prev = _euclidean(result[i - 1], (0, 0))
            d_curr = _euclidean(result[i], (0, 0))
            assert d_prev <= d_curr + 1e-9

    def test_k_larger_than_n(self) -> None:
        tree = KDTree([(0, 0), (1, 1)])
        result = tree.knn((0, 0), k=5)
        assert len(result) == 2

    def test_k_zero(self) -> None:
        tree = KDTree([(0, 0), (1, 1)])
        assert tree.knn((0, 0), k=0) == []

    def test_k_negative(self) -> None:
        tree = KDTree([(0, 0)])
        assert tree.knn((0, 0), k=-1) == []

    def test_sorted_by_distance(self) -> None:
        pts = [(10, 10), (0, 0), (5, 5), (1, 1), (20, 20)]
        tree = KDTree(pts)
        result = tree.knn((0, 0), k=5)
        assert result[0] == (0, 0)
        distances = [_euclidean(r, (0, 0)) for r in result]
        assert distances == sorted(distances)

    def test_3d_knn(self) -> None:
        tree = KDTree([(0, 0, 0), (1, 1, 1), (0, 0, 10), (0, 0, 5)])
        result = tree.knn((0, 0, 0), k=2)
        assert len(result) == 2
        assert result[0] == (0, 0, 0)

    def test_dimension_mismatch(self) -> None:
        tree = KDTree([(0, 0)])
        with pytest.raises(ValueError, match="dimensionality mismatch"):
            tree.knn((0, 0, 0), k=1)


# ---------------------------------------------------------------------------
# range search
# ---------------------------------------------------------------------------


class TestRangeSearch:
    def test_range_finds_all(self) -> None:
        tree = KDTree([(1, 1), (2, 2), (3, 3)])
        result = tree.range_search((0, 0), (5, 5))
        assert sorted(result) == sorted([(1, 1), (2, 2), (3, 3)])

    def test_range_finds_none(self) -> None:
        tree = KDTree([(0, 0), (5, 5)])
        assert tree.range_search((10, 10), (20, 20)) == []

    def test_range_partial(self) -> None:
        tree = KDTree([(1, 5), (5, 1), (5, 5), (0, 0)])
        result = tree.range_search((0, 0), (3, 3))
        assert sorted(result) == sorted([(0, 0)])

    def test_range_single_dimension(self) -> None:
        tree = KDTree([(1, 10), (2, 10), (3, 10), (4, 10)])
        result = tree.range_search((1.5, 0), (3.5, 20))
        assert sorted(result) == sorted([(2, 10), (3, 10)])

    def test_range_equals_point(self) -> None:
        tree = KDTree([(5, 5)])
        result = tree.range_search((5, 5), (5, 5))
        assert result == [(5, 5)]

    def test_range_3d(self) -> None:
        tree = KDTree([(0, 0, 0), (5, 5, 5), (0, 5, 0), (5, 0, 5)])
        result = tree.range_search((0, 0, 0), (5, 5, 5))
        assert len(result) == 4

    def test_range_3d_partial(self) -> None:
        tree = KDTree([(0, 0, 0), (5, 5, 5), (0, 5, 0)])
        result = tree.range_search((0, 0, 0), (1, 10, 10))
        assert sorted(result) == sorted([(0, 0, 0), (0, 5, 0)])

    def test_bound_mismatch(self) -> None:
        tree = KDTree([(0, 0)])
        with pytest.raises(ValueError, match="dimensionality mismatch"):
            tree.range_search((0, 0, 0), (1, 1, 1))

    def test_flipped_bounds(self) -> None:
        tree = KDTree([(5, 5)])
        result = tree.range_search((10, 10), (0, 0))
        assert result == []

    def test_negative_range(self) -> None:
        tree = KDTree([(-5, -5), (0, 0), (5, 5)])
        result = tree.range_search((-3, -3), (3, 3))
        assert (0, 0) in result
        assert len(result) == 1


# ---------------------------------------------------------------------------
# KDNode
# ---------------------------------------------------------------------------


class TestKDNode:
    def test_leaf_node(self) -> None:
        node = KDNode(point=(1.0, 2.0), axis=0)
        assert node.point == (1.0, 2.0)
        assert node.axis == 0
        assert node.left is None
        assert node.right is None

    def test_node_with_children(self) -> None:
        left = KDNode(point=(1, 2), axis=1)
        right = KDNode(point=(10, 20), axis=1)
        parent = KDNode(point=(5, 10), axis=0, left=left, right=right)
        assert parent.left is left
        assert parent.right is right
        assert parent.axis == 0
