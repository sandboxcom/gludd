"""Zero-suppressed Decision Diagram (ZDD).

Set-of-sets representation: each path from root to 1-terminal encodes a set
of variables (those taken on the hi branch).  Zero-suppression rule:
skip nodes whose hi child is 0 (False-terminal).

Public API:
    zdd_empty()       → ⊥ terminal
    zdd_base()        → 1 terminal (family {empty set})
    zdd_unit(var)     → singleton family {{var}}
    zdd_union(a, b)   → family union
    zdd_int(a, b)     → family intersection
    zdd_diff(a, b)    → family difference
    zdd_count(node)   → number of sets in the family
    zdd_enumerate(node) → list of frozensets
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class ZDDNode:
    """Represent ``ZDDNode`` values."""
    idx: int
    lo: ZDDNode
    hi: ZDDNode


_UNIQUE: dict[tuple[int, int, int], ZDDNode] = {}

_TERMINAL_BOTTOM: ZDDNode = ZDDNode(-2, lo=cast(ZDDNode, None), hi=cast(ZDDNode, None))
_TERMINAL_TOP: ZDDNode = ZDDNode(-1, lo=cast(ZDDNode, None), hi=cast(ZDDNode, None))


def _mk(var: int, lo: ZDDNode, hi: ZDDNode) -> ZDDNode:
    if hi is _TERMINAL_BOTTOM:
        return lo
    key = (var, id(lo), id(hi))
    node = _UNIQUE.get(key)
    if node is not None:
        return node
    node = ZDDNode(var, lo, hi)
    _UNIQUE[key] = node
    return node


def zdd_empty() -> ZDDNode:
    """Execute ``zdd_empty``."""
    return _TERMINAL_BOTTOM


def zdd_base() -> ZDDNode:
    """Execute ``zdd_base``."""
    return _TERMINAL_TOP


def zdd_unit(var: int) -> ZDDNode:
    """Execute ``zdd_unit``."""
    return _mk(var, _TERMINAL_BOTTOM, _TERMINAL_TOP)


def zdd_powerset(vars: list[int]) -> ZDDNode:
    """Execute ``zdd_powerset``."""
    result = zdd_base()
    for v in reversed(sorted(vars)):
        result = _mk(v, result, result)
    return result


def zdd_union(a: ZDDNode, b: ZDDNode) -> ZDDNode:
    """Execute ``zdd_union``."""
    return _op_union(a, b, {})


def _op_union(a: ZDDNode, b: ZDDNode, cache: dict[tuple[int, int], ZDDNode]) -> ZDDNode:
    if a is b:
        return a
    key = (id(a), id(b))
    cached = cache.get(key)
    if cached is not None:
        return cached
    if a is _TERMINAL_BOTTOM:
        return b
    if b is _TERMINAL_BOTTOM:
        return a
    if a is _TERMINAL_TOP:
        if b is _TERMINAL_TOP:
            return _TERMINAL_TOP
        result = _mk(b.idx, _op_union(a, b.lo, cache), b.hi)
        cache[key] = result
        return result
    if b is _TERMINAL_TOP:
        result = _mk(a.idx, _op_union(a.lo, b, cache), a.hi)
        cache[key] = result
        return result
    if a.idx < b.idx:
        result = _mk(a.idx, _op_union(a.lo, b, cache), a.hi)
    elif a.idx > b.idx:
        result = _mk(b.idx, _op_union(a, b.lo, cache), b.hi)
    else:
        result = _mk(
            a.idx,
            _op_union(a.lo, b.lo, cache),
            _op_union(a.hi, b.hi, cache),
        )
    cache[key] = result
    return result


def zdd_int(a: ZDDNode, b: ZDDNode) -> ZDDNode:
    """Execute ``zdd_int``."""
    return _op_int(a, b, {})


def _op_int(a: ZDDNode, b: ZDDNode, cache: dict[tuple[int, int], ZDDNode]) -> ZDDNode:
    if a is b:
        return a
    if a is _TERMINAL_BOTTOM or b is _TERMINAL_BOTTOM:
        return _TERMINAL_BOTTOM
    key = (id(a), id(b))
    cached = cache.get(key)
    if cached is not None:
        return cached
    if a is _TERMINAL_TOP:
        return _op_int(a, b.lo, cache)
    if b is _TERMINAL_TOP:
        return _op_int(a.lo, b, cache)
    if a.idx < b.idx:
        result = _op_int(a.lo, b, cache)
    elif a.idx > b.idx:
        result = _op_int(a, b.lo, cache)
    else:
        result = _mk(
            a.idx,
            _op_int(a.lo, b.lo, cache),
            _op_int(a.hi, b.hi, cache),
        )
    cache[key] = result
    return result


def zdd_diff(a: ZDDNode, b: ZDDNode) -> ZDDNode:
    """Execute ``zdd_diff``."""
    return _op_diff(a, b, {})


def _op_diff(a: ZDDNode, b: ZDDNode, cache: dict[tuple[int, int], ZDDNode]) -> ZDDNode:
    if a is b:
        return _TERMINAL_BOTTOM
    if a is _TERMINAL_BOTTOM:
        return _TERMINAL_BOTTOM
    if b is _TERMINAL_BOTTOM:
        return a
    key = (id(a), id(b))
    cached = cache.get(key)
    if cached is not None:
        return cached
    if a is _TERMINAL_TOP:
        if b is _TERMINAL_TOP:
            return _TERMINAL_BOTTOM
        return _op_diff(a, b.lo, cache)
    if b is _TERMINAL_TOP:
        result = _mk(a.idx, _op_diff(a.lo, b, cache), a.hi)
        cache[key] = result
        return result
    if a.idx < b.idx:
        result = _mk(a.idx, _op_diff(a.lo, b, cache), a.hi)
    elif a.idx > b.idx:
        result = _op_diff(a, b.lo, cache)
    else:
        result = _mk(
            a.idx,
            _op_diff(a.lo, b.lo, cache),
            _op_diff(a.hi, b.hi, cache),
        )
    cache[key] = result
    return result


def zdd_count(node: ZDDNode) -> int:
    """Execute ``zdd_count``."""
    cache: dict[int, int] = {}

    def _count(n: ZDDNode) -> int:
        nid = id(n)
        if nid in cache:
            return cache[nid]
        if n is _TERMINAL_BOTTOM:
            return 0
        if n is _TERMINAL_TOP:
            return 1
        result = _count(n.lo) + _count(n.hi)
        cache[nid] = result
        return result

    return _count(node)


def zdd_enumerate(node: ZDDNode) -> list[frozenset[int]]:
    """Execute ``zdd_enumerate``."""
    cache: dict[int, list[frozenset[int]]] = {}

    def _enum(n: ZDDNode) -> list[frozenset[int]]:
        nid = id(n)
        if nid in cache:
            return cache[nid]
        if n is _TERMINAL_BOTTOM:
            return []
        if n is _TERMINAL_TOP:
            return [frozenset[int]()]
        lo_sets = _enum(n.lo)
        hi_sets = _enum(n.hi)
        result = lo_sets + [s | {n.idx} for s in hi_sets]
        cache[nid] = result
        return result

    return _enum(node)
