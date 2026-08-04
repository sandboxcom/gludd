from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Base segment tree (point-update, range-query) — generic over aggregation fn
# ---------------------------------------------------------------------------


class SegmentTree(Generic[T]):
    def __init__(
        self,
        data: Sequence[T],
        func: Callable[[T, T], T],
        identity: T,
    ) -> None:
        self._n = len(data)
        self._func = func
        self._identity = identity
        self._size = 1 << (self._n - 1).bit_length()
        self._tree: list[T] = [identity] * (2 * self._size)
        for i in range(self._n):
            self._tree[self._size + i] = data[i]
        for i in range(self._size - 1, 0, -1):
            self._tree[i] = func(self._tree[2 * i], self._tree[2 * i + 1])

    @property
    def n(self) -> int:
        return self._n

    def query(self, left: int, right: int) -> T:
        left += self._size
        right += self._size
        res = self._identity
        while left < right:
            if left & 1:
                res = self._func(res, self._tree[left])
                left += 1
            if right & 1:
                right -= 1
                res = self._func(res, self._tree[right])
            left >>= 1
            right >>= 1
        return res

    def update(self, idx: int, value: T) -> None:
        pos = self._size + idx
        self._tree[pos] = value
        pos >>= 1
        while pos:
            self._tree[pos] = self._func(self._tree[2 * pos], self._tree[2 * pos + 1])
            pos >>= 1

    def __getitem__(self, idx: int) -> T:
        return self._tree[self._size + idx]

    def __len__(self) -> int:
        return self._n


# ---------------------------------------------------------------------------
# Lazy segment tree — range updates + range queries (sum)
# ---------------------------------------------------------------------------


class LazySegmentTree:
    def __init__(self, data: list[int]) -> None:
        self._n = len(data)
        self._size = 1 << (self._n - 1).bit_length()
        self._tree = [0] * (2 * self._size)
        self._lazy = [0] * (2 * self._size)
        for i in range(self._n):
            self._tree[self._size + i] = data[i]
        for i in range(self._size - 1, 0, -1):
            self._tree[i] = self._tree[2 * i] + self._tree[2 * i + 1]

    def _apply(self, pos: int, delta: int, seg_len: int) -> None:
        self._tree[pos] += delta * seg_len
        if pos < self._size:
            self._lazy[pos] += delta

    def _push(self, pos: int, seg_len: int) -> None:
        if self._lazy[pos]:
            half = seg_len >> 1
            self._apply(2 * pos, self._lazy[pos], half)
            self._apply(2 * pos + 1, self._lazy[pos], seg_len - half)
            self._lazy[pos] = 0

    def _range_update(self, pos: int, seg_left: int, seg_right: int, ql: int, qr: int, delta: int) -> None:
        if ql >= seg_right or qr <= seg_left:
            return
        if ql <= seg_left and seg_right <= qr:
            self._apply(pos, delta, seg_right - seg_left)
            return
        self._push(pos, seg_right - seg_left)
        mid = (seg_left + seg_right) >> 1
        self._range_update(2 * pos, seg_left, mid, ql, qr, delta)
        self._range_update(2 * pos + 1, mid, seg_right, ql, qr, delta)
        self._tree[pos] = self._tree[2 * pos] + self._tree[2 * pos + 1]

    def range_add(self, left: int, right: int, delta: int) -> None:
        self._range_update(1, 0, self._size, left, right, delta)

    def _range_query(self, pos: int, seg_left: int, seg_right: int, ql: int, qr: int) -> int:
        if ql >= seg_right or qr <= seg_left:
            return 0
        if ql <= seg_left and seg_right <= qr:
            return self._tree[pos]
        self._push(pos, seg_right - seg_left)
        mid = (seg_left + seg_right) >> 1
        return self._range_query(2 * pos, seg_left, mid, ql, qr) + self._range_query(
            2 * pos + 1, mid, seg_right, ql, qr
        )

    def range_sum(self, left: int, right: int) -> int:
        return self._range_query(1, 0, self._size, left, right)

    def __len__(self) -> int:
        return self._n


# =========================================================================
# Tests
# =========================================================================


class TestSegmentTreeBuild:
    def test_build_empty(self) -> None:
        st: SegmentTree[int] = SegmentTree([], func=int.__add__, identity=0)
        assert len(st) == 0

    def test_build_singleton(self) -> None:
        st = SegmentTree([7], func=int.__add__, identity=0)
        assert st.query(0, 1) == 7
        assert st[0] == 7

    def test_build_power_of_two(self) -> None:
        st = SegmentTree([1, 2, 3, 4], func=int.__add__, identity=0)
        assert st.query(0, 4) == 10
        assert st.query(1, 3) == 5

    def test_build_non_power_of_two(self) -> None:
        st = SegmentTree([3, 1, 4, 1, 5], func=int.__add__, identity=0)
        assert st.query(0, 5) == 14
        assert st.query(2, 5) == 10


