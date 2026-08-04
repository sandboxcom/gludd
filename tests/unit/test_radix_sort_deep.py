"""Deep radix sort and counting sort tests: LSD radix, MSD radix,
American flag, counting sort, bucket sort, in-place MSD.

Pure-Python implementations (stdlib only).
"""

from __future__ import annotations

import typing

import pytest

# ── Counting Sort ─────────────────────────────────────────────────────


def counting_sort(arr: list[int]) -> list[int]:
    """Stable counting sort for non-negative integers."""
    if not arr:
        return []
    lo, hi = min(arr), max(arr)
    offset = -lo
    k = hi - lo + 1
    counts = [0] * k
    for v in arr:
        counts[v + offset] += 1
    for i in range(1, k):
        counts[i] += counts[i - 1]
    result = [0] * len(arr)
    for v in reversed(arr):
        idx = counts[v + offset] - 1
        result[idx] = v
        counts[v + offset] -= 1
    return result


def counting_sort_for_radix(arr: list[int], exp: int, base: int = 10) -> list[int]:
    """Stable counting sort on a single digit place (used by radix sorts)."""
    n = len(arr)
    counts = [0] * base
    for v in arr:
        digit = (abs(v) // exp) % base
        counts[digit] += 1
    for i in range(1, base):
        counts[i] += counts[i - 1]
    result = [0] * n
    for v in reversed(arr):
        digit = (abs(v) // exp) % base
        counts[digit] -= 1
        result[counts[digit]] = v
    return result


# ── LSD Radix Sort ────────────────────────────────────────────────────


def lsd_radix_sort(arr: list[int], base: int = 10) -> list[int]:
    """LSD radix sort handling negative integers via two-pass sign split."""
    if len(arr) <= 1:
        return list(arr)
    neg = [-v for v in arr if v < 0]
    pos = [v for v in arr if v >= 0]
    if neg:
        neg = _lsd_radix_unsigned(neg, base)
        neg = [-v for v in reversed(neg)]
    if pos:
        pos = _lsd_radix_unsigned(pos, base)
    return neg + pos


def _lsd_radix_unsigned(arr: list[int], base: int = 10) -> list[int]:
    if len(arr) <= 1:
        return list(arr)
    result = list(arr)
    max_val = max(result)
    exp = 1
    while max_val // exp > 0:
        result = counting_sort_for_radix(result, exp, base)
        exp *= base
    return result


# ── MSD Radix Sort ────────────────────────────────────────────────────


def msd_radix_sort(arr: list[int], base: int = 10) -> list[int]:
    """MSD radix sort handling negative integers."""
    if len(arr) <= 1:
        return list(arr)
    neg = [-v for v in arr if v < 0]
    pos = [v for v in arr if v >= 0]
    if neg:
        neg = _msd_radix_unsigned(neg, base)
        neg = [-v for v in reversed(neg)]
    if pos:
        pos = _msd_radix_unsigned(pos, base)
    return neg + pos


def _msd_radix_unsigned(arr: list[int], base: int = 10) -> list[int]:
    if len(arr) <= 1:
        return list(arr)
    max_val = max(arr)
    max_exp = 1
    while max_val // (max_exp * base) > 0:
        max_exp *= base
    return _msd_rec(arr, max_exp, base)


def _msd_rec(arr: list[int], exp: int, base: int) -> list[int]:
    if len(arr) <= 1 or exp == 0:
        return list(arr)
    buckets: list[list[int]] = [[] for _ in range(base)]
    for v in arr:
        digit = (v // exp) % base
        buckets[digit].append(v)
    result: list[int] = []
    for bucket in buckets:
        if bucket:
            result.extend(_msd_rec(bucket, exp // base, base))
    return result


# ── American Flag Sort ────────────────────────────────────────────────


def american_flag_sort(arr: list[int], base: int = 10) -> list[int]:
    """American flag sort (in-place MSD radix variant)."""
    if len(arr) <= 1:
        return list(arr)
    neg = [-v for v in arr if v < 0]
    pos = [v for v in arr if v >= 0]
    if neg:
        neg = _american_flag_unsigned(neg, base)
        neg = [-v for v in reversed(neg)]
    if pos:
        pos = _american_flag_unsigned(pos, base)
    return neg + pos


def _american_flag_unsigned(arr: list[int], base: int = 10) -> list[int]:
    result = list(arr)
    if not result:
        return result
    max_val = max(result)
    max_exp = 1
    while max_val // (max_exp * base) > 0:
        max_exp *= base
    return _af_rec(result, max_exp, base)


def _af_rec(arr: list[int], exp: int, base: int) -> list[int]:
    if len(arr) <= 1 or exp == 0:
        return arr

    count = [0] * base
    for v in arr:
        count[(v // exp) % base] += 1

    start = [0] * base
    running = 0
    for d in range(base):
        start[d] = running
        running += count[d]

    result = [0] * len(arr)
    pos = list(start)
    for v in arr:
        d = (v // exp) % base
        result[pos[d]] = v
        pos[d] += 1

    offset = 0
    for d in range(base):
        sub = _af_rec(result[offset : offset + count[d]], exp // base, base)
        result[offset : offset + count[d]] = sub
        offset += count[d]

    return result


# ── Bucket Sort ───────────────────────────────────────────────────────


def bucket_sort(arr: list[float]) -> list[float]:
    """Bucket sort for floating-point values in [0, 1) or any finite range."""
    if len(arr) <= 1:
        return list(arr)
    lo, hi = min(arr), max(arr)
    if lo == hi:
        return list(arr)
    n = len(arr)
    buckets: list[list[float]] = [[] for _ in range(n)]
    span = hi - lo
    for v in arr:
        idx = min(int((v - lo) / span * n), n - 1)
        buckets[idx].append(v)
    for bucket in buckets:
        bucket.sort()
    result: list[float] = []
    for bucket in buckets:
        result.extend(bucket)
    return result


# ── In-Place MSD Radix Sort ───────────────────────────────────────────


def inplace_msd_radix_sort(arr: list[int], base: int = 10) -> list[int]:
    """In-place MSD radix sort handling negative integers.

    Splits by sign, maps negatives to positives for sorting, then reverses.
    """
    if len(arr) <= 1:
        return list(arr)
    result = list(arr)
    neg = [-v for v in result if v < 0]
    pos = [v for v in result if v >= 0]
    neg_count = len(neg)
    if neg_count > 0:
        _inplace_msd_unsigned(neg, 0, neg_count, base)
        for i in range(neg_count):
            neg[i] = -neg[i]
        neg.reverse()
    if pos:
        _inplace_msd_unsigned(pos, 0, len(pos), base)
    return neg + pos


def _inplace_msd_unsigned(arr: list[int], lo: int, hi: int, base: int) -> None:
    if hi - lo <= 1:
        return
    max_val = max(arr[lo:hi])
    max_exp = 1
    while max_val // (max_exp * base) > 0:
        max_exp *= base
    _inplace_msd_rec(arr, lo, hi, max_exp, base)


def _inplace_msd_rec(arr: list[int], lo: int, hi: int, exp: int, base: int) -> None:
    if hi - lo <= 1 or exp == 0:
        return

    sub = arr[lo:hi]

    count = [0] * base
    for v in sub:
        count[(v // exp) % base] += 1

    start = [0] * base
    running = 0
    for d in range(base):
        start[d] = running
        running += count[d]

    temp = [0] * len(sub)
    pos = list(start)
    for v in sub:
        d = (v // exp) % base
        temp[pos[d]] = v
        pos[d] += 1

    arr[lo:hi] = temp

    offset = lo
    for d in range(base):
        _inplace_msd_rec(arr, offset, offset + count[d], exp // base, base)
        offset += count[d]


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestCountingSort:
    def test_sorts_correctly(self) -> None:
        arr = [4, 2, 2, 8, 3, 3, 1]
        assert counting_sort(arr) == [1, 2, 2, 3, 3, 4, 8]

    def test_is_stable(self) -> None:
        arr = [(4, "a"), (2, "b"), (4, "c"), (2, "d"), (3, "e")]
        sorted_by_key = counting_sort([x[0] for x in arr])
        assert sorted_by_key == [2, 2, 3, 4, 4]

    def test_empty(self) -> None:
        assert counting_sort([]) == []

    def test_single(self) -> None:
        assert counting_sort([42]) == [42]

    def test_negative_integers(self) -> None:
        arr = [-5, -1, -3, -2, -4]
        assert counting_sort(arr) == [-5, -4, -3, -2, -1]

    def test_large_range(self) -> None:
        arr = [1000, 0, 500, 250, 750]
        assert counting_sort(arr) == [0, 250, 500, 750, 1000]

    def test_all_same(self) -> None:
        assert counting_sort([7, 7, 7, 7]) == [7, 7, 7, 7]


class TestLSDRadixSort:
    def test_sorts_correctly(self) -> None:
        arr = [170, 45, 75, 90, 802, 24, 2, 66]
        assert lsd_radix_sort(arr) == [2, 24, 45, 66, 75, 90, 170, 802]

    def test_is_stable(self) -> None:
        assert lsd_radix_sort([4, 2, 4, 2, 3]) == [2, 2, 3, 4, 4]

    def test_handles_negatives(self) -> None:
        arr = [-5, 3, -1, 0, -10, 7]
        assert lsd_radix_sort(arr) == [-10, -5, -1, 0, 3, 7]

    def test_all_negative(self) -> None:
        arr = [-50, -5, -100, -1, -20]
        assert lsd_radix_sort(arr) == [-100, -50, -20, -5, -1]

    def test_empty(self) -> None:
        assert lsd_radix_sort([]) == []

    def test_single(self) -> None:
        assert lsd_radix_sort([99]) == [99]

    def test_base_16(self) -> None:
        arr = [255, 16, 0, 1, 4095, 256]
        assert lsd_radix_sort(arr, base=16) == [0, 1, 16, 255, 256, 4095]

    def test_large_values(self) -> None:
        arr = [1000000, 1, 500000, 999999]
        assert lsd_radix_sort(arr) == [1, 500000, 999999, 1000000]

    def test_duplicates(self) -> None:
        arr = [5, 5, 1, 1, 3, 3, 5]
        assert lsd_radix_sort(arr) == [1, 1, 3, 3, 5, 5, 5]

    def test_already_sorted(self) -> None:
        arr = [1, 2, 3, 4, 5]
        assert lsd_radix_sort(arr) == [1, 2, 3, 4, 5]

    def test_reverse_sorted(self) -> None:
        arr = [9, 8, 7, 6, 5]
        assert lsd_radix_sort(arr) == [5, 6, 7, 8, 9]


class TestMSDRadixSort:
    def test_sorts_correctly(self) -> None:
        arr = [170, 45, 75, 90, 802, 24, 2, 66]
        assert msd_radix_sort(arr) == [2, 24, 45, 66, 75, 90, 170, 802]

    def test_handles_negatives(self) -> None:
        arr = [-5, 3, -1, 0, -10, 7]
        assert msd_radix_sort(arr) == [-10, -5, -1, 0, 3, 7]

    def test_empty(self) -> None:
        assert msd_radix_sort([]) == []

    def test_single(self) -> None:
        assert msd_radix_sort([42]) == [42]

    def test_large_values(self) -> None:
        arr = [10000, 1, 5000, 9999]
        assert msd_radix_sort(arr) == [1, 5000, 9999, 10000]


class TestAmericanFlagSort:
    def test_sorts_correctly(self) -> None:
        arr = [170, 45, 75, 90, 802, 24, 2, 66]
        assert american_flag_sort(arr) == [2, 24, 45, 66, 75, 90, 170, 802]

    def test_handles_negatives(self) -> None:
        arr = [-5, 3, -1, 0, -10, 7]
        assert american_flag_sort(arr) == [-10, -5, -1, 0, 3, 7]

    def test_single_element(self) -> None:
        assert american_flag_sort([1]) == [1]

    def test_all_same(self) -> None:
        arr = [7, 7, 7, 7, 7]
        assert american_flag_sort(arr) == [7, 7, 7, 7, 7]

    def test_empty(self) -> None:
        assert american_flag_sort([]) == []

    def test_large_shuffled(self) -> None:
        arr = [512, 128, 256, 64, 32, 16, 8, 4, 2, 1, 1024]
        expected = sorted(arr)
        assert american_flag_sort(arr) == expected


class TestBucketSort:
    def test_sorts_uniform_floats(self) -> None:
        arr = [0.78, 0.17, 0.39, 0.26, 0.72, 0.94, 0.21, 0.12]
        assert bucket_sort(arr) == sorted(arr)

    def test_handles_negative_floats(self) -> None:
        arr = [-0.5, 0.3, -0.1, 0.0, -0.9, 0.7]
        assert bucket_sort(arr) == sorted(arr)

    def test_empty(self) -> None:
        assert bucket_sort([]) == []

    def test_single(self) -> None:
        assert bucket_sort([0.5]) == [0.5]

    def test_all_same(self) -> None:
        arr = [0.42, 0.42, 0.42]
        assert bucket_sort(arr) == [0.42, 0.42, 0.42]

    def test_large_range_floats(self) -> None:
        arr = [1000.5, 0.1, 500.3, 250.7, 750.2]
        assert bucket_sort(arr) == [0.1, 250.7, 500.3, 750.2, 1000.5]

    def test_mixed_sign_floats(self) -> None:
        arr = [-100.0, 50.0, -25.5, 0.0, 75.25]
        assert bucket_sort(arr) == sorted(arr)


class TestInplaceMSDRadixSort:
    def test_sorts_correctly(self) -> None:
        arr = [170, 45, 75, 90, 802, 24, 2, 66]
        assert inplace_msd_radix_sort(arr) == [2, 24, 45, 66, 75, 90, 170, 802]

    def test_handles_negatives(self) -> None:
        arr = [-5, 3, -1, 0, -10, 7]
        assert inplace_msd_radix_sort(arr) == [-10, -5, -1, 0, 3, 7]

    def test_all_negative(self) -> None:
        arr = [-50, -5, -100, -1, -20]
        assert inplace_msd_radix_sort(arr) == [-100, -50, -20, -5, -1]

    def test_empty(self) -> None:
        assert inplace_msd_radix_sort([]) == []

    def test_single(self) -> None:
        assert inplace_msd_radix_sort([99]) == [99]

    def test_duplicates(self) -> None:
        arr = [5, 2, 5, 1, 2, 1]
        assert inplace_msd_radix_sort(arr) == [1, 1, 2, 2, 5, 5]

    def test_already_sorted(self) -> None:
        arr = [10, 20, 30, 40, 50]
        assert inplace_msd_radix_sort(arr) == [10, 20, 30, 40, 50]

    def test_mixed_zeros(self) -> None:
        arr = [0, 5, 0, 3, 0, 1]
        assert inplace_msd_radix_sort(arr) == [0, 0, 0, 1, 3, 5]


# ── Cross-algorithm consistency ───────────────────────────────────────


class TestConsistency:
    """All integer sorts must agree with Python's built-in sorted()."""

    CASES: typing.ClassVar[list[list[int]]] = [
        [],
        [1],
        [5, 3, 1, 4, 2],
        [170, 45, 75, 90, 802, 24, 2, 66],
        [-5, 3, -1, 0, -10, 7, 100, -50],
        [0, 0, 0, 0],
        [1, 1, 1, 2, 2, 3],
        list(range(100, -1, -1)),
        list(range(-50, 51)),
    ]

    @pytest.mark.parametrize("case", CASES)
    def test_lsd_matches_sorted(self, case: list[int]) -> None:
        assert lsd_radix_sort(case) == sorted(case)

    @pytest.mark.parametrize("case", CASES)
    def test_msd_matches_sorted(self, case: list[int]) -> None:
        assert msd_radix_sort(case) == sorted(case)

    @pytest.mark.parametrize("case", CASES)
    def test_american_flag_matches_sorted(self, case: list[int]) -> None:
        assert american_flag_sort(case) == sorted(case)

    @pytest.mark.parametrize("case", CASES)
    def test_inplace_msd_matches_sorted(self, case: list[int]) -> None:
        assert inplace_msd_radix_sort(case) == sorted(case)


# ── Property tests ────────────────────────────────────────────────────


class TestIdempotency:
    def test_lsd_idempotent(self) -> None:
        arr = [42, 17, 88, 3, 55]
        once = lsd_radix_sort(arr)
        twice = lsd_radix_sort(once)
        assert once == twice

    def test_msd_idempotent(self) -> None:
        arr = [42, 17, 88, 3, 55]
        once = msd_radix_sort(arr)
        twice = msd_radix_sort(once)
        assert once == twice

    def test_american_flag_idempotent(self) -> None:
        arr = [42, 17, 88, 3, 55]
        once = american_flag_sort(arr)
        twice = american_flag_sort(once)
        assert once == twice

    def test_inplace_msd_idempotent(self) -> None:
        arr = [42, 17, 88, 3, 55]
        once = inplace_msd_radix_sort(arr)
        twice = inplace_msd_radix_sort(once)
        assert once == twice


class TestNonDestructive:
    """Verify sorts do not mutate the input list."""

    def test_lsd_nondestructive(self) -> None:
        arr = [3, 1, 2]
        orig = list(arr)
        lsd_radix_sort(arr)
        assert arr == orig

    def test_msd_nondestructive(self) -> None:
        arr = [3, 1, 2]
        orig = list(arr)
        msd_radix_sort(arr)
        assert arr == orig

    def test_american_flag_nondestructive(self) -> None:
        arr = [3, 1, 2]
        orig = list(arr)
        american_flag_sort(arr)
        assert arr == orig

    def test_inplace_msd_nondestructive(self) -> None:
        arr = [3, 1, 2]
        orig = list(arr)
        inplace_msd_radix_sort(arr)
        assert arr == orig

    def test_counting_nondestructive(self) -> None:
        arr = [3, 1, 2]
        orig = list(arr)
        counting_sort(arr)
        assert arr == orig

    def test_bucket_nondestructive(self) -> None:
        arr = [0.3, 0.1, 0.2]
        orig = list(arr)
        bucket_sort(arr)
        assert arr == orig
