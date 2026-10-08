"""Unit tests for radix sort variants."""

import pytest

from general_ludd.algorithms.radix_sort import (
    american_flag_sort,
    bucket_sort,
    counting_sort,
    counting_sort_for_radix,
    inplace_msd_radix_sort,
    lsd_radix_sort,
    msd_radix_sort,
)


class TestCountingSort:
    def test_empty(self):
        assert counting_sort([]) == []

    def test_basic(self):
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        assert counting_sort(arr) == sorted(arr)

    def test_negative(self):
        arr = [-3, 1, -5, 0, 2]
        assert counting_sort(arr) == sorted(arr)

    def test_single_element(self):
        assert counting_sort([42]) == [42]


class TestCountingSortForRadix:
    def test_basic(self):
        arr = [170, 45, 75, 90, 2]
        result = counting_sort_for_radix(arr, 1)
        # Stable single-digit (exp=1) sort ordered by the ones place.
        assert result == [170, 90, 2, 45, 75]


class TestLsdRadixSort:
    def test_empty(self):
        assert lsd_radix_sort([]) == []

    def test_mixed(self):
        arr = [170, 45, 75, 90, 2, 802, 24, 66]
        assert lsd_radix_sort(arr) == sorted(arr)

    def test_negative(self):
        arr = [-10, 5, -3, 0, 1]
        assert lsd_radix_sort(arr) == sorted(arr)


class TestMsdRadixSort:
    def test_empty(self):
        assert msd_radix_sort([]) == []

    def test_mixed(self):
        arr = [170, 45, 75, 90, 2, 802, 24, 66]
        assert msd_radix_sort(arr) == sorted(arr)

    def test_negative(self):
        arr = [-10, 5, -3, 0, 1]
        assert msd_radix_sort(arr) == sorted(arr)


class TestAmericanFlagSort:
    def test_empty(self):
        assert american_flag_sort([]) == []

    def test_mixed(self):
        arr = [170, 45, 75, 90, 2, 802, 24, 66]
        assert american_flag_sort(arr) == sorted(arr)

    def test_negative(self):
        arr = [-10, 5, -3, 0, 1]
        assert american_flag_sort(arr) == sorted(arr)


class TestBucketSort:
    def test_empty(self):
        assert bucket_sort([]) == []

    def test_basic(self):
        arr = [0.42, 0.32, 0.33, 0.52, 0.37, 0.47, 0.51]
        assert bucket_sort(arr) == pytest.approx(sorted(arr))

    def test_uniform(self):
        assert bucket_sort([0.5, 0.5, 0.5]) == pytest.approx([0.5, 0.5, 0.5])


class TestInplaceMsdRadixSort:
    def test_empty(self):
        assert inplace_msd_radix_sort([]) == []

    def test_mixed(self):
        arr = [170, 45, 75, 90, 2, 802, 24, 66]
        assert inplace_msd_radix_sort(arr) == sorted(arr)

    def test_negative(self):
        arr = [-10, 5, -3, 0, 1]
        assert inplace_msd_radix_sort(arr) == sorted(arr)
