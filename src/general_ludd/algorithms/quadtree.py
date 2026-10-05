"""Quadtree (2D) and Octree (3D) spatial partitioning.

Point insert, range query, k-nearest-neighbor query.  Subdivision is triggered
when the capacity of a node is exceeded and max_depth has not been reached.
"""

from __future__ import annotations

import math
from heapq import heappop, heappush
from typing import Any, TypeVar

Point2D = tuple[float, float]
Point3D = tuple[float, float, float]

_Q = TypeVar("_Q", bound="Quadtree")
_O = TypeVar("_O", bound="Octree")

_DEFAULT_CAPACITY = 4
_DEFAULT_MAX_DEPTH = 8


def _contains_2d(bbox: tuple[float, float, float, float], p: Point2D) -> bool:
    x_min, y_min, x_max, y_max = bbox
    return x_min <= p[0] <= x_max and y_min <= p[1] <= y_max


def _contains_3d(bbox: tuple[float, float, float, float, float, float], p: Point3D) -> bool:
    x_min, y_min, z_min, x_max, y_max, z_max = bbox
    return x_min <= p[0] <= x_max and y_min <= p[1] <= y_max and z_min <= p[2] <= z_max


def _intersects_2d(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _intersects_3d(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
) -> bool:
    return not (a[3] < b[0] or b[3] < a[0] or a[4] < b[1] or b[4] < a[1] or a[5] < b[2] or b[5] < a[2])


def _dist_2d(a: Point2D, b: Point2D) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dist_3d(a: Point3D, b: Point3D) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _min_dist_bbox_2d(bbox: tuple[float, float, float, float], p: Point2D) -> float:
    x_min, y_min, x_max, y_max = bbox
    dx = max(x_min - p[0], 0.0, p[0] - x_max)
    dy = max(y_min - p[1], 0.0, p[1] - y_max)
    return math.hypot(dx, dy)


def _min_dist_bbox_3d(bbox: tuple[float, float, float, float, float, float], p: Point3D) -> float:
    x_min, y_min, z_min, x_max, y_max, z_max = bbox
    dx = max(x_min - p[0], 0.0, p[0] - x_max)
    dy = max(y_min - p[1], 0.0, p[1] - y_max)
    dz = max(z_min - p[2], 0.0, p[2] - z_max)
    return math.hypot(dx, dy, dz)


class Quadtree:
    """Axis-aligned 2D quadtree with point storage and spatial queries."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        capacity: int = _DEFAULT_CAPACITY,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        depth: int = 0,
    ) -> None:
        """Initialize a ``Quadtree`` instance."""
        self._bbox = bbox
        self._capacity = capacity
        self._max_depth = max_depth
        self._depth = depth
        self._points: list[tuple[Point2D, Any]] = []
        self._divided = False
        self._ne: Quadtree | None = None
        self._nw: Quadtree | None = None
        self._se: Quadtree | None = None
        self._sw: Quadtree | None = None

    def _child_nodes(self) -> tuple[Quadtree, Quadtree, Quadtree, Quadtree]:
        """Return the four initialized children of a divided node."""
        if self._ne is None or self._nw is None or self._se is None or self._sw is None:
            raise RuntimeError("divided quadtree is missing child nodes")
        return self._ne, self._nw, self._se, self._sw

    @property
    def count(self) -> int:
        """Execute ``count``."""
        total = len(self._points)
        if self._divided:
            total += sum(child.count for child in self._child_nodes())
        return total

    def insert(self, point: Point2D, data: Any = None) -> bool:
        """Execute ``insert``."""
        if not _contains_2d(self._bbox, point):
            return False
        if not self._divided:
            if len(self._points) < self._capacity or self._depth >= self._max_depth or self._capacity == 0:
                self._points.append((point, data))
                return True
            self._subdivide()
        self._insert_into_child(point, data)
        return True

    def _subdivide(self) -> None:
        x_min, y_min, x_max, y_max = self._bbox
        x_mid = (x_min + x_max) / 2
        y_mid = (y_min + y_max) / 2
        d = self._depth + 1
        c = self._capacity
        m = self._max_depth
        self._ne = Quadtree((x_mid, y_mid, x_max, y_max), c, m, d)
        self._nw = Quadtree((x_min, y_mid, x_mid, y_max), c, m, d)
        self._se = Quadtree((x_mid, y_min, x_max, y_mid), c, m, d)
        self._sw = Quadtree((x_min, y_min, x_mid, y_mid), c, m, d)
        for pt, dt in self._points:
            self._insert_into_child(pt, dt)
        self._points.clear()
        self._divided = True

    def _insert_into_child(self, point: Point2D, data: Any) -> None:
        ne, nw, se, sw = self._child_nodes()
        x_mid = (self._bbox[0] + self._bbox[2]) / 2
        y_mid = (self._bbox[1] + self._bbox[3]) / 2
        children = (sw, se, nw, ne)
        child_index = int(point[0] >= x_mid) + 2 * int(point[1] >= y_mid)
        children[child_index].insert(point, data)

    def query_range(self, bbox: tuple[float, float, float, float]) -> list[tuple[Point2D, Any]]:
        """Execute ``query_range``."""
        if not _intersects_2d(self._bbox, bbox):
            return []
        results: list[tuple[Point2D, Any]] = []
        for pt, dt in self._points:
            if _contains_2d(bbox, pt):
                results.append((pt, dt))
        if self._divided:
            for child in self._child_nodes():
                results.extend(child.query_range(bbox))
        return results

    def query_nearest(self, point: Point2D, k: int = 1) -> list[tuple[Point2D, Any]]:
        """Execute ``query_nearest``."""
        if k <= 0:
            return []
        heap: list[tuple[float, int, tuple[Point2D, Any]]] = []
        counter = 0
        self._nearest_recursive(point, k, heap, counter)
        result: list[tuple[Point2D, Any]] = []
        while heap:
            _, _, item = heappop(heap)
            result.append(item)
        result.reverse()
        return result[:k]

    def _nearest_recursive(
        self,
        point: Point2D,
        k: int,
        heap: list[tuple[float, int, tuple[Point2D, Any]]],
        counter: int,
    ) -> int:
        min_dist = _min_dist_bbox_2d(self._bbox, point)
        if len(heap) >= k and -heap[0][0] < min_dist:
            return counter
        for pt, dt in self._points:
            d = _dist_2d(point, pt)
            if len(heap) < k or d < -heap[0][0]:
                heappush(heap, (-d, counter, (pt, dt)))
                counter += 1
                if len(heap) > k:
                    heappop(heap)
        if self._divided:
            children = sorted(
                self._child_nodes(),
                key=lambda child: _min_dist_bbox_2d(child._bbox, point),
            )
            for child in children:
                c_min = _min_dist_bbox_2d(child._bbox, point)
                if len(heap) >= k and -heap[0][0] < c_min:
                    continue
                counter = child._nearest_recursive(point, k, heap, counter)
        return counter


class Octree:
    """Axis-aligned 3D octree with point storage and spatial queries."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float, float, float],
        capacity: int = _DEFAULT_CAPACITY,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        depth: int = 0,
    ) -> None:
        """Initialize a ``Octree`` instance."""
        self._bbox = bbox
        self._capacity = capacity
        self._max_depth = max_depth
        self._depth = depth
        self._points: list[tuple[Point3D, Any]] = []
        self._divided = False
        self._children: list[Octree] = []

    @property
    def count(self) -> int:
        """Execute ``count``."""
        total = len(self._points)
        if self._divided:
            for c in self._children:
                total += c.count
        return total

    def insert(self, point: Point3D, data: Any = None) -> bool:
        """Execute ``insert``."""
        if not _contains_3d(self._bbox, point):
            return False
        if not self._divided:
            if len(self._points) < self._capacity or self._depth >= self._max_depth or self._capacity == 0:
                self._points.append((point, data))
                return True
            self._subdivide()
        self._insert_into_child(point, data)
        return True

    def _subdivide(self) -> None:
        x_min, y_min, z_min, x_max, y_max, z_max = self._bbox
        x_mid = (x_min + x_max) / 2
        y_mid = (y_min + y_max) / 2
        z_mid = (z_min + z_max) / 2
        d = self._depth + 1
        c = self._capacity
        m = self._max_depth
        self._children = [
            Octree((x_min, y_min, z_min, x_mid, y_mid, z_mid), c, m, d),
            Octree((x_mid, y_min, z_min, x_max, y_mid, z_mid), c, m, d),
            Octree((x_min, y_mid, z_min, x_mid, y_max, z_mid), c, m, d),
            Octree((x_mid, y_mid, z_min, x_max, y_max, z_mid), c, m, d),
            Octree((x_min, y_min, z_mid, x_mid, y_mid, z_max), c, m, d),
            Octree((x_mid, y_min, z_mid, x_max, y_mid, z_max), c, m, d),
            Octree((x_min, y_mid, z_mid, x_mid, y_max, z_max), c, m, d),
            Octree((x_mid, y_mid, z_mid, x_max, y_max, z_max), c, m, d),
        ]
        for pt, dt in self._points:
            self._insert_into_child(pt, dt)
        self._points.clear()
        self._divided = True

    def _insert_into_child(self, point: Point3D, data: Any) -> None:
        x_mid = (self._bbox[0] + self._bbox[3]) / 2
        y_mid = (self._bbox[1] + self._bbox[4]) / 2
        z_mid = (self._bbox[2] + self._bbox[5]) / 2
        idx = int(point[0] >= x_mid) + 2 * int(point[1] >= y_mid) + 4 * int(point[2] >= z_mid)
        self._children[idx].insert(point, data)

    def query_range(self, bbox: tuple[float, float, float, float, float, float]) -> list[tuple[Point3D, Any]]:
        """Execute ``query_range``."""
        if not _intersects_3d(self._bbox, bbox):
            return []
        results: list[tuple[Point3D, Any]] = []
        for pt, dt in self._points:
            if _contains_3d(bbox, pt):
                results.append((pt, dt))
        if self._divided:
            for child in self._children:
                results.extend(child.query_range(bbox))
        return results

    def query_nearest(self, point: Point3D, k: int = 1) -> list[tuple[Point3D, Any]]:
        """Execute ``query_nearest``."""
        if k <= 0:
            return []
        heap: list[tuple[float, int, tuple[Point3D, Any]]] = []
        counter = 0
        self._nearest_recursive(point, k, heap, counter)
        result: list[tuple[Point3D, Any]] = []
        while heap:
            _, _, item = heappop(heap)
            result.append(item)
        result.reverse()
        return result[:k]

    def _nearest_recursive(
        self,
        point: Point3D,
        k: int,
        heap: list[tuple[float, int, tuple[Point3D, Any]]],
        counter: int,
    ) -> int:
        min_dist = _min_dist_bbox_3d(self._bbox, point)
        if len(heap) >= k and -heap[0][0] < min_dist:
            return counter
        for pt, dt in self._points:
            d = _dist_3d(point, pt)
            if len(heap) < k or d < -heap[0][0]:
                heappush(heap, (-d, counter, (pt, dt)))
                counter += 1
                if len(heap) > k:
                    heappop(heap)
        if self._divided:
            sorted_children = sorted(
                self._children,
                key=lambda c: _min_dist_bbox_3d(c._bbox, point),
            )
            for child in sorted_children:
                c_min = _min_dist_bbox_3d(child._bbox, point)
                if len(heap) >= k and -heap[0][0] < c_min:
                    continue
                counter = child._nearest_recursive(point, k, heap, counter)
        return counter
