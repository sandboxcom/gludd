"""Tests for src/general_ludd/algorithms/randomized.py"""

from __future__ import annotations

import random

import pytest

from general_ludd.algorithms.randomized import (
    bogosort_check,
    fisher_yates_shuffle,
    random_permutation,
    random_subset,
)


class TestFisherYatesShuffle:
    def test_shuffle_is_permutation(self):
        items = [1, 2, 3, 4, 5]
        result = fisher_yates_shuffle(items)
        assert sorted(result) == sorted(items)

    def test_shuffle_preserves_length(self):
        items = [10, 20, 30]
        result = fisher_yates_shuffle(items)
        assert len(result) == len(items)

    def test_shuffle_does_not_mutate_input(self):
        items = [1, 2, 3]
        original = list(items)
        fisher_yates_shuffle(items)
        assert items == original

    def test_empty_list(self):
        assert fisher_yates_shuffle([]) == []

    def test_single_element(self):
        assert fisher_yates_shuffle([42]) == [42]

    def test_deterministic_with_seeded_rng(self):
        items = list(range(10))
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        assert fisher_yates_shuffle(items, rng=rng1) == fisher_yates_shuffle(items, rng=rng2)

    def test_different_seeds_produce_different(self):
        items = list(range(20))
        rng1 = random.Random(1)
        rng2 = random.Random(9999)
        result1 = fisher_yates_shuffle(items, rng=rng1)
        result2 = fisher_yates_shuffle(items, rng=rng2)
        assert result1 != result2


class TestRandomPermutation:
    def test_permutation_is_range_shuffled(self):
        result = random_permutation(5)
        assert sorted(result) == [0, 1, 2, 3, 4]

    def test_zero(self):
        assert random_permutation(0) == []

    def test_one(self):
        assert random_permutation(1) == [0]

    def test_negative_n_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            random_permutation(-1)

    def test_deterministic_with_seeded_rng(self):
        rng1 = random.Random(7)
        rng2 = random.Random(7)
        assert random_permutation(10, rng=rng1) == random_permutation(10, rng=rng2)


class TestRandomSubset:
    def test_subset_size(self):
        population = list(range(100))
        result = random_subset(population, 5)
        assert len(result) == 5

    def test_subset_elements_are_from_population(self):
        population = list(range(50))
        result = random_subset(population, 10)
        for x in result:
            assert x in population

    def test_k_greater_than_population_returns_all(self):
        population = [1, 2, 3]
        result = random_subset(population, 10)
        assert sorted(result) == [1, 2, 3]

    def test_empty_population(self):
        assert random_subset([], 5) == []

    def test_k_zero_returns_empty(self):
        assert random_subset([1, 2, 3], 0) == []

    def test_k_negative_returns_empty(self):
        assert random_subset([1, 2, 3], -1) == []

    def test_reservoir_deterministic_with_seeded_rng(self):
        population = list(range(100))
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        assert random_subset(population, 5, rng=rng1) == random_subset(population, 5, rng=rng2)


class TestBogosortCheck:
    def test_sorted_list(self):
        assert bogosort_check([1, 2, 3, 4, 5]) is True

    def test_unsorted_list(self):
        assert bogosort_check([3, 1, 4, 2]) is False

    def test_empty_list(self):
        assert bogosort_check([]) is True

    def test_single_element(self):
        assert bogosort_check([99]) is True

    def test_duplicates_sorted(self):
        assert bogosort_check([1, 1, 2, 2, 3]) is True

    def test_duplicates_unsorted(self):
        assert bogosort_check([1, 2, 1]) is False