class TestSegmentTreeRangeQuery:
    def test_full_range(self) -> None:
        st = SegmentTree([10, 20, 30, 40, 50], func=int.__add__, identity=0)
        assert st.query(0, 5) == 150

    def test_empty_range(self) -> None:
        st = SegmentTree([10, 20, 30], func=int.__add__, identity=0)
        assert st.query(1, 1) == 0

    def test_single_element_range(self) -> None:
        st = SegmentTree([5, 8, 13], func=int.__add__, identity=0)
        assert st.query(1, 2) == 8

    def test_partial_range(self) -> None:
        st = SegmentTree([2, 4, 6, 8, 10], func=int.__add__, identity=0)
        assert st.query(1, 4) == 18


class TestSegmentTreePointUpdate:
    def test_update_and_re_query(self) -> None:
        st = SegmentTree([1, 2, 3, 4, 5], func=int.__add__, identity=0)
        st.update(2, 99)
        assert st[2] == 99
        assert st.query(0, 5) == 111

    def test_update_first_and_last(self) -> None:
        st = SegmentTree([10, 20, 30], func=int.__add__, identity=0)
        st.update(0, 100)
        st.update(2, 300)
        assert st.query(0, 3) == 420

    def test_update_to_zero(self) -> None:
        st = SegmentTree([7, 8, 9], func=int.__add__, identity=0)
        st.update(1, 0)
        assert st.query(0, 3) == 16


class TestSegmentTreeMinMax:
    def test_range_min(self) -> None:
        st = SegmentTree([5, 2, 8, 1, 9], func=min, identity=sys.maxsize)
        assert st.query(0, 5) == 1
        assert st.query(0, 2) == 2

    def test_range_max(self) -> None:
        st = SegmentTree([5, 2, 8, 1, 9], func=max, identity=-sys.maxsize - 1)
        assert st.query(0, 5) == 9
        assert st.query(1, 4) == 8

    def test_min_update(self) -> None:
        st = SegmentTree([5, 2, 8, 1, 9], func=min, identity=sys.maxsize)
        st.update(0, 0)
        assert st.query(0, 5) == 0

    def test_max_update(self) -> None:
        st = SegmentTree([5, 2, 8, 1, 9], func=max, identity=-sys.maxsize - 1)
        st.update(3, 99)
        assert st.query(0, 5) == 99


class TestLazySegmentTree:
    def test_build_and_full_sum(self) -> None:
        lst = LazySegmentTree([1, 2, 3, 4, 5])
        assert lst.range_sum(0, 5) == 15

    def test_range_add_full(self) -> None:
        lst = LazySegmentTree([1, 2, 3, 4, 5])
        lst.range_add(0, 5, 10)
        assert lst.range_sum(0, 5) == 65

    def test_range_add_prefix(self) -> None:
        lst = LazySegmentTree([1, 2, 3, 4, 5])
        lst.range_add(0, 2, 5)
        assert lst.range_sum(0, 2) == 13
        assert lst.range_sum(2, 5) == 12

    def test_range_add_suffix(self) -> None:
        lst = LazySegmentTree([10, 20, 30, 40])
        lst.range_add(2, 4, 100)
        assert lst.range_sum(2, 4) == 270

    def test_lazy_propagation_read_triggers_push(self) -> None:
        lst = LazySegmentTree([0, 0, 0, 0, 0, 0, 0, 0])
        lst.range_add(2, 6, 7)
        assert lst.range_sum(0, 8) == 28

    def test_chained_range_updates(self) -> None:
        lst = LazySegmentTree([0] * 10)
        lst.range_add(0, 10, 1)
        lst.range_add(3, 7, 2)
        lst.range_add(5, 6, 5)
        assert lst.range_sum(0, 10) == 10 + 8 + 5
        assert lst.range_sum(4, 5) == 3
        assert lst.range_sum(5, 6) == 8

    def test_large_array_incremental_updates(self) -> None:
        n = 100
        lst = LazySegmentTree(list(range(n)))
        expected = list(range(n))
        for _ in range(10):
            for i in range(0, n, 7):
                lst.range_add(i, min(i + 5, n), 3)
                for j in range(i, min(i + 5, n)):
                    expected[j] += 3
        for start in range(0, n, 13):
            end = min(start + 11, n)
            assert lst.range_sum(start, end) == sum(expected[start:end])
