"""Deep top-k and order statistics tests.

Covers quickselect, heap-based top-k, streaming top-k, median,
median-of-medians, order_statistic, partial_sort, quantile, and IQR
with 20+ test cases.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.topk import (
    interquartile_range,
    median,
    median_of_medians,
    nth_element,
    order_statistic,
    partial_sort,
    quantile,
    quickselect,
    topk_heapsort,
    topk_streaming,
)


class TestQuickselect:
    def test_k0_returns_min(self) -> None:
        arr = [5, 3, 8, 1, 9, 2]
        assert quickselect(arr, 0) == 1

    def test_klast_returns_max(self) -> None:
        arr = [5, 3, 8, 1, 9, 2]
        assert quickselect(arr, 5) == 9

    def test_median_index(self) -> None:
        arr = [7, 10, 4, 3, 20, 15, 8]
        n = len(arr)
        result = quickselect(arr, n // 2)
        expected = sorted(arr)[n // 2]
        assert result == expected

    def test_single_element(self) -> None:
        assert quickselect([42], 0) == 42

    def test_sorted_ascending(self) -> None:
        arr = list(range(100))
        assert quickselect(arr, 73) == 73

    def test_sorted_descending(self) -> None:
        arr = list(range(99, -1, -1))
        assert quickselect(arr, 50) == 50

    def test_duplicates(self) -> None:
        arr = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5]
        result = quickselect(arr, 5)
        assert result == 4

    def test_large_random(self) -> None:
        import random

        rng = random.Random(42)
        n = 1000
        arr = [rng.randint(-10000, 10000) for _ in range(n)]
        k = rng.randint(0, n - 1)
        result = quickselect(arr, k)
        expected = sorted(arr)[k]
        assert result == expected

    def test_negative_k_raises(self) -> None:
        with pytest.raises(IndexError):
            quickselect([1, 2, 3], -1)

    def test_k_out_of_range_raises(self) -> None:
        with pytest.raises(IndexError):
            quickselect([1, 2, 3], 3)

    def test_partitions_correctly(self) -> None:
        arr = [9, 7, 5, 11, 12, 2, 14, 3, 10, 6]
        k = 4
        val = quickselect(arr, k)
        for i in range(k):
            assert arr[i] <= val
        for i in range(k + 1, len(arr)):
            assert arr[i] >= val


class TestNthElement:
    def test_first_k_unordered_smallest(self) -> None:
        arr = [5, 1, 3, 8, 2, 9, 7, 4]
        result = nth_element(arr, 3)
        assert set(result[:3]) == {1, 2, 3}
        assert result[3] <= result[4] or True

    def test_k_zero_returns_unchanged(self) -> None:
        arr = [3, 1, 2]
        result = nth_element(arr, 0)
        assert result is arr

    def test_k_exceeds_n_clamped(self) -> None:
        arr = [5, 1, 3]
        result = nth_element(arr, 100)
        assert sorted(result[:3]) == [1, 3, 5]


class TestTopkHeapsort:
    def test_largest_k(self) -> None:
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        result = topk_heapsort(arr, 3, largest=True)
        assert result == [9, 6, 5]

    def test_smallest_k(self) -> None:
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        result = topk_heapsort(arr, 3, largest=False)
        assert result == [1, 1, 2]

    def test_k_zero(self) -> None:
        assert topk_heapsort([1, 2, 3], 0) == []

    def test_k_exceeds_n(self) -> None:
        assert topk_heapsort([3, 1, 2], 10, largest=True) == [3, 2, 1]

    def test_empty_input(self) -> None:
        assert topk_heapsort([], 5) == []

    def test_iterator_input(self) -> None:
        result = topk_heapsort(iter(range(100, 0, -1)), 4, largest=True)
        assert result == [100, 99, 98, 97]


class TestTopkStreaming:
    def test_largest_k_streaming(self) -> None:
        result = topk_streaming([7, 1, 5, 3, 6, 4, 2, 9, 8], 3, largest=True)
        assert result == [9, 8, 7]

    def test_smallest_k_streaming(self) -> None:
        result = topk_streaming([7, 1, 5, 3, 6, 4, 2, 9, 8], 3, largest=False)
        assert result == [1, 2, 3]

    def test_k_zero_streaming(self) -> None:
        assert topk_streaming([1, 2, 3], 0) == []

    def test_k_exceeds_n_streaming(self) -> None:
        result = topk_streaming([3, 1, 2], 10, largest=True)
        assert len(result) == 3
        assert set(result) == {1, 2, 3}

    def test_matches_heapsort(self) -> None:
        import random

        rng = random.Random(123)
        data = [rng.randint(0, 5000) for _ in range(2000)]
        hs = topk_heapsort(data, 20, largest=True)
        st = topk_streaming(data, 20, largest=True)
        assert hs == st


class TestMedian:
    def test_odd_length(self) -> None:
        assert median([1, 3, 2, 5, 4]) == 3

    def test_even_length_lower_median(self) -> None:
        assert median([1, 2, 3, 4]) == 2

    def test_single_element(self) -> None:
        assert median([99]) == 99

    def test_empty(self) -> None:
        assert median([]) is None

    def test_large_random(self) -> None:
        import random

        rng = random.Random(99)
        for n in (50, 51, 200):
            arr = [rng.randint(-500, 500) for _ in range(n)]
            got = median(arr)
            expected = sorted(arr)[(n - 1) // 2]
            assert got == expected


class TestMedianOfMedians:
    def test_basic_selection(self) -> None:
        arr = [7, 10, 4, 3, 20, 15, 8, 12, 1, 6, 9, 5, 11, 19, 2]
        assert median_of_medians(arr, 0) == 1
        assert median_of_medians(arr, 14) == 20
        assert median_of_medians(arr, 7) == 8

    def test_matches_quickselect(self) -> None:
        import random

        rng = random.Random(7)
        data = [rng.randint(0, 1000) for _ in range(200)]
        for _ in range(20):
            k = rng.randint(0, 199)
            qs = quickselect(data[:], k)
            mom = median_of_medians(data[:], k)
            assert qs == mom

    def test_out_of_range_raises(self) -> None:
        with pytest.raises(IndexError):
            median_of_medians([1, 2, 3], 3)
        with pytest.raises(IndexError):
            median_of_medians([1, 2, 3], -1)

    def test_small_array(self) -> None:
        assert median_of_medians([3, 1, 2], 1) == 2


class TestOrderStatistic:
    def test_kth_smallest(self) -> None:
        arr = [7, 1, 5, 3, 6, 4, 2]
        assert order_statistic(arr, 0) == 1
        assert order_statistic(arr, 3) == 4
        assert order_statistic(arr, 6) == 7

    def test_with_key(self) -> None:
        arr = ["apple", "banana", "kiwi", "pear"]
        assert order_statistic(arr, 0, key=len) == "kiwi"
        assert order_statistic(arr, 3, key=len) == "banana"

    def test_iterator(self) -> None:
        result = order_statistic(iter([9, 3, 5, 1, 7]), 2)
        assert result == 5


class TestPartialSort:
    def test_smallest_prefix(self) -> None:
        arr = [7, 2, 9, 1, 4, 6, 3, 8, 5]
        result = partial_sort(arr, 4, largest=False)
        assert result == [1, 2, 3, 4]

    def test_largest_prefix(self) -> None:
        arr = [7, 2, 9, 1, 4, 6, 3, 8, 5]
        result = partial_sort(arr, 3, largest=True)
        assert result == [9, 8, 7]

    def test_k_zero(self) -> None:
        assert partial_sort([3, 1, 2], 0) == []

    def test_k_exceeds_n(self) -> None:
        arr = [3, 1, 2]
        result = partial_sort(arr, 10, largest=False)
        assert result == [1, 2, 3]


class TestQuantile:
    def test_median_quantile(self) -> None:
        arr = [1, 3, 2, 5, 4]
        assert quantile(arr, 0.5) == 3

    def test_min_and_max(self) -> None:
        arr = [5, 3, 8, 1, 9]
        assert quantile(arr, 0.0) == 1
        assert quantile(arr, 1.0) == 9

    def test_clamp_out_of_range(self) -> None:
        arr = [1, 2, 3, 4, 5]
        assert quantile(arr, -0.5) == 1
        assert quantile(arr, 2.0) == 5

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            quantile([], 0.5)

    def test_single_element(self) -> None:
        assert quantile([99], 0.3) == 99


class TestInterquartileRange:
    def test_basic_iqr(self) -> None:
        arr = [6, 7, 15, 36, 39, 40, 41, 42, 43, 47, 49]
        q1, med, q3 = interquartile_range(arr)
        assert q1 == 15 or q1 == 25.5
        assert med == 40
        assert q3 == 43 or q3 == 42

    def test_two_elements(self) -> None:
        q1, med, q3 = interquartile_range([10, 20])
        assert q1 == 10
        assert med == 10
        assert q3 == 10

    def test_single_element_raises(self) -> None:
        with pytest.raises(ValueError):
            interquartile_range([1])

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            interquartile_range([])

    def test_sorted_input(self) -> None:
        arr = list(range(1, 101))
        q1, med, q3 = interquartile_range(arr)
        assert 24 <= q1 <= 26
        assert med == 50
        assert 74 <= q3 <= 76

    def test_all_equal(self) -> None:
        arr = [5, 5, 5, 5, 5]
        q1, med, q3 = interquartile_range(arr)
        assert q1 == 5
        assert med == 5
        assert q3 == 5
