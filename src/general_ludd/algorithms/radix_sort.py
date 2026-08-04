"""Radix sort implementations: LSD, MSD, American flag, counting sort,
bucket sort, in-place MSD. Pure-Python, stdlib only.
"""

from __future__ import annotations


def counting_sort(arr: list[int]) -> list[int]:
    """Stable counting sort for any integers."""
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


def american_flag_sort(arr: list[int], base: int = 10) -> list[int]:
    """American flag sort (MSD radix with counting-sort partitioning)."""
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


def bucket_sort(arr: list[float]) -> list[float]:
    """Bucket sort for floating-point values."""
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


def inplace_msd_radix_sort(arr: list[int], base: int = 10) -> list[int]:
    """In-place MSD radix sort handling negative integers."""
    if len(arr) <= 1:
        return list(arr)
    neg = [-v for v in arr if v < 0]
    pos = [v for v in arr if v >= 0]
    if neg:
        _inplace_msd_unsigned(neg, 0, len(neg), base)
        for i in range(len(neg)):
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
