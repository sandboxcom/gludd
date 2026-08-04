"""Deep sorting algorithm tests: merge sort, quick sort, heap sort,
stable sort, partial sort, external sort, timsort properties. 18 tests.
"""

from __future__ import annotations

import heapq
import math
import random
import tempfile

import pytest

# ── Algorithm Implementations ──────────────────────────────────────────────


def merge_sort(arr: list[int]) -> list[int]:
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return _merge(left, right)


def _merge(left: list[int], right: list[int]) -> list[int]:
    result: list[int] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result


def quick_sort(arr: list[int]) -> list[int]:
    if len(arr) <= 1:
        return arr
    pivot = arr[0]
    less = [x for x in arr[1:] if x <= pivot]
    greater = [x for x in arr[1:] if x > pivot]
    return [*quick_sort(less), pivot, *quick_sort(greater)]


def quick_sort_random_pivot(arr: list[int]) -> list[int]:
    if len(arr) <= 1:
        return arr
    pivot_idx = random.randint(0, len(arr) - 1)
    pivot = arr[pivot_idx]
    less = [x for x in arr if x < pivot]
    equal = [x for x in arr if x == pivot]
    greater = [x for x in arr if x > pivot]
    return quick_sort_random_pivot(less) + equal + quick_sort_random_pivot(greater)


def heap_sort(arr: list[int]) -> list[int]:
    result: list[int] = []
    heap = list(arr)
    heapq.heapify(heap)
    while heap:
        result.append(heapq.heappop(heap))
    return result


def partial_sort(arr: list[int], k: int) -> list[int]:
    heap = list(arr)
    heapq.heapify(heap)
    return [heapq.heappop(heap) for _ in range(min(k, len(arr)))]


def stable_merge_sort_pairs(arr: list[tuple[int, str]]) -> list[tuple[int, str]]:
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = stable_merge_sort_pairs(arr[:mid])
    right = stable_merge_sort_pairs(arr[mid:])
    return _merge_pairs(left, right)


def _merge_pairs(left: list[tuple[int, str]], right: list[tuple[int, str]]) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i][0] <= right[j][0]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result


def external_sort_2way(data: list[int], chunk_size: int) -> list[int]:
    chunks: list[list[int]] = []
    for i in range(0, len(data), chunk_size):
        chunk = sorted(data[i : i + chunk_size])
        chunks.append(chunk)
    while len(chunks) > 1:
        merged: list[list[int]] = []
        for i in range(0, len(chunks), 2):
            if i + 1 < len(chunks):
                merged.append(list(heapq.merge(chunks[i], chunks[i + 1])))
            else:
                merged.append(chunks[i])
        chunks = merged
    return chunks[0] if chunks else []


def insertion_sort(arr: list[int]) -> list[int]:
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def tim_sort_like(arr: list[int], min_run: int = 64) -> list[int]:
    if len(arr) <= min_run:
        return insertion_sort(arr)
    mid = len(arr) // 2
    left = tim_sort_like(arr[:mid], min_run)
    right = tim_sort_like(arr[mid:], min_run)
    return _merge(left, right)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def rng() -> random.Random:
    return random.Random(42)


@pytest.fixture
def sorted_asc() -> list[int]:
    return list(range(200))


@pytest.fixture
def sorted_desc() -> list[int]:
    return list(range(199, -1, -1))


@pytest.fixture
def random_1k(rng: random.Random) -> list[int]:
    return [rng.randint(-1000, 1000) for _ in range(1000)]


@pytest.fixture
def random_10k(rng: random.Random) -> list[int]:
    return [rng.randint(-10000, 10000) for _ in range(10000)]


# ── Merge Sort ─────────────────────────────────────────────────────────────


