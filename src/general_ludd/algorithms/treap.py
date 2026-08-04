"""Treap (Tree + Heap): randomized BST with split/merge + implicit treap.

Pure-Python, stdlib only.  Each node carries a random priority; the tree
is a BST by key and a max-heap by priority.  Insert/delete/contains are
expected O(log n); worst-case O(n) is astronomically unlikely.

Implicit treap stores sizes instead of keys — elements are indexed by
position, supporting array-like operations (split at index, insert at
index, range query) in expected O(log n).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TreapNode(Generic[K, V]):
    __slots__ = ("key", "left", "priority", "right", "val")

    def __init__(self, key: K, val: V, priority: float | None = None) -> None:
        self.key = key
        self.val = val
        self.priority = priority if priority is not None else random.random()
        self.left: TreapNode[K, V] | None = None
        self.right: TreapNode[K, V] | None = None


class ImplicitNode(Generic[V]):
    __slots__ = ("left", "priority", "right", "size", "val")

    def __init__(self, val: V, priority: float | None = None) -> None:
        self.val = val
        self.priority = priority if priority is not None else random.random()
        self.size = 1
        self.left: ImplicitNode[V] | None = None
        self.right: ImplicitNode[V] | None = None


# --- Rotation helpers ---


def _rotate_right(p: TreapNode[K, V]) -> TreapNode[K, V]:
    q = p.left
    assert q is not None
    p.left = q.right
    q.right = p
    return q


def _rotate_left(p: TreapNode[K, V]) -> TreapNode[K, V]:
    q = p.right
    assert q is not None
    p.right = q.left
    q.left = p
    return q


# --- Core BST ops ---


def treap_insert(root: TreapNode[K, V] | None, key: K, val: V) -> TreapNode[K, V] | None:
    """Insert (key, val); overwrites value if key exists. Returns new root."""
    if root is None:
        return TreapNode(key, val)
    if key == root.key:
        root.val = val
        return root
    if key < root.key:
        root.left = treap_insert(root.left, key, val)
        if root.left is not None and root.left.priority > root.priority:
            root = _rotate_right(root)
    else:
        root.right = treap_insert(root.right, key, val)
        if root.right is not None and root.right.priority > root.priority:
            root = _rotate_left(root)
    return root


def treap_delete(root: TreapNode[K, V] | None, key: K) -> TreapNode[K, V] | None:
    """Delete *key* if present. Returns new root."""
    if root is None:
        return None
    if key < root.key:
        root.left = treap_delete(root.left, key)
        return root
    if key > root.key:
        root.right = treap_delete(root.right, key)
        return root
    # key == root.key — delete this node
    if root.left is None:
        return root.right
    if root.right is None:
        return root.left
    # Both children exist: rotate the higher-priority child up, recurse
    if root.left.priority > root.right.priority:
        root = _rotate_right(root)
        root.right = treap_delete(root.right, key)
    else:
        root = _rotate_left(root)
        root.left = treap_delete(root.left, key)
    return root


def treap_contains(root: TreapNode[K, V] | None, key: K) -> bool:
    """Return True if *key* is present."""
    cur = root
    while cur is not None:
        if key == cur.key:
            return True
        cur = cur.left if key < cur.key else cur.right
    return False


def treap_get(root: TreapNode[K, V] | None, key: K, default: V | None = None) -> V | None:
    """Return value for *key*, or *default* if absent."""
    cur = root
    while cur is not None:
        if key == cur.key:
            return cur.val
        cur = cur.left if key < cur.key else cur.right
    return default


def treap_items(root: TreapNode[K, V] | None) -> list[tuple[K, V]]:
    """In-order traversal returning sorted (key, value) pairs."""
    result: list[tuple[K, V]] = []
    _inorder(root, result)
    return result


def _inorder(node: TreapNode[K, V] | None, out: list[tuple[K, V]]) -> None:
    if node is None:
        return
    _inorder(node.left, out)
    out.append((node.key, node.val))
    _inorder(node.right, out)


# --- Split / Merge (iterative to avoid recursion-depth issues) ---


def treap_split(root: TreapNode[K, V] | None, key: K) -> tuple[TreapNode[K, V] | None, TreapNode[K, V] | None]:
    """Split tree into (≤key, >key). Iterative — no recursion depth issues."""
    left_parts: list[tuple[TreapNode[K, V] | None, int]] = []
    right_parts: list[tuple[TreapNode[K, V] | None, int]] = []
    cur: TreapNode[K, V] | None = root
    while cur is not None:
        if cur.key <= key:
            # cur and cur.left belong to left tree; continue down cur.right
            left_parts.append((cur, 0))  # 0 = left child of split point
            cur = cur.right
        else:
            # cur and cur.right belong to right tree; continue down cur.left
            right_parts.append((cur, 1))  # 1 = right child of split point
            cur = cur.left

    left_root: TreapNode[K, V] | None = None
    for node, _dir in reversed(left_parts):
        node.right = left_root
        left_root = node

    right_root: TreapNode[K, V] | None = None
    for node, _dir in reversed(right_parts):
        node.left = right_root
        right_root = node

    return left_root, right_root


def treap_merge(left: TreapNode[K, V] | None, right: TreapNode[K, V] | None) -> TreapNode[K, V] | None:
    """Merge two trees where all keys in *left* ≤ all keys in *right*. Iterative."""
    if left is None:
        return right
    if right is None:
        return left
    result: TreapNode[K, V] | None = None
    prev: TreapNode[K, V] | None = None
    prev_dir: str = ""
    while left is not None and right is not None:
        if left.priority > right.priority:
            # left becomes root, left.right and right need merging
            if result is None:
                result = left
            if prev is not None:
                if prev_dir == "L":
                    prev.left = left
                else:
                    prev.right = left
            prev = left
            prev_dir = "R"
            left = left.right
        else:
            if result is None:
                result = right
            if prev is not None:
                if prev_dir == "L":
                    prev.left = right
                else:
                    prev.right = right
            prev = right
            prev_dir = "L"
            right = right.left
    remaining = left if left is not None else right
    if prev is not None:
        if prev_dir == "L":
            prev.left = remaining
        else:
            prev.right = remaining
    else:
        result = remaining
    return result


# --- Implicit treap (by-position) ---


def _implicit_size(node: ImplicitNode[V] | None) -> int:
    return node.size if node is not None else 0


def _update_size(node: ImplicitNode[V]) -> None:
    node.size = 1 + _implicit_size(node.left) + _implicit_size(node.right)


def _implicit_rotate_right(p: ImplicitNode[V]) -> ImplicitNode[V]:
    q = p.left
    assert q is not None
    p.left = q.right
    q.right = p
    _update_size(p)
    _update_size(q)
    return q


def _implicit_rotate_left(p: ImplicitNode[V]) -> ImplicitNode[V]:
    q = p.right
    assert q is not None
    p.right = q.left
    q.left = p
    _update_size(p)
    _update_size(q)
    return q


def implicit_split(root: ImplicitNode[V] | None, pos: int) -> tuple[ImplicitNode[V] | None, ImplicitNode[V] | None]:
    """Split so that left subtree contains exactly *pos* elements. Iterative."""
    left_parts: list[tuple[ImplicitNode[V] | None, int]] = []
    right_parts: list[tuple[ImplicitNode[V] | None, int]] = []
    cur: ImplicitNode[V] | None = root
    while cur is not None:
        left_sz = _implicit_size(cur.left)
        if pos <= left_sz:
            right_parts.append((cur, 1))
            cur = cur.left
        else:
            pos -= left_sz + 1
            left_parts.append((cur, 0))
            cur = cur.right

    left_root: ImplicitNode[V] | None = None
    for node, _dir in reversed(left_parts):
        node.right = left_root
        _update_size(node)
        left_root = node

    right_root: ImplicitNode[V] | None = None
    for node, _dir in reversed(right_parts):
        node.left = right_root
        _update_size(node)
        right_root = node

    return left_root, right_root


def implicit_merge(
    left: ImplicitNode[V] | None,
    right: ImplicitNode[V] | None,
) -> ImplicitNode[V] | None:
    """Merge two implicit treaps (all of *left* precedes all of *right*). Iterative."""
    if left is None:
        return right
    if right is None:
        return left
    result: ImplicitNode[V] | None = None
    prev: ImplicitNode[V] | None = None
    prev_dir: str = ""
    while left is not None and right is not None:
        if left.priority > right.priority:
            if result is None:
                result = left
            if prev is not None:
                if prev_dir == "L":
                    prev.left = left
                else:
                    prev.right = left
            prev = left
            prev_dir = "R"
            left = left.right
        else:
            if result is None:
                result = right
            if prev is not None:
                if prev_dir == "L":
                    prev.left = right
                else:
                    prev.right = right
            prev = right
            prev_dir = "L"
            right = right.left
    remaining = left if left is not None else right
    if prev is not None:
        if prev_dir == "L":
            prev.left = remaining
        else:
            prev.right = remaining
    else:
        result = remaining

    _recalc_sizes(result)
    return result


def _recalc_sizes(node: ImplicitNode[V] | None) -> None:
    """Post-order recalc of sizes (recursive; depth is expected O(log n))."""
    if node is None:
        return
    _recalc_sizes(node.left)
    _recalc_sizes(node.right)
    _update_size(node)


def implicit_push_back(root: ImplicitNode[V] | None, val: V) -> ImplicitNode[V] | None:
    """Append *val* at the end. Returns new root."""
    return implicit_merge(root, ImplicitNode(val))


def implicit_insert_at(root: ImplicitNode[V] | None, pos: int, val: V) -> ImplicitNode[V] | None:
    """Insert *val* before position *pos* (0-indexed). Returns new root."""
    a, b = implicit_split(root, pos)
    return implicit_merge(implicit_merge(a, ImplicitNode(val)), b)


def implicit_delete_at(root: ImplicitNode[V] | None, pos: int) -> ImplicitNode[V] | None:
    """Delete element at position *pos* (0-indexed). Returns new root."""
    a, b = implicit_split(root, pos)
    _, c = implicit_split(b, 1)
    return implicit_merge(a, c)


def implicit_get(root: ImplicitNode[V] | None, pos: int) -> V | None:
    """Return element at position *pos* (0-indexed), or None if out of bounds."""
    cur = root
    while cur is not None:
        left_sz = _implicit_size(cur.left)
        if pos < left_sz:
            cur = cur.left
        elif pos == left_sz:
            return cur.val
        else:
            pos -= left_sz + 1
            cur = cur.right
    return None


def implicit_size(root: ImplicitNode[V] | None) -> int:
    """Total number of elements."""
    return _implicit_size(root)


def implicit_to_list(root: ImplicitNode[V] | None) -> list[V]:
    """In-order traversal returning elements in position order."""
    result: list[V] = []
    _implicit_inorder(root, result)
    return result


def _implicit_inorder(node: ImplicitNode[V] | None, out: list[V]) -> None:
    if node is None:
        return
    _implicit_inorder(node.left, out)
    out.append(node.val)
    _implicit_inorder(node.right, out)


def implicit_range_query(root: ImplicitNode[V] | None, lo: int, r: int, agg: Callable[[V, V], V], default: V) -> V:
    """Apply commutative *agg* over elements in [lo, r). Returns *default* if empty range."""
    if root is None or lo >= r:
        return default
    a, b = implicit_split(root, lo)
    b, c = implicit_split(b, r - lo)
    result = default
    if b is not None:
        result = _fold(b, agg, default)
    root = implicit_merge(implicit_merge(a, b), c)
    return result


def _fold(node: ImplicitNode[V] | None, agg: Callable[[V, V], V], default: V) -> V:
    if node is None:
        return default
    left = _fold(node.left, agg, default)
    mid = node.val
    right = _fold(node.right, agg, default)
    cur = default
    for v in (left, mid, right):
        if v is not default or (v is default and v is not None):
            cur = agg(cur, v) if cur is not default else v
    return cur
