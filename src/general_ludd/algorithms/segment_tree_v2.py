"""Segment tree v2: lazy sum/min/max, persistent (immutable nodes), and 2D.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

T = TypeVar("T")


# ── Lazy segment tree (range add + range sum/min/max) ──────────────────


class _BaseLazyTree(Generic[T]):
    def __init__(
        self,
        data: Sequence[T],
        merge: Callable[[T, T], T],
        identity: T,
    ) -> None:
        self._n = len(data)
        self._merge = merge
        self._identity = identity
        self._size = 1 << (self._n - 1).bit_length() if self._n else 1
        self._tree: list[T] = [identity] * (2 * self._size)
        self._lazy: list[T] = [identity] * (2 * self._size)
        for i in range(self._n):
            self._tree[self._size + i] = data[i]
        for i in range(self._size - 1, 0, -1):
            self._tree[i] = merge(self._tree[2 * i], self._tree[2 * i + 1])

    @property
    def n(self) -> int:
        return self._n

    def _apply(self, pos: int, delta: T, seg_len: int) -> None:
        raise NotImplementedError

    def _push(self, pos: int, seg_len: int) -> None:
        if self._lazy[pos] != self._identity:
            d = self._lazy[pos]
            half = seg_len >> 1
            self._apply(2 * pos, d, half)
            self._apply(2 * pos + 1, d, seg_len - half)
            self._lazy[pos] = self._identity

    def range_update(self, left: int, right: int, delta: T) -> None:
        def _upd(pos: int, seg_l: int, seg_r: int) -> None:
            if left >= seg_r or right <= seg_l:
                return
            if left <= seg_l and seg_r <= right:
                self._apply(pos, delta, seg_r - seg_l)
                return
            self._push(pos, seg_r - seg_l)
            mid = (seg_l + seg_r) >> 1
            _upd(2 * pos, seg_l, mid)
            _upd(2 * pos + 1, mid, seg_r)
            self._tree[pos] = self._merge(self._tree[2 * pos], self._tree[2 * pos + 1])

        _upd(1, 0, self._size)

    def range_query(self, left: int, right: int) -> T:
        def _qry(pos: int, seg_l: int, seg_r: int) -> T:
            if left >= seg_r or right <= seg_l:
                return self._identity
            if left <= seg_l and seg_r <= right:
                return self._tree[pos]
            self._push(pos, seg_r - seg_l)
            mid = (seg_l + seg_r) >> 1
            return self._merge(_qry(2 * pos, seg_l, mid), _qry(2 * pos + 1, mid, seg_r))

        return _qry(1, 0, self._size)

    def __len__(self) -> int:
        return self._n


class _LazySumTree(_BaseLazyTree[int]):
    def __init__(self, data: list[int]) -> None:
        super().__init__(data, merge=int.__add__, identity=0)

    def _apply(self, pos: int, delta: int, seg_len: int) -> None:
        self._tree[pos] += delta * seg_len
        if pos < self._size:
            self._lazy[pos] += delta


class _LazyMinTree(_BaseLazyTree[int]):
    """Range add + range min. _apply uses += since min(a+d,b+d) = min(a,b)+d."""

    def __init__(self, data: list[int]) -> None:
        super().__init__(data, merge=min, identity=sys.maxsize)

    def _apply(self, pos: int, delta: int, seg_len: int) -> None:
        self._tree[pos] += delta
        if pos < self._size:
            self._lazy[pos] += delta


class _LazyMaxTree(_BaseLazyTree[int]):
    """Range add + range max. _apply uses += since max(a+d,b+d) = max(a,b)+d."""

    def __init__(self, data: list[int]) -> None:
        super().__init__(data, merge=max, identity=-sys.maxsize - 1)

    def _apply(self, pos: int, delta: int, seg_len: int) -> None:
        self._tree[pos] += delta
        if pos < self._size:
            self._lazy[pos] += delta


LazySegmentTreeV2 = _BaseLazyTree  # re-export generic base


def lazy_sum_tree(data: list[int]) -> _LazySumTree:
    """Execute ``lazy_sum_tree``."""
    return _LazySumTree(data)


def lazy_min_tree(data: list[int]) -> _LazyMinTree:
    """Execute ``lazy_min_tree``."""
    return _LazyMinTree(data)


def lazy_max_tree(data: list[int]) -> _LazyMaxTree:
    """Execute ``lazy_max_tree``."""
    return _LazyMaxTree(data)


# ── Persistent segment tree (immutable node versioning) ─────────────────


class _Node:
    __slots__ = ("left_child", "right_child", "value")

    def __init__(
        self,
        value: int = 0,
        left: _Node | None = None,
        right: _Node | None = None,
    ) -> None:
        self.value = value
        self.left_child = left
        self.right_child = right


class PersistentSegTree:
    """Point updates create new roots; all prior versions remain queryable.

    Range queries use [left, right) half-open indexing.
    """

    def __init__(self, data: list[int]) -> None:
        """Initialize a ``PersistentSegTree`` instance."""
        self._n = len(data)
        self._roots: list[_Node | None] = []
        if self._n:
            self._roots.append(self._build(0, self._n - 1, data))

    def _build(self, lo: int, hi: int, data: list[int]) -> _Node:
        if lo == hi:
            return _Node(value=data[lo])
        mid = (lo + hi) >> 1
        left = self._build(lo, mid, data)
        right = self._build(mid + 1, hi, data)
        return _Node(value=left.value + right.value, left=left, right=right)

    @property
    def versions(self) -> int:
        """Execute ``versions``."""
        return len(self._roots)

    def update(self, idx: int, value: int) -> None:
        """Execute ``update``."""
        root = self._roots[-1]
        new_root = self._update(root, 0, self._n - 1, idx, value)
        self._roots.append(new_root)

    def _update(self, node: _Node | None, lo: int, hi: int, idx: int, value: int) -> _Node | None:
        if node is None:
            return None
        if lo == hi:
            return _Node(value=value)
        mid = (lo + hi) >> 1
        new = _Node()
        if idx <= mid:
            new.left_child = self._update(node.left_child, lo, mid, idx, value)
            new.right_child = node.right_child
        else:
            new.left_child = node.left_child
            new.right_child = self._update(node.right_child, mid + 1, hi, idx, value)
        left_v = new.left_child.value if new.left_child else 0
        right_v = new.right_child.value if new.right_child else 0
        new.value = left_v + right_v
        return new

    def query(self, version: int, left: int, right: int) -> int:
        """Execute ``query``."""
        return self._query(self._roots[version], 0, self._n - 1, left, right - 1)

    def _query(self, node: _Node | None, lo: int, hi: int, ql: int, qr: int) -> int:
        if node is None or ql > hi or qr < lo:
            return 0
        if ql <= lo and hi <= qr:
            return node.value
        mid = (lo + hi) >> 1
        return self._query(node.left_child, lo, mid, ql, qr) + self._query(node.right_child, mid + 1, hi, ql, qr)

    @property
    def n(self) -> int:
        """Execute ``n``."""
        return self._n


# ── 2D segment tree (point update, submatrix sum) ───────────────────────


class SegTree2D:
    """Point updates + submatrix range-sum queries on a 2D grid.

    Range queries use [r1, r2) x [c1, c2) half-open indexing.
    """

    def __init__(self, rows: int, cols: int) -> None:
        """Initialize a ``SegTree2D`` instance."""
        self._rows = rows
        self._cols = cols
        self._row_size = 1 << (rows - 1).bit_length() if rows else 1
        self._col_size = 1 << (cols - 1).bit_length() if cols else 1
        self._tree: list[list[int] | None] = [None] * (2 * self._row_size)

    def _ensure_inner(self, row_node: int) -> list[int]:
        if self._tree[row_node] is None:
            self._tree[row_node] = [0] * (2 * self._col_size)
        inner = self._tree[row_node]
        assert inner is not None
        return inner

    def update(self, row: int, col: int, delta: int) -> None:
        """Execute ``update``."""
        rpos = self._row_size + row
        while rpos:
            inner = self._ensure_inner(rpos)
            cpos = self._col_size + col
            while cpos:
                inner[cpos] += delta
                cpos >>= 1
            rpos >>= 1

    def query(self, r1: int, r2: int, c1: int, c2: int) -> int:
        """Execute ``query``."""
        def _col_sum(node: int, lo: int, hi: int, inner: list[int], qc1: int, qc2: int) -> int:
            if qc1 > hi or qc2 < lo:
                return 0
            if qc1 <= lo and hi <= qc2:
                return inner[node]
            mid = (lo + hi) >> 1
            return _col_sum(2 * node, lo, mid, inner, qc1, qc2) + _col_sum(2 * node + 1, mid + 1, hi, inner, qc1, qc2)

        total = 0
        r1_pos = self._row_size + r1
        r2_pos = self._row_size + r2
        while r1_pos < r2_pos:
            if r1_pos & 1:
                inner = self._tree[r1_pos]
                if inner is not None:
                    total += _col_sum(1, 0, self._col_size - 1, inner, c1, c2 - 1)
                r1_pos += 1
            if r2_pos & 1:
                r2_pos -= 1
                inner = self._tree[r2_pos]
                if inner is not None:
                    total += _col_sum(1, 0, self._col_size - 1, inner, c1, c2 - 1)
            r1_pos >>= 1
            r2_pos >>= 1
        return total


# ── 2D segment tree with lazy row updates ───────────────────────────────


class LazySegTree2D:
    """2D grid with row-assign and column-sum queries over row ranges.

    assign_row() sets an entire row's values.
    col_query() sums column values over a row range [r1, r2).
    """

    def __init__(self, rows: int, cols: int) -> None:
        """Initialize a ``LazySegTree2D`` instance."""
        self._cols = cols
        self._size = 1 << (rows - 1).bit_length() if rows else 1
        self._tree: list[list[int] | None] = [None] * (2 * self._size)

    def assign_row(self, row: int, values: list[int]) -> None:
        """Execute ``assign_row``."""
        pos = self._size + row
        self._tree[pos] = list(values)
        pos >>= 1
        while pos:
            left = self._tree[2 * pos]
            right = self._tree[2 * pos + 1]
            if left is not None and right is not None:
                self._tree[pos] = [lv + r for lv, r in zip(left, right, strict=False)]
            elif left is not None:
                self._tree[pos] = list(left)
            elif right is not None:
                self._tree[pos] = list(right)
            pos >>= 1

    def col_query(self, r1: int, r2: int, col: int) -> int:
        """Execute ``col_query``."""
        def _qry(pos: int, lo: int, hi: int) -> int:
            if r1 > hi or r2 - 1 < lo:
                return 0
            if r1 <= lo and hi < r2:
                inner = self._tree[pos]
                return inner[col] if inner is not None else 0
            mid = (lo + hi) >> 1
            return _qry(2 * pos, lo, mid) + _qry(2 * pos + 1, mid + 1, hi)

        return _qry(1, 0, self._size - 1)
