"""Augmented interval tree: O(log n) insert/delete, O(log n + k) overlap + stabbing queries.

Each node stores max_hi = max endpoint of any interval in its subtree, enabling
early pruning during search.  Balanced via Treap priority (randomized BST).

Pure-Python, stdlib only.
"""

from __future__ import annotations

import random
from typing import Generic, TypeVar

V = TypeVar("V")


class IntervalNode(Generic[V]):
    __slots__ = ("hi", "left", "lo", "max_hi", "priority", "right", "val")

    def __init__(self, lo: int, hi: int, val: V) -> None:
        self.lo = lo
        self.hi = hi
        self.val = val
        self.priority = random.random()
        self.max_hi = hi
        self.left: IntervalNode[V] | None = None
        self.right: IntervalNode[V] | None = None


def _node_max_hi(node: IntervalNode[V] | None) -> int:
    return node.max_hi if node is not None else -1


def _recalc(node: IntervalNode[V]) -> None:
    node.max_hi = max(node.hi, _node_max_hi(node.left), _node_max_hi(node.right))


def _rotate_right(p: IntervalNode[V]) -> IntervalNode[V]:
    q = p.left
    assert q is not None
    p.left = q.right
    q.right = p
    _recalc(p)
    _recalc(q)
    return q


def _rotate_left(p: IntervalNode[V]) -> IntervalNode[V]:
    q = p.right
    assert q is not None
    p.right = q.left
    q.left = p
    _recalc(p)
    _recalc(q)
    return q


def interval_insert(root: IntervalNode[V] | None, lo: int, hi: int, val: V) -> IntervalNode[V]:
    if root is None:
        return IntervalNode(lo, hi, val)
    if (lo, hi) == (root.lo, root.hi):
        root.val = val
        return root
    if (lo, hi) < (root.lo, root.hi):
        root.left = interval_insert(root.left, lo, hi, val)
        if root.left is not None and root.left.priority > root.priority:
            root = _rotate_right(root)
    else:
        root.right = interval_insert(root.right, lo, hi, val)
        if root.right is not None and root.right.priority > root.priority:
            root = _rotate_left(root)
    _recalc(root)
    return root


def interval_delete(root: IntervalNode[V] | None, lo: int, hi: int) -> IntervalNode[V] | None:
    if root is None:
        return None
    if (lo, hi) < (root.lo, root.hi):
        root.left = interval_delete(root.left, lo, hi)
    elif (lo, hi) > (root.lo, root.hi):
        root.right = interval_delete(root.right, lo, hi)
    else:
        if root.left is None:
            return root.right
        if root.right is None:
            return root.left
        if root.left.priority > root.right.priority:
            root = _rotate_right(root)
            root.right = interval_delete(root.right, lo, hi)
        else:
            root = _rotate_left(root)
            root.left = interval_delete(root.left, lo, hi)
    if root is not None:
        _recalc(root)
    return root


def _overlaps(lo: int, hi: int, node_lo: int, node_hi: int) -> bool:
    return lo < node_hi and node_lo < hi


def interval_overlap_query(root: IntervalNode[V] | None, lo: int, hi: int) -> list[tuple[int, int, V]]:
    result: list[tuple[int, int, V]] = []
    _overlap_collect(root, lo, hi, result)
    return result


def _overlap_collect(node: IntervalNode[V] | None, lo: int, hi: int, out: list[tuple[int, int, V]]) -> None:
    if node is None:
        return
    if _node_max_hi(node.left) > lo:
        _overlap_collect(node.left, lo, hi, out)
    if _overlaps(lo, hi, node.lo, node.hi):
        out.append((node.lo, node.hi, node.val))
    if node.lo < hi and _node_max_hi(node.right) > lo:
        _overlap_collect(node.right, lo, hi, out)


def interval_stabbing_query(root: IntervalNode[V] | None, point: int) -> list[tuple[int, int, V]]:
    result: list[tuple[int, int, V]] = []
    _stabbing_collect(root, point, result)
    return result


def _stabbing_collect(node: IntervalNode[V] | None, point: int, out: list[tuple[int, int, V]]) -> None:
    if node is None:
        return
    if _node_max_hi(node.left) > point:
        _stabbing_collect(node.left, point, out)
    if node.lo <= point < node.hi:
        out.append((node.lo, node.hi, node.val))
    if node.lo <= point and _node_max_hi(node.right) > point:
        _stabbing_collect(node.right, point, out)


def interval_to_list(root: IntervalNode[V] | None) -> list[tuple[int, int, V]]:
    result: list[tuple[int, int, V]] = []
    _inorder(root, result)
    return result


def _inorder(node: IntervalNode[V] | None, out: list[tuple[int, int, V]]) -> None:
    if node is None:
        return
    _inorder(node.left, out)
    out.append((node.lo, node.hi, node.val))
    _inorder(node.right, out)


def interval_size(root: IntervalNode[V] | None) -> int:
    if root is None:
        return 0
    return 1 + interval_size(root.left) + interval_size(root.right)
