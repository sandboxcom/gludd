"""Deep reservoir sampling and random algorithm tests.

Covers reservoir_sample, weighted_reservoir_sample, systematic_sample,
and fisher_yates_shuffle with 30+ test cases.
"""

from __future__ import annotations

import random
from collections import Counter

from general_ludd.algorithms.reservoir import (
    fisher_yates_shuffle,
    reservoir_sample,
    systematic_sample,
    weighted_reservoir_sample,
)


def _int_stream(n: int) -> list[int]:
    return list(range(n))


class TestReservoirSample:
    """Uniform reservoir sampling tests."""

    def test_exact_k_items_returned(self) -> None:
        rng = random.Random(42)
        result = reservoir_sample(range(1000), 10, rng=rng)
        assert len(result) == 10
        assert all(isinstance(x, int) for x in result)

    def test_k_greater_than_n_returns_all(self) -> None:
        rng = random.Random(42)
        result = reservoir_sample(range(5), 20, rng=rng)
        assert len(result) == 5
        assert set(result) == {0, 1, 2, 3, 4}

    def test_k_zero_returns_empty(self) -> None:
        rng = random.Random(42)
        result = reservoir_sample(range(100), 0, rng=rng)
        assert result == []

    def test_k_negative_returns_empty(self) -> None:
        result = reservoir_sample(range(100), -1)
        assert result == []

    def test_deterministic_with_fixed_seed(self) -> None:
        rng1 = random.Random(99)
        rng2 = random.Random(99)
        result1 = reservoir_sample(range(500), 5, rng=rng1)
        result2 = reservoir_sample(range(500), 5, rng=rng2)
        assert result1 == result2

    def test_different_seeds_give_different_results(self) -> None:
        rng1 = random.Random(1)
        rng2 = random.Random(2)
        result1 = reservoir_sample(range(500), 5, rng=rng1)
        result2 = reservoir_sample(range(500), 5, rng=rng2)
        assert result1 != result2

    def test_empty_stream_returns_empty(self) -> None:
        result = reservoir_sample(iter([]), 5)
        assert result == []

    def test_single_element_stream(self) -> None:
        result = reservoir_sample(iter([42]), 1)
        assert result == [42]

    def test_single_element_stream_k_larger(self) -> None:
        result = reservoir_sample(iter([42]), 5)
        assert result == [42]

    def test_iterator_input_consumed(self) -> None:
        it = iter(range(100))
        rng = random.Random(42)
        result = reservoir_sample(it, 10, rng=rng)
        assert len(result) == 10

    def test_large_k_small_n(self) -> None:
        result = reservoir_sample(range(3), 10, rng=random.Random(42))
        assert len(result) == 3
        assert set(result) == {0, 1, 2}

    def test_uniform_distribution_approximate(self) -> None:
        counts: Counter[int] = Counter()
        trials = 2000
        for seed in range(trials):
            rng = random.Random(seed)
            result = reservoir_sample(range(20), 1, rng=rng)
            assert len(result) == 1
            counts[result[0]] += 1
        expected = trials / 20
        for i in range(20):
            assert counts[i] >= expected * 0.5
            assert counts[i] <= expected * 2.0

    def test_sample_from_list(self) -> None:
        rng = random.Random(42)
        data = ["a", "b", "c", "d", "e", "f"]
        result = reservoir_sample(data, 3, rng=rng)
        assert len(result) == 3
        assert all(x in data for x in result)

    def test_all_items_distinct_when_k_leq_population(self) -> None:
        rng = random.Random(42)
        data = list(range(100))
        for _ in range(20):
            result = reservoir_sample(data, 20, rng=rng)
            assert len(set(result)) == len(result)

    def test_no_rng_default_works(self) -> None:
        result = reservoir_sample(range(50), 5)
        assert len(result) == 5
        assert all(isinstance(x, int) for x in result)


class TestWeightedReservoirSample:
    """A-Chao weighted reservoir sampling tests."""

    def test_k_items_returned(self) -> None:
        rng = random.Random(42)
        stream = [(i, 1.0) for i in range(100)]
        result = weighted_reservoir_sample(stream, 10, rng=rng)
        assert len(result) == 10

    def test_k_greater_than_n_returns_all(self) -> None:
        stream = [("a", 1.0), ("b", 2.0)]
        result = weighted_reservoir_sample(stream, 10, rng=random.Random(42))
        assert len(result) == 2
        assert set(result) == {"a", "b"}

    def test_zero_weight_items_never_selected(self) -> None:
        rng = random.Random(42)
        stream = [("zero", 0.0), ("nonzero", 10.0)]
        for _ in range(50):
            result = weighted_reservoir_sample(stream, 1, rng=rng)
            assert result[0] != "zero"

    def test_negative_weight_skipped(self) -> None:
        rng = random.Random(42)
        stream = [("neg", -1.0), ("pos", 10.0)]
        for _ in range(50):
            result = weighted_reservoir_sample(stream, 1, rng=rng)
            assert "neg" not in result

    def test_high_weight_items_selected_more_often(self) -> None:
        counts: Counter[str] = Counter()
        stream = [("light", 1.0), ("heavy", 100.0)]
        for seed in range(500):
            rng = random.Random(seed)
            result = weighted_reservoir_sample(stream, 1, rng=rng)
            counts[result[0]] += 1
        assert counts["heavy"] > counts["light"] * 2

    def test_empty_stream_returns_empty(self) -> None:
        result = weighted_reservoir_sample(iter([]), 5)
        assert result == []

    def test_k_zero_returns_empty(self) -> None:
        stream = [("a", 1.0), ("b", 2.0)]
        result = weighted_reservoir_sample(stream, 0)
        assert result == []

    def test_all_zero_weights(self) -> None:
        stream = [("a", 0.0), ("b", 0.0), ("c", 0.0)]
        result = weighted_reservoir_sample(stream, 2, rng=random.Random(42))
        assert len(result) == 2
        assert all(x in {"a", "b", "c"} for x in result)

    def test_single_element(self) -> None:
        result = weighted_reservoir_sample([("only", 5.0)], 1, rng=random.Random(42))
        assert result == ["only"]

    def test_no_rng_default_works(self) -> None:
        stream = [(i, float(i + 1)) for i in range(50)]
        result = weighted_reservoir_sample(stream, 5)
        assert len(result) == 5


