"""Pure stdlib algorithm acceptance for the content-addressed unit pilot."""

from __future__ import annotations

import os
import unittest

if os.environ.get("GLUDD_PANTS_UNIT_POLICY") != "v1":
    raise unittest.SkipTest("executed only by the explicitly admitted Pants unit lane")

from hash_set import HashSet
from radix_sort import (
    american_flag_sort,
    bucket_sort,
    counting_sort,
    counting_sort_for_radix,
    inplace_msd_radix_sort,
    lsd_radix_sort,
    msd_radix_sort,
)


def test_integer_sorters_cover_empty_single_signed_and_recursive_paths() -> None:
    values = [170, -45, 75, 0, -802, 24, 2, 66]
    sorters = (
        counting_sort,
        lsd_radix_sort,
        msd_radix_sort,
        american_flag_sort,
        inplace_msd_radix_sort,
    )
    for sorter in sorters:
        assert sorter([]) == []
        assert sorter([7]) == [7]
        assert sorter(values) == sorted(values)
        assert sorter([-8, -2, -30]) == [-30, -8, -2]
        assert sorter([999, 1, 99, 9, 0]) == [0, 1, 9, 99, 999]


def test_counting_sort_digit_passes_are_stable_across_bases() -> None:
    values = [170, 90, 802, 2, 24, 45, 75, 66]
    assert counting_sort_for_radix(values, 1) == [170, 90, 802, 2, 24, 45, 75, 66]
    binary = counting_sort_for_radix([5, 2, 3, 4], 1, base=2)
    assert binary == [2, 4, 5, 3]


def test_bucket_sort_covers_degenerate_signed_and_populated_buckets() -> None:
    floats = [0.42, -0.32, 0.33, 0.52, 0.37]
    assert bucket_sort([]) == []
    assert bucket_sort([0.5]) == [0.5]
    assert bucket_sort([0.5, 0.5, 0.5]) == [0.5, 0.5, 0.5]
    assert bucket_sort(floats) == sorted(floats)


def test_hash_set_crud_resize_tombstones_and_repr() -> None:
    values = HashSet[int]()
    assert len(values) == 0
    assert values.size == 0
    assert values.capacity == 8
    for number in range(100):
        values.add(number)
        values.add(number)
    assert len(values) == 100
    assert all(number in values for number in range(100))
    for number in range(60):
        values.remove(number)
    assert all(number not in values for number in range(60))
    values.discard(999)
    try:
        values.remove(999)
    except KeyError:
        pass
    else:
        raise AssertionError("removing an absent HashSet value must fail")
    assert "HashSet" in repr(values)
    values.clear()
    assert list(values) == []


def test_hash_set_operations_and_comparisons() -> None:
    left = HashSet([1, 2, 3])
    right = HashSet([3, 4, 5])
    subset = HashSet([1, 2])
    assert sorted(left | right) == [1, 2, 3, 4, 5]
    assert sorted(left & right) == [3]
    assert sorted(left - right) == [1, 2]
    assert sorted(left.symmetric_difference(right)) == [1, 2, 4, 5]
    assert subset < left
    assert subset <= left
    assert left > subset
    assert left >= subset
    assert left.issuperset(subset)
    assert left.isdisjoint(HashSet([8, 9]))
    assert not left.isdisjoint(right)
    assert left != right
    assert left != [1, 2, 3]


def test_hash_set_mutating_operations_and_copy_are_independent() -> None:
    values = HashSet([1, 2, 3])
    values.update(HashSet([3, 4]))
    assert sorted(values) == [1, 2, 3, 4]
    values.intersection_update(HashSet([2, 3, 9]))
    assert sorted(values) == [2, 3]
    values.difference_update(HashSet([3, 7]))
    assert list(values) == [2]
    copied = values.copy()
    copied.add(8)
    assert copied != values
    assert list(values) == [2]
