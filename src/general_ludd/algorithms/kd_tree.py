"""KD-tree: k-dimensional tree for spatial partitioning and search.

Operations: build, nearest neighbour, k-NN, range search.
O(n log n) build, O(log n) expected query, O(n^{1-1/k}) worst-case.
Pure-Python, stdlib only.
"""

from __future__ import annotations

import heapq
from typing import TypeVar

K = TypeVar("K", bound=float)
_Point = tuple[K, ...]


class KDNode:
    __slots__ = ("axis", "left", "point", "right")

    def __init__(
        self,
        point: _Point[float],
        axis: int,
        left: KDNode | None = None,
        right: KDNode | None = None,
    ) -> None:
        self.point = point
        self.axis = axis
        self.left = left
        self.right = right


def _build(points: list[_Point[float]], depth: int = 0) -> KDNode | None:
    if not points:
        return None
    k = len(points[0])
    axis = depth % k
    points.sort(key=lambda p: p[axis])
    median = len(points) // 2
    return KDNode(
        point=points[median],
        axis=axis,
        left=_build(points[:median], depth + 1),
        right=_build(points[median + 1 :], depth + 1),
    )


def _squared_dist(a: _Point[float], b: _Point[float]) -> float:
    return sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False))


def _nn(
    node: KDNode | None,
    target: _Point[float],
    best: tuple[float, _Point[float] | None],
) -> tuple[float, _Point[float] | None]:
    if node is None:
        return best

    d = _squared_dist(node.point, target)
    if best[1] is None or d < best[0]:
        best = (d, node.point)

    axis = node.axis
    diff = target[axis] - node.point[axis]

    near = node.left if diff <= 0 else node.right
    far = node.right if diff <= 0 else node.left

    best = _nn(near, target, best)

    if far is not None and diff * diff < best[0]:
        best = _nn(far, target, best)

    return best


def _knn(
    node: KDNode | None,
    target: _Point[float],
    k: int,
    heap: list[tuple[float, _Point[float]]],
) -> list[tuple[float, _Point[float]]]:
    if node is None:
        return heap

    d = _squared_dist(node.point, target)
    heapq.heappush(heap, (-d, node.point))
    if len(heap) > k:
        heapq.heappop(heap)

    axis = node.axis
    diff = target[axis] - node.point[axis]

    near = node.left if diff <= 0 else node.right
    far = node.right if diff <= 0 else node.left

    _knn(near, target, k, heap)

    worst = -heap[0][0] if heap else float("inf")
    if far is not None and diff * diff < worst:
        _knn(far, target, k, heap)

    return heap


def _range_search(
    node: KDNode | None,
    lower: _Point[float],
    upper: _Point[float],
    results: list[_Point[float]],
) -> list[_Point[float]]:
    if node is None:
        return results

    point = node.point
    inside = all(lo <= p <= hi for lo, p, hi in zip(lower, point, upper, strict=False))
    if inside:
        results.append(point)

    axis = node.axis
    if lower[axis] <= point[axis]:
        _range_search(node.left, lower, upper, results)
    if point[axis] <= upper[axis]:
        _range_search(node.right, lower, upper, results)

    return results


class KDTree:
    __slots__ = ("_k", "_root")

    def __init__(self, points: list[_Point[float]]) -> None:
        if not points:
            raise ValueError("must provide at least one point")
        k = len(points[0])
        if any(len(p) != k for p in points):
            raise ValueError("all points must have the same dimensionality")
        self._k = k
        self._root = _build(points)

    @property
    def k(self) -> int:
        return self._k

    def nearest(self, target: _Point[float]) -> _Point[float] | None:
        if len(target) != self._k:
            raise ValueError("target dimensionality mismatch")
        _, pt = _nn(self._root, target, (float("inf"), None))
        return pt

    def knn(self, target: _Point[float], k: int) -> list[_Point[float]]:
        if len(target) != self._k:
            raise ValueError("target dimensionality mismatch")
        if k <= 0:
            return []
        heap = _knn(self._root, target, k, [])
        return [pt for _, pt in sorted(heap, key=lambda x: -x[0])]

    def range_search(self, lower: _Point[float], upper: _Point[float]) -> list[_Point[float]]:
        if len(lower) != self._k or len(upper) != self._k:
            raise ValueError("bound dimensionality mismatch")
        return _range_search(self._root, lower, upper, [])


def build_kdtree(points: list[_Point[float]]) -> KDTree:
    return KDTree(points)
