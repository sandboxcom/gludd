"""Fenwick tree v2: BIT, range update/query, 2D BIT, order statistic tree.

Pure-Python, stdlib only. 0-indexed external interface; internal is 1-indexed.
"""

from __future__ import annotations


class BIT:
    """Fenwick tree — point update, prefix sum query. O(log n)."""

    def __init__(self, size: int) -> None:
        self.n = size
        self.tree = [0] * (size + 1)

    def add(self, idx: int, delta: int) -> None:
        i = idx + 1
        while i <= self.n:
            self.tree[i] += delta
            i += i & -i

    def prefix_sum(self, idx: int) -> int:
        i = idx + 1
        s = 0
        while i > 0:
            s += self.tree[i]
            i -= i & -i
        return s

    def range_sum(self, left: int, right: int) -> int:
        if left > right:
            return 0
        return self.prefix_sum(right) - (self.prefix_sum(left - 1) if left > 0 else 0)

    def from_array(self, arr: list[int]) -> BIT:
        for i, val in enumerate(arr):
            self.add(i, val)
        return self


class RangeUpdatePointQuery:
    """Fenwick tree backing difference array — range add, point value. O(log n)."""

    def __init__(self, size: int) -> None:
        self.bit = BIT(size)

    def from_values(self, arr: list[int]) -> RangeUpdatePointQuery:
        self.bit.add(0, arr[0])
        for i in range(1, len(arr)):
            self.bit.add(i, arr[i] - arr[i - 1])
        return self

    def range_add(self, left: int, right: int, delta: int) -> None:
        self.bit.add(left, delta)
        if right + 1 < self.bit.n:
            self.bit.add(right + 1, -delta)

    def point_value(self, idx: int) -> int:
        return self.bit.prefix_sum(idx)


class RangeUpdateRangeQuery:
    """Range add + range sum via two BITs. O(log n)."""

    def __init__(self, size: int) -> None:
        self.n = size
        self.bit1 = BIT(size)
        self.bit2 = BIT(size)

    def from_values(self, arr: list[int]) -> RangeUpdateRangeQuery:
        for i, val in enumerate(arr):
            self.range_add(i, i, val)
        return self

    def range_add(self, left: int, right: int, delta: int) -> None:
        self.bit1.add(left, delta)
        self.bit1.add(right + 1, -delta)
        self.bit2.add(left, delta * (left - 1))
        self.bit2.add(right + 1, -delta * right)

    def _prefix_sum(self, idx: int) -> int:
        if idx < 0:
            return 0
        return self.bit1.prefix_sum(idx) * idx - self.bit2.prefix_sum(idx)

    def range_sum(self, left: int, right: int) -> int:
        return self._prefix_sum(right) - self._prefix_sum(left - 1)


class BIT2D:
    """2D BIT — point update, prefix sum query. O(log rows * log cols)."""

    def __init__(self, rows: int, cols: int) -> None:
        self.r = rows
        self.c = cols
        self.tree = [[0] * (cols + 1) for _ in range(rows + 1)]

    def add(self, r: int, c: int, delta: int) -> None:
        i = r + 1
        while i <= self.r:
            j = c + 1
            while j <= self.c:
                self.tree[i][j] += delta
                j += j & -j
            i += i & -i

    def prefix_sum(self, r: int, c: int) -> int:
        s = 0
        i = r + 1
        while i > 0:
            j = c + 1
            while j > 0:
                s += self.tree[i][j]
                j -= j & -j
            i -= i & -i
        return s

    def rect_sum(self, r1: int, c1: int, r2: int, c2: int) -> int:
        return (
            self.prefix_sum(r2, c2)
            - (self.prefix_sum(r1 - 1, c2) if r1 > 0 else 0)
            - (self.prefix_sum(r2, c1 - 1) if c1 > 0 else 0)
            + (self.prefix_sum(r1 - 1, c1 - 1) if r1 > 0 and c1 > 0 else 0)
        )


class OrderStatisticTree:
    """Order statistic tree backed by Fenwick tree frequency array.

    Supports insert(k), remove(k), kth(k), count_less_than(k),
    count_range(lo, hi).  Assumes ≤65535 distinct values and non-negative keys.
    """

    MAX_VAL = 65535

    def __init__(self, max_val: int = MAX_VAL) -> None:
        self.max_val = max_val
        self.bit = BIT(max_val + 1)
        self._size = 0

    def insert(self, val: int) -> None:
        if val < 0 or val > self.max_val:
            raise ValueError(f"val {val} out of range [0, {self.max_val}]")
        self.bit.add(val, 1)
        self._size += 1

    def remove(self, val: int) -> None:
        if val < 0 or val > self.max_val:
            raise ValueError(f"val {val} out of range [0, {self.max_val}]")
        if self.count(val) == 0:
            raise KeyError(f"val {val} not present")
        self.bit.add(val, -1)
        self._size -= 1

    def count(self, val: int) -> int:
        return self.bit.range_sum(val, val)

    def count_less_than(self, val: int) -> int:
        if val <= 0:
            return 0
        if val > self.max_val:
            return self._size
        return self.bit.prefix_sum(val - 1)

    def count_range(self, lo: int, hi: int) -> int:
        return self.bit.range_sum(lo, hi)

    def kth(self, k: int) -> int:
        if k < 0 or k >= self._size:
            raise ValueError(f"k={k} out of range [0, {self._size - 1}]")
        lo, hi = 0, self.max_val
        while lo < hi:
            mid = (lo + hi) // 2
            if self.bit.prefix_sum(mid) <= k:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def __len__(self) -> int:
        return self._size
