from __future__ import annotations

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
    def test_empty(self) -> None:
        assert counting_sort([]) == []

    def test_single(self) -> None:
        assert counting_sort([5]) == [5]

    def test_sorted(self) -> None:
        assert counting_sort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]

    def test_reverse(self) -> None:
        assert counting_sort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]

    def test_with_negatives(self) -> None:
        assert counting_sort([-3, 0, 5, -1, 2]) == [-3, -1, 0, 2, 5]

    def test_duplicates(self) -> None:
        assert counting_sort([3, 1, 4, 1, 5, 9, 2, 6, 5]) == [1, 1, 2, 3, 4, 5, 5, 6, 9]

    def test_stable(self) -> None:
        # Stability not testable with ints, but verify output is correct
        arr = [2, 1, 2, 1]
        result = counting_sort(arr)
        assert result == sorted(arr)


class TestCountingSortForRadix:
    def test_ones_digit(self) -> None:
        arr = [321, 102, 43, 500]
        result = counting_sort_for_radix(arr, 1, 10)
        assert result == [500, 321, 102, 43]

    def test_tens_digit(self) -> None:
        arr = [321, 102, 43, 500]
        result = counting_sort_for_radix(arr, 10, 10)
        assert result == [102, 500, 321, 43]

    def test_equal_digits_retain_original_order(self) -> None:
        arr = [500, 102, 700, 304]

        assert counting_sort_for_radix(arr, 10, 10) == arr

    def test_base_2(self) -> None:
        arr = [5, 3, 8, 1]
        result = counting_sort_for_radix(arr, 1, 2)
        assert len(result) == 4
        assert set(result) == set(arr)


class TestLsdRadixSort:
    def test_empty(self) -> None:
        assert lsd_radix_sort([]) == []

    def test_single(self) -> None:
        assert lsd_radix_sort([42]) == [42]

    def test_small(self) -> None:
        arr = [170, 45, 75, 90, 802, 24, 2, 66]
        expected = sorted(arr)
        assert lsd_radix_sort(arr) == expected

    def test_with_negatives(self) -> None:
        arr = [-5, 10, -3, 0, 7]
        assert lsd_radix_sort(arr) == sorted(arr)

    def test_all_negative(self) -> None:
        arr = [-50, -10, -30, -20, -40]
        assert lsd_radix_sort(arr) == sorted(arr)

    def test_large_range(self) -> None:
        arr = [1000, 1, 500, 250, 999, 100]
        assert lsd_radix_sort(arr) == sorted(arr)


class TestMsdRadixSort:
    def test_empty(self) -> None:
        assert msd_radix_sort([]) == []

    def test_single(self) -> None:
        assert msd_radix_sort([7]) == [7]

    def test_basic(self) -> None:
        arr = [329, 457, 657, 839, 436, 720, 355]
        assert msd_radix_sort(arr) == sorted(arr)

    def test_with_negatives(self) -> None:
        arr = [-10, 5, -3, 0, 8, -1]
        assert msd_radix_sort(arr) == sorted(arr)

    def test_duplicates(self) -> None:
        arr = [5, 5, 3, 1, 3, 5]
        assert msd_radix_sort(arr) == sorted(arr)


class TestAmericanFlagSort:
    def test_empty(self) -> None:
        assert american_flag_sort([]) == []

    def test_single(self) -> None:
        assert american_flag_sort([99]) == [99]

    def test_basic(self) -> None:
        arr = [64, 34, 25, 12, 22, 11, 90]
        assert american_flag_sort(arr) == sorted(arr)

    def test_with_negatives(self) -> None:
        arr = [-5, -10, 0, 3, -2]
        assert american_flag_sort(arr) == sorted(arr)

    def test_already_sorted(self) -> None:
        arr = [1, 2, 3, 4, 5]
        assert american_flag_sort(arr) == [1, 2, 3, 4, 5]


class TestBucketSort:
    def test_empty(self) -> None:
        assert bucket_sort([]) == []

    def test_single(self) -> None:
        assert bucket_sort([0.5]) == [0.5]

    def test_basic(self) -> None:
        arr = [0.78, 0.17, 0.39, 0.26, 0.72, 0.94, 0.21]
        result = bucket_sort(arr)
        assert result == sorted(arr)

    def test_all_same(self) -> None:
        arr = [0.5, 0.5, 0.5]
        assert bucket_sort(arr) == [0.5, 0.5, 0.5]

    def test_mixed(self) -> None:
        arr = [0.9, 0.1, 0.5, 0.3, 0.7]
        assert bucket_sort(arr) == sorted(arr)


class TestInplaceMsdRadixSort:
    def test_empty(self) -> None:
        assert inplace_msd_radix_sort([]) == []

    def test_single(self) -> None:
        assert inplace_msd_radix_sort([1]) == [1]

    def test_basic(self) -> None:
        arr = [42, 17, 23, 89, 5, 56]
        assert inplace_msd_radix_sort(arr) == sorted(arr)

    def test_with_negatives(self) -> None:
        arr = [-10, 5, 0, -3, 8]
        assert inplace_msd_radix_sort(arr) == sorted(arr)

    def test_all_negative(self) -> None:
        arr = [-30, -10, -50, -20]
        assert inplace_msd_radix_sort(arr) == sorted(arr)
