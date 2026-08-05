"""Quickselect, heap-based top-k, median, order-statistic selection.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar


class Comparable(Protocol):
    def __lt__(self, other: object, /) -> bool: ...
    def __gt__(self, other: object, /) -> bool: ...
    def __le__(self, other: object, /) -> bool: ...


T = TypeVar("T", bound=Comparable)


def quickselect(arr: list[T], k: int) -> T:
    """Hoare's quickselect — k-th smallest (0-indexed) in expected O(n).

    Mutates *arr* in-place.  Raises IndexError on out-of-range *k*.
    """
    n = len(arr)
    if k < 0 or k >= n:
        raise IndexError(f"k={k} out of range [0, {n})")

    lo, hi = 0, n - 1
    while lo < hi:
        pivot = _partition(arr, lo, hi)
        if k < pivot:
            hi = pivot - 1
        elif k > pivot:
            lo = pivot + 1
        else:
            return arr[k]
    return arr[lo]


def _partition(arr: list[T], lo: int, hi: int) -> int:
    """Lomuto partition with median-of-three pivot."""
    mid = lo + (hi - lo) // 2
    a, b, c = arr[lo], arr[mid], arr[hi]
    if (a <= b <= c) or (c <= b <= a):
        pivot_val: T = arr[mid]
        arr[mid], arr[hi] = arr[hi], arr[mid]
    elif (b <= a <= c) or (c <= a <= b):
        pivot_val = arr[lo]
        arr[lo], arr[hi] = arr[hi], arr[lo]
    else:
        pivot_val = arr[hi]

    i = lo
    for j in range(lo, hi):
        if arr[j] < pivot_val:
            arr[i], arr[j] = arr[j], arr[i]
            i += 1
    arr[i], arr[hi] = arr[hi], arr[i]
    return i


def nth_element(arr: list[T], k: int) -> list[T]:
    """Re-arrange *arr* so arr[0:k] are the k smallest (unordered),
    then the k-th element, then the rest (unordered).

    Returns *arr* for convenience (mutated in-place).
    """
    if k <= 0:
        return arr
    n = len(arr)
    k = min(k, n)
    quickselect(arr, k - 1)
    return arr


def topk_heapsort(arr: Iterable[T], k: int, *, largest: bool = True) -> list[T]:
    """Heap-based top-k — returns the k largest (or smallest) elements.

    O(n log k) time, O(k) memory.  When *largest* is False, returns
    the k smallest.  When k >= len(arr), all elements are returned.
    """
    data = list(arr)
    n = len(data)
    if k <= 0:
        return []
    k = min(k, n)
    if largest:
        return heapq.nlargest(k, data)
    return heapq.nsmallest(k, data)


def topk_streaming(
    stream: Iterable[T],
    k: int,
    *,
    largest: bool = True,
) -> list[T]:
    """Streaming top-k via bounded min-heap (or max-heap).  Processes
    the iterable one element at a time without materializing it.

    O(n log k) time, O(k) memory.
    """
    if k <= 0:
        return []

    if largest:
        heap: list[T] = []
        for item in stream:
            if len(heap) < k:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
        heap.sort(reverse=True)
        return heap
    else:
        heap: list[T] = []
        for item in stream:
            if len(heap) < k:
                heapq.heappush(heap, item)
            elif item < max(heap):
                heap[heap.index(max(heap))] = item
                heapq.heapify(heap)
        return sorted(heap)


def median(arr: list[T]) -> T | None:
    """Median: lower median for even-length lists (O(n) expected).

    Returns None for empty input; otherwise the element at floor((n-1)/2)
    after find-nth.
    """
    n = len(arr)
    if n == 0:
        return None
    arr = list(arr)
    mid = (n - 1) // 2
    return quickselect(arr, mid)


def median_of_medians(arr: list[T], k: int) -> T:
    """Worst-case O(n) selection using Blum-Floyd-Pratt-Rivest-Tarjan
    median-of-medians pivot.  Mutates *arr* (works on a copy).

    Raises IndexError on out-of-range *k*.
    """
    n = len(arr)
    if k < 0 or k >= n:
        raise IndexError(f"k={k} out of range [0, {n})")
    arr = list(arr)
    return _mom_select(arr, 0, n - 1, k)


def _mom_select(arr: list[T], lo: int, hi: int, k: int) -> T:
    while lo < hi:
        pivot = _mom_partition(arr, lo, hi)
        if k < pivot:
            hi = pivot - 1
        elif k > pivot:
            lo = pivot + 1
        else:
            return arr[k]
    return arr[lo]


def _mom_partition(arr: list[T], lo: int, hi: int) -> int:
    n = hi - lo + 1
    if n <= 5:
        arr[lo : hi + 1] = sorted(arr[lo : hi + 1])
        return lo + n // 2

    groups = [(lo + i * 5, min(lo + i * 5 + 4, hi)) for i in range((n + 4) // 5)]
    medians: list[T] = []
    for start, end in groups:
        chunk: list[T] = sorted(arr[start : end + 1])
        medians.append(chunk[len(chunk) // 2])
    pivot_val = _mom_select(medians, 0, len(medians) - 1, len(medians) // 2)

    p_idx = None
    for i in range(lo, hi + 1):
        if arr[i] == pivot_val:
            p_idx = i
            break
    assert p_idx is not None
    arr[p_idx], arr[hi] = arr[hi], arr[p_idx]

    i = lo
    for j in range(lo, hi):
        if arr[j] < pivot_val:
            arr[i], arr[j] = arr[j], arr[i]
            i += 1
    arr[i], arr[hi] = arr[hi], arr[i]
    return i


def order_statistic(arr: Iterable[T], k: int, *, key: Callable[[T], Comparable] | None = None) -> T:
    """k-th smallest element (0-indexed) without mutating input.

    When *key* is provided, ordering is by key(x).  Raises IndexError
    for out-of-range *k*.
    """
    data = list(arr)
    if key is not None:
        data.sort(key=key)
    else:
        data.sort()
    return data[k]


def partial_sort(arr: list[T], k: int, *, largest: bool = False) -> list[T]:
    """Partially sort arr — first k smallest (or largest) elements in order.

    Returns the sorted prefix (length k).  Mutates *arr* in-place.
    """
    n = len(arr)
    if k <= 0:
        return []
    k = min(k, n)

    if largest:
        quickselect(arr, n - k)
        result: list[T] = sorted(arr[n - k :], reverse=True)
        return result
    else:
        quickselect(arr, k - 1)
        result = sorted(arr[:k])
        return result


def quantile(arr: list[T], q: float) -> T:
    """Return the q-quantile of *arr*.  q=0.5 gives the median, q=0 gives
    the min, q=1 gives the max.  Mutates *arr* in-place.
    """
    n = len(arr)
    if n == 0:
        raise ValueError("quantile of empty sequence")
    q = max(0.0, min(1.0, q))
    k = int(q * (n - 1))
    k = min(k, n - 1)
    return quickselect(arr, k)


def interquartile_range(arr: list[T]) -> tuple[T, T, T]:
    """Return (Q1, median, Q3) — 25th, 50th, 75th percentiles.

    Mutates *arr* in-place.  Raises ValueError for fewer than 2 elements.
    """
    n = len(arr)
    if n < 2:
        raise ValueError("IQR requires at least 2 elements")
    q1 = quantile(arr, 0.25)
    med = quantile(arr, 0.50)
    q3 = quantile(arr, 0.75)
    return q1, med, q3
