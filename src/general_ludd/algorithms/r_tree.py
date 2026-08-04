"""R-tree spatial index: insert, search, quadratic split, bounding box.

Pure-Python, stdlib only. A 2D rectangle-based R-tree for spatial queries.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def margin(self) -> float:
        return 2.0 * ((self.x2 - self.x1) + (self.y2 - self.y1))

    def contains(self, other: BBox) -> bool:
        return self.x1 <= other.x1 and self.y1 <= other.y1 and self.x2 >= other.x2 and self.y2 >= other.y2

    def intersects(self, other: BBox) -> bool:
        return not (self.x2 < other.x1 or self.x1 > other.x2 or self.y2 < other.y1 or self.y1 > other.y2)

    def distance_sq(self, other: BBox) -> float:
        dx: float = max(0.0, max(self.x1 - other.x2, other.x1 - self.x2))
        dy: float = max(0.0, max(self.y1 - other.y2, other.y1 - self.y2))
        return dx * dx + dy * dy

    def expanded(self, other: BBox) -> BBox:
        return BBox(
            x1=min(self.x1, other.x1),
            y1=min(self.y1, other.y1),
            x2=max(self.x2, other.x2),
            y2=max(self.y2, other.y2),
        )

    @staticmethod
    def union_all(bboxes: Sequence[BBox]) -> BBox:
        if not bboxes:
            return BBox(math.inf, math.inf, -math.inf, -math.inf)
        x1 = min(b.x1 for b in bboxes)
        y1 = min(b.y1 for b in bboxes)
        x2 = max(b.x2 for b in bboxes)
        y2 = max(b.y2 for b in bboxes)
        return BBox(x1, y1, x2, y2)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)


class _Node(Generic[T]):
    __slots__ = ("bbox", "children", "data", "is_leaf", "parent")

    def __init__(self, is_leaf: bool = True) -> None:
        self.bbox: BBox = BBox(math.inf, math.inf, -math.inf, -math.inf)
        self.children: list[Any] = []
        self.data: list[Any] = []
        self.parent: _Node[T] | None = None
        self.is_leaf = is_leaf

    @property
    def size(self) -> int:
        return len(self.children)

    def child_bbox(self, idx: int) -> BBox:
        child = self.children[idx]
        if isinstance(child, BBox):
            return child
        if isinstance(child, _Node):
            return child.bbox
        return BBox(math.inf, math.inf, -math.inf, -math.inf)

    def recalc_bbox(self) -> None:
        bboxes: list[BBox] = []
        for child in self.children:
            if isinstance(child, BBox):
                bboxes.append(child)
            elif isinstance(child, _Node):
                bboxes.append(child.bbox)
        self.bbox = BBox.union_all(bboxes)


class RTree(Generic[T]):
    def __init__(self, max_entries: int = 5, min_entries: int = 2) -> None:
        if min_entries < 1:
            raise ValueError("min_entries must be >= 1")
        if max_entries < 2 * min_entries:
            raise ValueError(f"max_entries ({max_entries}) must be >= 2 * min_entries ({2 * min_entries})")
        self._max = max_entries
        self._min = min_entries
        self._root: _Node[T] = _Node(is_leaf=True)
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def insert(self, bbox: BBox, data: T) -> None:
        leaf = self._choose_leaf(bbox)
        leaf.children.append(bbox)
        leaf.data.append(data)
        leaf.recalc_bbox()
        self._size += 1
        if leaf.size > self._max:
            self._split(leaf)

    def search(self, query: BBox) -> list[T]:
        result: list[T] = []
        self._search_rec(self._root, query, result)
        return result

    def search_bbox(self, query: BBox) -> list[tuple[BBox, T]]:
        result: list[tuple[BBox, T]] = []
        self._search_bbox_rec(self._root, query, result)
        return result

    def range_search(self, min_x: float, min_y: float, max_x: float, max_y: float) -> list[T]:
        return self.search(BBox(min_x, min_y, max_x, max_y))

    def nearest(self, point: tuple[float, float], k: int = 1) -> list[tuple[float, T]]:
        query_box = BBox(point[0], point[1], point[0], point[1])
        candidates: list[tuple[float, Any]] = [(self._root.bbox.distance_sq(query_box), self._root)]
        result: list[tuple[float, T]] = []

        while candidates and len(result) < k:
            candidates.sort(key=lambda x: x[0])
            _dist, item = candidates.pop(0)

            if isinstance(item, _Node):
                node: _Node[T] = item
                if node.is_leaf:
                    for i in range(node.size):
                        child = node.children[i]
                        if isinstance(child, BBox):
                            d = child.distance_sq(query_box)
                            candidates.append((d, node.data[i]))
                else:
                    for child_node in node.children:
                        if isinstance(child_node, _Node):
                            d = child_node.bbox.distance_sq(query_box)
                            candidates.append((d, child_node))
            else:
                result.append((math.sqrt(_dist), item))

        return result

    def contains_point(self, x: float, y: float) -> list[T]:
        return self.search(BBox(x, y, x, y))

    @property
    def depth(self) -> int:
        return self._depth_rec(self._root)

    @property
    def total_nodes(self) -> int:
        return self._count_nodes(self._root)

    def _depth_rec(self, node: _Node[T] | None) -> int:
        if node is None:
            return 0
        if node.is_leaf:
            return 1
        if not node.children:
            return 1
        max_child_depth = 0
        for child in node.children:
            if isinstance(child, _Node):
                max_child_depth = max(max_child_depth, self._depth_rec(child))
        return 1 + max_child_depth

    def _count_nodes(self, node: _Node[T] | None) -> int:
        if node is None:
            return 0
        count = 1
        if not node.is_leaf:
            for child in node.children:
                if isinstance(child, _Node):
                    count += self._count_nodes(child)
        return count

    def _choose_leaf(self, bbox: BBox) -> _Node[T]:
        node = self._root
        while not node.is_leaf:
            best_idx = 0
            best_enlargement = math.inf
            best_area = math.inf
            for i in range(node.size):
                child = node.children[i]
                if not isinstance(child, _Node):
                    continue
                enlargement = child.bbox.expanded(bbox).area - child.bbox.area
                if enlargement < best_enlargement - 1e-9:
                    best_enlargement = enlargement
                    best_area = child.bbox.area
                    best_idx = i
                elif abs(enlargement - best_enlargement) < 1e-9:
                    if child.bbox.area < best_area:
                        best_area = child.bbox.area
                        best_idx = i
            node = node.children[best_idx]
        return node

    def _split(self, node: _Node[T]) -> None:
        seeds = self._pick_seeds(node)
        g1_bbox = node.child_bbox(seeds[0])
        g2_bbox = node.child_bbox(seeds[1])
        g1: list[int] = [seeds[0]]
        g2: list[int] = [seeds[1]]
        assigned: set[int] = {seeds[0], seeds[1]}

        remaining = [i for i in range(node.size) if i not in assigned]

        while remaining:
            n_remaining = len(remaining)
            g1_short = len(g1) < self._min and len(g1) + n_remaining == self._min
            g2_short = len(g2) < self._min and len(g2) + n_remaining == self._min

            if g1_short:
                for idx in remaining:
                    g1.append(idx)
                    g1_bbox = g1_bbox.expanded(node.child_bbox(idx))
                break
            if g2_short:
                for idx in remaining:
                    g2.append(idx)
                    g2_bbox = g2_bbox.expanded(node.child_bbox(idx))
                break

            best_idx = -1
            best_diff = -math.inf
            best_group = 0
            for idx in remaining:
                c = node.child_bbox(idx)
                e1 = g1_bbox.expanded(c).area - g1_bbox.area
                e2 = g2_bbox.expanded(c).area - g2_bbox.area
                diff = abs(e1 - e2)
                if diff > best_diff + 1e-9:
                    best_diff = diff
                    best_idx = idx
                    best_group = 1 if e1 < e2 else 2
                elif abs(diff - best_diff) < 1e-9:
                    area1 = g1_bbox.expanded(c).area
                    area2 = g2_bbox.expanded(c).area
                    if area1 < area2:
                        best_idx = idx
                        best_group = 1
                    elif area2 < area1:
                        best_idx = idx
                        best_group = 2

            if best_group == 1:
                g1.append(best_idx)
                g1_bbox = g1_bbox.expanded(node.child_bbox(best_idx))
            else:
                g2.append(best_idx)
                g2_bbox = g2_bbox.expanded(node.child_bbox(best_idx))
            assigned.add(best_idx)
            remaining = [i for i in range(node.size) if i not in assigned]

        new_node: _Node[T] = _Node(is_leaf=node.is_leaf)
        old_children = list(node.children)
        old_data = list(node.data)

        node.children = [old_children[i] for i in g1]
        node.data = [old_data[i] for i in g1]
        new_node.children = [old_children[i] for i in g2]
        new_node.data = [old_data[i] for i in g2]

        node.recalc_bbox()
        new_node.recalc_bbox()

        if node.parent is None:
            new_root: _Node[T] = _Node(is_leaf=False)
            new_root.children = [node, new_node]
            new_root.data = [None, None]
            new_root.recalc_bbox()
            node.parent = new_root
            new_node.parent = new_root
            self._root = new_root
        else:
            parent = node.parent
            parent.children.append(new_node)
            parent.data.append(None)
            parent.recalc_bbox()
            new_node.parent = parent
            if parent.size > self._max:
                self._split(parent)

    def _pick_seeds(self, node: _Node[T]) -> tuple[int, int]:
        worst = -math.inf
        idx1 = 0
        idx2 = 1
        for i in range(node.size):
            for j in range(i + 1, node.size):
                a = node.child_bbox(i)
                b = node.child_bbox(j)
                d = a.expanded(b).area - a.area - b.area
                if d > worst + 1e-9:
                    worst = d
                    idx1, idx2 = i, j
        return idx1, idx2

    def _search_rec(self, node: _Node[T], query: BBox, result: list[T]) -> None:
        if node.is_leaf:
            for i in range(node.size):
                child = node.children[i]
                if isinstance(child, BBox) and child.intersects(query):
                    result.append(node.data[i])
        else:
            for child in node.children:
                if isinstance(child, _Node) and child.bbox.intersects(query):
                    self._search_rec(child, query, result)

    def _search_bbox_rec(self, node: _Node[T], query: BBox, result: list[tuple[BBox, T]]) -> None:
        if node.is_leaf:
            for i in range(node.size):
                child = node.children[i]
                if isinstance(child, BBox) and child.intersects(query):
                    result.append((child, node.data[i]))
        else:
            for child in node.children:
                if isinstance(child, _Node) and child.bbox.intersects(query):
                    self._search_bbox_rec(child, query, result)
