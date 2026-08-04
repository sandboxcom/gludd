"""Splay tree: self-adjusting BST with splay, insert, search, delete, split, merge.

Every operation splays the accessed node to the root.  Amortized O(log n)
per operation; O(n) worst-case single operation, O(1) amortized per access
in a sequence.

Pure-Python, stdlib only.  Follows project conventions (__slots__, Generic,
from __future__ import annotations).
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar


class Comparable(Protocol):
    def __lt__(self, other: object, /) -> bool: ...
    def __gt__(self, other: object, /) -> bool: ...
    def __le__(self, other: object, /) -> bool: ...


K = TypeVar("K", bound=Comparable)
V = TypeVar("V")


class SplayNode(Generic[K, V]):
    __slots__ = ("key", "left", "right", "val")

    def __init__(
        self,
        key: K,
        val: V,
        left: SplayNode[K, V] | None = None,
        right: SplayNode[K, V] | None = None,
    ) -> None:
        self.key = key
        self.val = val
        self.left = left
        self.right = right


# ---------------------------------------------------------------------------
# Rotation helpers
# ---------------------------------------------------------------------------


def _rotate_right(x: SplayNode[K, V]) -> SplayNode[K, V]:
    y = x.left
    assert y is not None
    x.left = y.right
    y.right = x
    return y


def _rotate_left(x: SplayNode[K, V]) -> SplayNode[K, V]:
    y = x.right
    assert y is not None
    x.right = y.left
    y.left = x
    return y


# ---------------------------------------------------------------------------
# Splay — bring key to root (or the closest match if key is absent)
# ---------------------------------------------------------------------------


def splay(root: SplayNode[K, V] | None, key: K) -> SplayNode[K, V] | None:
    """Splay *key* to the root.  If absent, the last accessed node becomes root."""
    if root is None:
        return None

    # Header node as a dummy root — simplifies the iterative algorithm
    header = SplayNode[K, V](key, root.val)
    header.left = None
    header.right = None
    left_max = header
    right_min = header
    cur = root

    while True:
        if key < cur.key:
            if cur.left is None:
                break
            if key < cur.left.key:
                cur = _rotate_right(cur)
                if cur.left is None:
                    break
            right_min.left = cur
            right_min = cur
            assert cur.left is not None
            cur = cur.left
            right_min.left = None
        elif key > cur.key:
            if cur.right is None:
                break
            if key > cur.right.key:
                cur = _rotate_left(cur)
                if cur.right is None:
                    break
            left_max.right = cur
            left_max = cur
            assert cur.right is not None
            cur = cur.right
            left_max.right = None
        else:
            break

    left_max.right = cur.left
    right_min.left = cur.right
    cur.left = header.right
    cur.right = header.left
    return cur


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def splay_insert(
    root: SplayNode[K, V] | None,
    key: K,
    val: V,
) -> SplayNode[K, V] | None:
    """Insert (key, val); overwrites value if key exists.  Splays new/existing node to root."""
    if root is None:
        return SplayNode(key, val)

    root = splay(root, key)

    if root is None:
        return SplayNode(key, val)

    if key == root.key:
        root.val = val
        return root

    node = SplayNode(key, val)
    if key < root.key:
        node.right = root
        node.left = root.left
        root.left = None
    else:
        node.left = root
        node.right = root.right
        root.right = None
    return node


# ---------------------------------------------------------------------------
# Search / contains
# ---------------------------------------------------------------------------


def splay_get(
    root: SplayNode[K, V] | None,
    key: K,
) -> tuple[V | None, SplayNode[K, V] | None]:
    """Return (value, new_root).  Splays the found node (or closest) to root."""
    if root is None:
        return None, None
    root = splay(root, key)
    if root is not None and root.key == key:
        return root.val, root
    return None, root


def splay_contains(
    root: SplayNode[K, V] | None,
    key: K,
) -> tuple[bool, SplayNode[K, V] | None]:
    """Return (found, new_root).  Splays the found node (or closest) to root."""
    if root is None:
        return False, None
    root = splay(root, key)
    return (root is not None and root.key == key), root


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def splay_delete(
    root: SplayNode[K, V] | None,
    key: K,
) -> SplayNode[K, V] | None:
    """Delete *key* if present.  Returns new root."""
    if root is None:
        return None

    root = splay(root, key)
    if root is None or root.key != key:
        return root

    if root.left is None:
        return root.right
    if root.right is None:
        return root.left

    left: SplayNode[K, V] | None = root.left
    right = root.right
    left = splay(left, key)
    if left is not None:
        left.right = right
    return left


# ---------------------------------------------------------------------------
# Split / Merge
# ---------------------------------------------------------------------------


def splay_split(
    root: SplayNode[K, V] | None,
    key: K,
) -> tuple[SplayNode[K, V] | None, SplayNode[K, V] | None]:
    """Split so left tree contains all keys ≤ key, right tree contains all keys > key."""
    if root is None:
        return None, None

    root = splay(root, key)
    if root is None:
        return None, None

    if root.key <= key:
        right = root.right
        root.right = None
        return root, right
    else:
        left = root.left
        root.left = None
        return left, root


def splay_merge(
    left: SplayNode[K, V] | None,
    right: SplayNode[K, V] | None,
) -> SplayNode[K, V] | None:
    """Merge two trees: all keys in *left* must be ≤ all keys in *right*."""
    if left is None:
        return right
    if right is None:
        return left

    left = splay(left, right.key)
    if left is not None:
        left.right = right
    return left


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def splay_items(root: SplayNode[K, V] | None) -> list[tuple[K, V]]:
    """In-order traversal returning sorted (key, value) pairs."""
    result: list[tuple[K, V]] = []
    _inorder(root, result)
    return result


def _inorder(node: SplayNode[K, V] | None, out: list[tuple[K, V]]) -> None:
    if node is None:
        return
    _inorder(node.left, out)
    out.append((node.key, node.val))
    _inorder(node.right, out)