class TestMergeSort:
    def test_empty(self) -> None:
        assert merge_sort([]) == []

    def test_single_element(self) -> None:
        assert merge_sort([5]) == [5]

    def test_sorted_ascending(self, sorted_asc: list[int]) -> None:
        assert merge_sort(sorted_asc) == sorted_asc

    def test_sorted_descending(self, sorted_desc: list[int]) -> None:
        assert merge_sort(sorted_desc) == list(reversed(sorted_desc))

    def test_random_large(self, random_10k: list[int]) -> None:
        result = merge_sort(random_10k)
        assert result == sorted(random_10k)
        assert len(result) == len(random_10k)

    def test_duplicates(self) -> None:
        arr = [3, 1, 3, 2, 3, 1, 2, 1]
        assert merge_sort(arr) == sorted(arr)

    def test_all_same(self) -> None:
        arr = [7] * 100
        assert merge_sort(arr) == [7] * 100


# ── Quick Sort ─────────────────────────────────────────────────────────────


class TestQuickSort:
    def test_empty(self) -> None:
        assert quick_sort([]) == []

    def test_single(self) -> None:
        assert quick_sort([42]) == [42]

    def test_basic_partition(self) -> None:
        arr = [4, 1, 7, 3, 8, 2, 5, 6]
        assert quick_sort(arr) == sorted(arr)

    def test_random_1k(self, random_1k: list[int]) -> None:
        assert quick_sort(random_1k) == sorted(random_1k)

    def test_worst_case_already_sorted(self, sorted_asc: list[int]) -> None:
        result = quick_sort(sorted_asc)
        assert result == sorted_asc

    def test_random_pivot_handles_duplicates(self, rng: random.Random) -> None:
        arr = [rng.randint(0, 5) for _ in range(200)]
        assert quick_sort_random_pivot(arr) == sorted(arr)


# ── Heap Sort ──────────────────────────────────────────────────────────────


class TestHeapSort:
    def test_empty(self) -> None:
        assert heap_sort([]) == []

    def test_basic(self) -> None:
        arr = [9, 3, 1, 4, 7]
        assert heap_sort(arr) == [1, 3, 4, 7, 9]

    def test_random_large(self, random_10k: list[int]) -> None:
        assert heap_sort(random_10k) == sorted(random_10k)

    def test_heap_property_holds(self, rng: random.Random) -> None:
        arr = [rng.randint(-500, 500) for _ in range(500)]
        heap = list(arr)
        heapq.heapify(heap)
        for i in range(len(heap)):
            left = 2 * i + 1
            right = 2 * i + 2
            if left < len(heap):
                assert heap[i] <= heap[left], f"heap violation at {i}->{left}"
            if right < len(heap):
                assert heap[i] <= heap[right], f"heap violation at {i}->{right}"


# ── Stable Sort ────────────────────────────────────────────────────────────


class TestStableSort:
    def test_stable_merge_sort_pairs(self) -> None:
        pairs: list[tuple[int, str]] = [
            (5, "a"),
            (3, "b"),
            (5, "c"),
            (2, "d"),
            (3, "e"),
            (5, "f"),
        ]
        result = stable_merge_sort_pairs(pairs)
        # Same keys must retain original relative order
        fives = [s for k, s in result if k == 5]
        assert fives == ["a", "c", "f"]
        threes = [s for k, s in result if k == 3]
        assert threes == ["b", "e"]

    def test_builtin_stable(self) -> None:
        pairs: list[tuple[int, str]] = [
            (0, "first"),
            (0, "second"),
            (0, "third"),
        ]
        result = sorted(pairs, key=lambda x: x[0])
        assert result == pairs  # already in order, stability preserves it

    def test_many_duplicate_keys_large(self, rng: random.Random) -> None:
        pairs = [(rng.randint(0, 20), chr(65 + i % 26)) for i in range(2000)]
        result = stable_merge_sort_pairs(pairs)
        expected = sorted(pairs, key=lambda x: x[0])
        for i, (key_r, _) in enumerate(result):
            assert key_r == expected[i][0]


# ── Partial Sort ───────────────────────────────────────────────────────────


