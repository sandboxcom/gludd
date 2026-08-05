"""Deep tests for Fisher-Yates shuffle, random permutation, random subset
(reservoir sampling), and bogosort check — distributional fairness, edge cases,
and correctness against naive implementations.
"""

from __future__ import annotations

import collections
import random

import pytest

# ---------------------------------------------------------------------------
# Naive verifiers — ground truth for correctness checks
# ---------------------------------------------------------------------------


def _naive_shuffled(items: list[int]) -> list[int]:
    result = list(items)
    random.shuffle(result)
    return result


def _is_permutation(original: list[int], candidate: list[int]) -> bool:
    return len(original) == len(candidate) and sorted(original) == sorted(candidate)


def _is_sorted_ascending(items: list[int]) -> bool:
    return all(items[i] <= items[i + 1] for i in range(len(items) - 1))


def _naive_random_subset(population: list[int], k: int) -> list[int]:
    return random.sample(population, min(k, len(population)))


# ---------------------------------------------------------------------------
# Fixtures — import the module under test (will fail until implemented)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rmod():
    from general_ludd.algorithms import randomized

    return randomized


# ---------------------------------------------------------------------------
# Fisher-Yates shuffle
# ---------------------------------------------------------------------------


def test_fy_empty_list(rmod):
    assert rmod.fisher_yates_shuffle([]) == []


def test_fy_single_element(rmod):
    assert rmod.fisher_yates_shuffle([42]) == [42]


def test_fy_produces_permutation(rmod):
    original = [1, 2, 3, 4, 5, 6, 7, 8]
    for _ in range(20):
        result = rmod.fisher_yates_shuffle(original)
        assert _is_permutation(original, result)


def test_fy_does_not_mutate_original(rmod):
    original = [10, 20, 30, 40, 50]
    snapshot = list(original)
    rmod.fisher_yates_shuffle(original)
    assert original == snapshot


def test_fy_distribution_is_uniform(rmod):
    """Over many trials a 3-element list sees each permutation ~1/6 of the time."""
    items = [0, 1, 2]
    n = 6000
    counts: dict[tuple[int, ...], int] = collections.defaultdict(int)
    rng = random.Random(42)
    for _ in range(n):
        perm = tuple(rmod.fisher_yates_shuffle(items, rng=rng))
        counts[perm] += 1
    expected = n / 6
    for count in counts.values():
        assert 0.7 * expected < count < 1.3 * expected, f"permutation count {count} too far from expected {expected}"


def test_fy_identity_on_len_2(rmod):
    for _ in range(50):
        result = rmod.fisher_yates_shuffle([7, 8])
        assert result == [7, 8] or result == [8, 7]


def test_fy_large_input(rmod):
    n = 1000
    original = list(range(n))
    result = rmod.fisher_yates_shuffle(original)
    assert _is_permutation(original, result)
    assert result != original


# ---------------------------------------------------------------------------
# random_permutation
# ---------------------------------------------------------------------------


def test_perm_zero_length(rmod):
    assert rmod.random_permutation(0) == []


def test_perm_n1(rmod):
    assert rmod.random_permutation(1) == [0]


def test_perm_length_match(rmod):
    for n in [2, 3, 5, 10, 100]:
        result = rmod.random_permutation(n)
        assert len(result) == n
        assert set(result) == set(range(n))


def test_perm_no_duplicates(rmod):
    for n in [5, 10, 50]:
        result = rmod.random_permutation(n)
        assert len(result) == len(set(result))


def test_perm_negative_raises(rmod):
    with pytest.raises(ValueError):
        rmod.random_permutation(-1)


# ---------------------------------------------------------------------------
# random_subset (reservoir sampling)
# ---------------------------------------------------------------------------


def test_subset_empty_population(rmod):
    assert rmod.random_subset([], 5) == []


def test_subset_zero_k(rmod):
    assert rmod.random_subset([1, 2, 3], 0) == []


def test_subset_k_larger_than_population(rmod):
    pop = [1, 2, 3]
    result = rmod.random_subset(pop, 10)
    assert sorted(result) == sorted(pop)


def test_subset_length(rmod):
    pop = list(range(50))
    for k in [1, 5, 25]:
        result = rmod.random_subset(pop, k)
        assert len(result) == k
        assert set(result).issubset(set(pop))


def test_subset_no_duplicates(rmod):
    pop = list(range(100))
    result = rmod.random_subset(pop, 10)
    assert len(result) == len(set(result))


def test_subset_distribution(rmod):
    """Each element in [0..9] should appear in ~k/n of subsets when k=3, n=10."""
    pop = list(range(10))
    k = 3
    trials = 5000
    rng = random.Random(99)
    appearance_counts = collections.Counter[int]()
    for _ in range(trials):
        for elem in rmod.random_subset(pop, k, rng=rng):
            appearance_counts[elem] += 1
    expected = trials * k / len(pop)
    for elem in pop:
        assert 0.65 * expected < appearance_counts[elem] < 1.35 * expected, (
            f"element {elem} appeared {appearance_counts[elem]} times, expected ~{expected}"
        )


# ---------------------------------------------------------------------------
# bogosort_check — checks if a list is sorted
# ---------------------------------------------------------------------------


def test_bogosort_check_empty(rmod):
    assert rmod.bogosort_check([]) is True


def test_bogosort_check_single(rmod):
    assert rmod.bogosort_check([99]) is True


def test_bogosort_check_sorted(rmod):
    assert rmod.bogosort_check([1, 2, 3, 4, 5]) is True


def test_bogosort_check_unsorted(rmod):
    assert rmod.bogosort_check([1, 3, 2, 4]) is False


def test_bogosort_check_with_duplicates_sorted(rmod):
    assert rmod.bogosort_check([1, 1, 2, 2, 3]) is True


def test_bogosort_check_with_duplicates_unsorted(rmod):
    assert rmod.bogosort_check([1, 1, 3, 2, 2]) is False


def test_bogosort_check_descending(rmod):
    assert rmod.bogosort_check([5, 4, 3, 2, 1]) is False


def test_bogosort_check_large_sorted(rmod):
    n = 10000
    assert rmod.bogosort_check(list(range(n))) is True


def test_bogosort_check_large_unsorted(rmod):
    n = 10000
    items = list(range(n))
    items[-1], items[0] = items[0], items[-1]
    assert rmod.bogosort_check(items) is False
