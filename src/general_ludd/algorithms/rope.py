"""Rope: a node-weight-aware split/merge tree for large mutable strings.

A rope represents a string as a binary tree where leaves hold substrings
and internal nodes hold the total weight (character count) of their left
subtree.  Split, concat, insert, delete, and report (substring) all run
in O(log n) expected time on a balanced tree.

Pure-Python, stdlib only.  Follows project conventions (__slots__,
from __future__ import annotations).
"""

from __future__ import annotations

import sys


class RopeNode:
    """Node in a rope tree.

    Leaf:  data holds a substring, left=None, right=None,
           weight = len(data).
    Inner: data="", weight = total characters in left subtree,
           left/right point to children.
    """

    __slots__ = ("data", "left", "right", "weight")

    def __init__(
        self,
        data: str = "",
        weight: int = 0,
        left: RopeNode | None = None,
        right: RopeNode | None = None,
    ) -> None:
        self.data = data
        self.weight = weight if weight else len(data)
        self.left = left
        self.right = right


SYSTEM_RECURSION_LIMIT = sys.getrecursionlimit()


def rope_from_str(s: str) -> RopeNode | None:
    """Build a balanced rope from *s*.  Returns None for empty string."""
    if not s:
        return None
    return _build_balanced(s, 0, len(s))


def _build_balanced(s: str, lo: int, hi: int) -> RopeNode:
    """Recursive balanced build: split s[lo:hi] at midpoint."""
    if hi - lo <= 256:
        return RopeNode(data=s[lo:hi])
    mid = (lo + hi) // 2
    left = _build_balanced(s, lo, mid)
    right = _build_balanced(s, mid, hi)
    return RopeNode(weight=_rope_weight(left), left=left, right=right)


def rope_to_string(root: RopeNode | None) -> str:
    """In-order concatenation of all leaf data."""
    parts: list[str] = []
    _traverse(root, parts)
    return "".join(parts)


def _traverse(node: RopeNode | None, out: list[str]) -> None:
    if node is None:
        return
    _traverse(node.left, out)
    if node.data:
        out.append(node.data)
    _traverse(node.right, out)


def rope_weight(root: RopeNode | None) -> int:
    """Total character count of the rope."""
    return _rope_weight(root)


def _rope_weight(node: RopeNode | None) -> int:
    if node is None:
        return 0
    if node.data:
        return len(node.data)
    return node.weight + _rope_weight(node.right)


def rope_report(root: RopeNode | None, lo: int, hi: int) -> str:
    """Extract substring root[lo:hi].  Clamps bounds to [0, len)."""
    if root is None:
        return ""
    total = rope_weight(root)
    lo = max(0, lo)
    hi = min(hi, total)
    if lo >= hi:
        return ""
    return _report(root, lo, hi)


def _report(node: RopeNode | None, lo: int, hi: int) -> str:
    """Walk the tree collecting leaf data overlapping [lo, hi)."""
    if node is None or lo >= hi:
        return ""
    if node.data:
        return node.data[lo:hi]
    left_weight = node.weight
    result_parts: list[str] = []
    if lo < left_weight:
        result_parts.append(_report(node.left, lo, min(hi, left_weight)))
    if hi > left_weight:
        result_parts.append(_report(node.right, max(0, lo - left_weight), hi - left_weight))
    return "".join(result_parts)


def rope_concat(left: RopeNode | None, right: RopeNode | None) -> RopeNode | None:
    """Concatenate two ropes.  Returns left+right."""
    if left is None:
        return right
    if right is None:
        return left
    return RopeNode(weight=_rope_weight(left), left=left, right=right)


def rope_split(root: RopeNode | None, pos: int) -> tuple[RopeNode | None, RopeNode | None]:
    """Split *root* at *pos*: left gets first *pos* chars, right gets rest."""
    if root is None:
        return None, None
    total = rope_weight(root)
    pos = max(0, min(pos, total))
    if pos == 0:
        return None, root
    if pos == total:
        return root, None
    return _split_at(root, pos)


def _split_at(node: RopeNode, pos: int) -> tuple[RopeNode | None, RopeNode | None]:
    """Recursively split a non-leaf or leaf node at relative position *pos*."""
    if node.data:
        return (
            RopeNode(data=node.data[:pos]),
            RopeNode(data=node.data[pos:]),
        )
    left_weight = node.weight
    if pos < left_weight:
        assert node.left is not None
        left_left, left_right = _split_at(node.left, pos)
        right = RopeNode(weight=_rope_weight(left_right), left=left_right, right=node.right)
        return left_left, right
    if pos > left_weight:
        assert node.right is not None
        right_left, right_right = _split_at(node.right, pos - left_weight)
        left = RopeNode(weight=left_weight, left=node.left, right=right_left)
        return left, right_right
    return node.left, node.right


def rope_insert(root: RopeNode | None, s: str, pos: int) -> RopeNode | None:
    """Insert string *s* at position *pos*.  Clamps pos to [0, len]."""
    if root is None:
        return rope_from_str(s)
    total = rope_weight(root)
    pos = max(0, min(pos, total))
    left, right = rope_split(root, pos)
    mid = rope_from_str(s)
    return rope_concat(rope_concat(left, mid), right)


def rope_delete(root: RopeNode | None, lo: int, hi: int) -> RopeNode | None:
    """Delete characters in [lo, hi).  Clamps to [0, len]."""
    if root is None:
        return None
    total = rope_weight(root)
    lo = max(0, lo)
    hi = min(hi, total)
    if lo >= hi:
        return root
    left, rest = rope_split(root, lo)
    _, right = rope_split(rest, hi - lo)
    return rope_concat(left, right)


def rope_balance(root: RopeNode | None) -> RopeNode | None:
    """Rebuild a perfectly balanced rope from the content of *root*."""
    if root is None:
        return None
    leaf_data: list[str] = []
    _collect_leaves(root, leaf_data)
    if not leaf_data:
        return None
    return _build_balanced("".join(leaf_data), 0, sum(len(d) for d in leaf_data))


def _collect_leaves(node: RopeNode | None, out: list[str]) -> None:
    if node is None:
        return
    if node.data:
        out.append(node.data)
    else:
        _collect_leaves(node.left, out)
        _collect_leaves(node.right, out)