class TestPartialSort:
    def test_top_k(self) -> None:
        arr = [7, 1, 9, 3, 5, 2, 8]
        top3 = partial_sort(arr, 3)
        assert top3 == [1, 2, 3]

    def test_k_larger_than_len(self) -> None:
        arr = [4, 1, 2]
        assert partial_sort(arr, 10) == sorted(arr)

    def test_k_zero(self) -> None:
        assert partial_sort([5, 3, 8], 0) == []

    def test_random_top_k(self, random_1k: list[int], rng: random.Random) -> None:
        k = rng.randint(1, 50)
        top = partial_sort(random_1k, k)
        assert len(top) == k
        assert top == sorted(random_1k)[:k]


# ── External Sort ──────────────────────────────────────────────────────────


class TestExternalSort:
    def test_small_chunks(self) -> None:
        data = [9, 2, 7, 1, 5, 3, 8, 4, 6, 0]
        assert external_sort_2way(data, 3) == sorted(data)

    def test_single_chunk(self) -> None:
        data = [3, 1, 2]
        assert external_sort_2way(data, 10) == sorted(data)

    def test_large_dataset(self) -> None:
        data = list(range(999, -1, -1))
        assert external_sort_2way(data, 50) == sorted(data)

    def test_duplicates_across_chunks(self) -> None:
        data = [5] * 50 + [1] * 50 + [9] * 50
        assert external_sort_2way(data, 20) == sorted(data)

    def test_disk_backed_chunk_write(self) -> None:
        data = [rng.randint(0, 1000) for rng in [random.Random(99)]][:0] or list(range(100))
        data = list(range(1000, -1, -1))
        chunk_files: list[str] = []
        chunk_size = 250
        for i in range(0, len(data), chunk_size):
            chunk = sorted(data[i : i + chunk_size])
            with tempfile.NamedTemporaryFile(mode="w", suffix=".chunk", delete=False) as f:
                f.write("\n".join(str(v) for v in chunk))
                chunk_files.append(f.name)
        assert len(chunk_files) == math.ceil(len(data) / chunk_size)
        import os

        for path in chunk_files:
            os.unlink(path)


# ── Timsort Properties ─────────────────────────────────────────────────────


class TestTimsortProperties:
    def test_timsort_like_basic(self) -> None:
        arr = [7, 3, 9, 1, 5, 2, 8, 4, 6]
        assert tim_sort_like(arr, min_run=3) == sorted(arr)

    def test_timsort_like_already_sorted(self, sorted_asc: list[int]) -> None:
        assert tim_sort_like(sorted_asc) == sorted_asc

    def test_timsort_like_large_random(self, random_10k: list[int]) -> None:
        assert tim_sort_like(random_10k, min_run=80) == sorted(random_10k)

    def test_adaptive_behavior(self) -> None:
        nearly_sorted = list(range(500))
        for i in range(0, 500, 50):
            if i + 1 < 500:
                nearly_sorted[i], nearly_sorted[i + 1] = nearly_sorted[i + 1], nearly_sorted[i]
        assert tim_sort_like(nearly_sorted) == list(range(500))


# ── Cross-Algorithm Consistency ────────────────────────────────────────────


class TestCrossAlgorithmConsistency:
    def test_all_algorithms_agree(self, random_10k: list[int]) -> None:
        expected = sorted(random_10k)
        assert merge_sort(random_10k) == expected
        assert quick_sort_random_pivot(random_10k) == expected
        assert heap_sort(random_10k) == expected
        assert tim_sort_like(random_10k) == expected
        assert external_sort_2way(random_10k, 200) == expected

    def test_empty_consistency(self) -> None:
        assert merge_sort([]) == quick_sort([]) == heap_sort([]) == tim_sort_like([]) == []

    def test_single_consistency(self) -> None:
        assert merge_sort([1]) == quick_sort([1]) == heap_sort([1]) == tim_sort_like([1]) == [1]
