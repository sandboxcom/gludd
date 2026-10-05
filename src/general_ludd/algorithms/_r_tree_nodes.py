"""Bounding boxes and tree nodes shared by the R-tree implementation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class BBox:
    """Represent an axis-aligned two-dimensional bounding box."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        """Return the non-negative area of the box."""
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def margin(self) -> float:
        """Return the perimeter-like margin used by R-tree heuristics."""
        return 2.0 * ((self.x2 - self.x1) + (self.y2 - self.y1))

    def contains(self, other: BBox) -> bool:
        """Return whether this box fully contains ``other``."""
        return self.x1 <= other.x1 and self.y1 <= other.y1 and self.x2 >= other.x2 and self.y2 >= other.y2

    def intersects(self, other: BBox) -> bool:
        """Return whether this box intersects ``other``."""
        return not (self.x2 < other.x1 or self.x1 > other.x2 or self.y2 < other.y1 or self.y1 > other.y2)

    def distance_sq(self, other: BBox) -> float:
        """Return the squared minimum distance to ``other``."""
        dx = max(0.0, max(self.x1 - other.x2, other.x1 - self.x2))
        dy = max(0.0, max(self.y1 - other.y2, other.y1 - self.y2))
        return dx * dx + dy * dy

    def expanded(self, other: BBox) -> BBox:
        """Return the smallest box containing this box and ``other``."""
        return BBox(
            x1=min(self.x1, other.x1),
            y1=min(self.y1, other.y1),
            x2=max(self.x2, other.x2),
            y2=max(self.y2, other.y2),
        )

    @staticmethod
    def union_all(bboxes: Sequence[BBox]) -> BBox:
        """Return the union of all boxes, or the canonical empty box."""
        if not bboxes:
            return BBox(math.inf, math.inf, -math.inf, -math.inf)
        return BBox(
            min(box.x1 for box in bboxes),
            min(box.y1 for box in bboxes),
            max(box.x2 for box in bboxes),
            max(box.y2 for box in bboxes),
        )

    @property
    def center(self) -> tuple[float, float]:
        """Return the center point of the box."""
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0


class _Node(Generic[T]):
    """Store one internal or leaf node in an :class:`RTree`."""

    __slots__ = ("bbox", "children", "data", "is_leaf", "parent")

    def __init__(self, is_leaf: bool = True) -> None:
        self.bbox = BBox(math.inf, math.inf, -math.inf, -math.inf)
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
        boxes = [
            child if isinstance(child, BBox) else child.bbox
            for child in self.children
            if isinstance(child, (BBox, _Node))
        ]
        self.bbox = BBox.union_all(boxes)


__all__ = ("BBox", "_Node")
