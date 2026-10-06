"""Trie primitives shared by persistent and transient vectors."""

from __future__ import annotations

from typing import Any

_SHIFT_INC = 5
_BRANCH = 1 << _SHIFT_INC
_MASK = _BRANCH - 1


def _node_new() -> list[Any]:
    return [None] * _BRANCH


def _node_copy_set(node: list[Any], idx: int, val: Any) -> list[Any]:
    copied = node[:]
    copied[idx] = val
    return copied


def _tailoff(cnt: int) -> int:
    if cnt < _BRANCH:
        return 0
    return ((cnt - 1) >> _SHIFT_INC) << _SHIFT_INC


def _new_path(shift: int, node: list[Any]) -> list[Any]:
    """Create a path from shift down to leaf, storing node at the leaf."""
    if shift == 0:
        return node
    new_node = _node_new()
    new_node[0] = _new_path(shift - _SHIFT_INC, node)
    return new_node


def _push_tail(
    cnt: int,
    shift: int,
    root: list[Any],
    tail: list[Any],
) -> list[Any]:
    """Insert tail into the trie through path copying."""
    tail_off = cnt - len(tail)
    subidx = (tail_off >> shift) & _MASK
    if shift == _SHIFT_INC:
        return _node_copy_set(root, subidx, tail)
    child = root[subidx]
    if child is None:
        child = _node_new()
    nested = _push_tail(cnt, shift - _SHIFT_INC, child, tail)
    return _node_copy_set(root, subidx, nested)


def _array_for(
    cnt: int,
    shift: int,
    root: list[Any],
    tail: list[Any],
) -> list[Any]:
    if cnt == 0:
        return tail
    node = root
    for level in range(shift, 0, -_SHIFT_INC):
        idx = (cnt >> level) & _MASK
        nested = node[idx]
        if nested is None:
            return tail
        node = nested
    return node


def _pop_tail(cnt: int, shift: int, root: list[Any]) -> list[Any] | None:
    """Remove the tail leaf from the trie after a pop."""
    subidx = (cnt >> shift) & _MASK
    if shift > _SHIFT_INC:
        child = root[subidx]
        if child is None:
            return None
        nested = _pop_tail(cnt, shift - _SHIFT_INC, child)
        if nested is None:
            if subidx == 0:
                return None
            return _node_copy_set(root, subidx, None)
        return _node_copy_set(root, subidx, nested)
    if subidx == 0:
        return None
    return _node_copy_set(root, subidx, None)