class TestSystematicSample:
    """Systematic sampling tests."""

    def test_covers_entire_population_with_step_one(self) -> None:
        pop = list(range(10))
        result = systematic_sample(pop, 10)
        assert len(result) == 10
        assert set(result) == set(pop)

    def test_returns_k_items(self) -> None:
        rng = random.Random(42)
        pop = list(range(100))
        result = systematic_sample(pop, 5, rng=rng)
        assert len(result) == 5

    def test_different_seeds_give_different_start(self) -> None:
        pop = list(range(100))
        rng1 = random.Random(1)
        rng2 = random.Random(999)
        result1 = systematic_sample(pop, 2, rng=rng1)
        result2 = systematic_sample(pop, 2, rng=rng2)
        assert result1 != result2

    def test_k_greater_than_n_returns_all(self) -> None:
        pop = ["x", "y"]
        result = systematic_sample(pop, 10)
        assert len(result) == 2
        assert set(result) == {"x", "y"}

    def test_k_zero_returns_empty(self) -> None:
        result = systematic_sample([1, 2, 3], 0)
        assert result == []

    def test_empty_population_returns_empty(self) -> None:
        result = systematic_sample([], 5)
        assert result == []

    def test_k_equals_one_returns_one_element(self) -> None:
        pop = list(range(50))
        result = systematic_sample(pop, 1, rng=random.Random(42))
        assert len(result) == 1
        assert result[0] in pop

    def test_deterministic_with_same_seed(self) -> None:
        pop = list(range(200))
        rng1 = random.Random(77)
        rng2 = random.Random(77)
        assert systematic_sample(pop, 4, rng=rng1) == systematic_sample(pop, 4, rng=rng2)

    def test_no_rng_default_works(self) -> None:
        result = systematic_sample(list(range(30)), 5)
        assert len(result) == 5


class TestFisherYatesShuffle:
    """Fisher-Yates shuffle tests."""

    def test_preserves_all_elements(self) -> None:
        arr = list(range(20))
        result = fisher_yates_shuffle(arr, rng=random.Random(42))
        assert len(result) == 20
        assert set(result) == set(range(20))

    def test_deterministic_with_seed(self) -> None:
        arr1 = list(range(10))
        arr2 = list(range(10))
        fisher_yates_shuffle(arr1, rng=random.Random(99))
        fisher_yates_shuffle(arr2, rng=random.Random(99))
        assert arr1 == arr2

    def test_different_seeds_different_order(self) -> None:
        arr1 = list(range(10))
        arr2 = list(range(10))
        fisher_yates_shuffle(arr1, rng=random.Random(1))
        fisher_yates_shuffle(arr2, rng=random.Random(2))
        assert arr1 != arr2

    def test_empty_list_unchanged(self) -> None:
        arr: list[int] = []
        result = fisher_yates_shuffle(arr)
        assert result == []

    def test_single_element_unchanged(self) -> None:
        result = fisher_yates_shuffle([5])
        assert result == [5]

    def test_no_duplicates_after_shuffle(self) -> None:
        arr = list(range(50))
        result = fisher_yates_shuffle(arr, rng=random.Random(42))
        assert len(result) == len(set(result))

    def test_returns_same_list_object(self) -> None:
        arr = [1, 2, 3, 4, 5]
        result = fisher_yates_shuffle(arr, rng=random.Random(42))
        assert result is arr

    def test_shuffling_twice_may_differ(self) -> None:
        rng1 = random.Random(42)
        rng2 = random.Random(99)
        arr = list(range(20))
        copy1 = list(arr)
        copy2 = list(arr)
        fisher_yates_shuffle(copy1, rng=rng1)
        fisher_yates_shuffle(copy2, rng=rng2)
        assert copy1 != copy2

    def test_no_rng_default_works(self) -> None:
        arr = list(range(20))
        result = fisher_yates_shuffle(arr)
        assert len(result) == 20
        assert set(result) == set(range(20))
