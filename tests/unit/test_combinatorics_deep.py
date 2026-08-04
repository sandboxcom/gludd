"""Deep combinatorics tests: permutations, combinations, power set,
Cartesian product, partition generation.

Pure-Python (stdlib only) — no external combinatorics library.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator

# ── Permutations ──────────────────────────────────────────────────────


def permutations_iter(seq: list[int], k: int | None = None) -> Iterator[tuple[int, ...]]:
    """Iterative permutation generator using index swapping (Heap-style)."""
    n = len(seq)
    r = n if k is None else k
    if r > n:
        return
    indices = list(range(n))
    cycles = list(range(n, n - r, -1))
    out = tuple(seq[i] for i in indices[:r])
    yield out
    while True:
        for i in reversed(range(r)):
            cycles[i] -= 1
            if cycles[i] == 0:
                indices[i:] = [*indices[i + 1:], indices[i]]
                cycles[i] = n - i
            else:
                j = cycles[i]
                indices[i], indices[-j] = indices[-j], indices[i]
                out = tuple(seq[idx] for idx in indices[:r])
                yield out
                break
        else:
            return


def count_permutations(n: int, k: int | None = None) -> int:
    r = n if k is None else k
    if r > n:
        return 0
    return math.factorial(n) // math.factorial(n - r)


class TestPermutations:
    def test_permutations_empty(self) -> None:
        result = list(permutations_iter([]))
        assert len(result) == 1
        assert result == [()]

    def test_permutations_single(self) -> None:
        result = list(permutations_iter([7]))
        assert len(result) == 1
        assert result == [(7,)]

    def test_permutations_n3(self) -> None:
        result = sorted(permutations_iter([1, 2, 3]))
        expected = sorted(itertools.permutations([1, 2, 3]))
        assert result == expected
        assert len(result) == 6

    def test_permutations_n4(self) -> None:
        result = sorted(permutations_iter([1, 2, 3, 4]))
        expected = sorted(itertools.permutations([1, 2, 3, 4]))
        assert result == expected
        assert len(result) == 24

    def test_permutations_k2_n4(self) -> None:
        result = sorted(permutations_iter([1, 2, 3, 4], k=2))
        expected = sorted(itertools.permutations([1, 2, 3, 4], 2))
        assert result == expected
        assert len(result) == 12

    def test_permutations_k_greater_than_n(self) -> None:
        result = list(permutations_iter([1, 2], k=3))
        assert result == []

    def test_count_permutations(self) -> None:
        assert count_permutations(5) == 120
        assert count_permutations(5, 2) == 20
        assert count_permutations(7, 3) == 210
        assert count_permutations(4, 5) == 0

    def test_count_matches_generated(self) -> None:
        for n in range(7):
            for k in range(1, n + 1):
                gen_count = len(list(permutations_iter(list(range(n)), k=k)))
                assert gen_count == count_permutations(n, k)


# ── Combinations ──────────────────────────────────────────────────────


def combinations_iter(seq: list[int], k: int) -> Iterator[tuple[int, ...]]:
    """Lexicographic combination generator."""
    n = len(seq)
    if k > n:
        return
    indices = list(range(k))
    yield tuple(seq[i] for i in indices)
    while True:
        for i in reversed(range(k)):
            if indices[i] != i + n - k:
                break
        else:
            return
        indices[i] += 1
        for j in range(i + 1, k):
            indices[j] = indices[j - 1] + 1
        yield tuple(seq[idx] for idx in indices)


def binomial(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    result = 1
    for i in range(1, k + 1):
        result = result * (n - k + i) // i
    return result


class TestCombinations:
    def test_combinations_n_choose_0(self) -> None:
        assert binomial(5, 0) == 1
        result = list(combinations_iter([1, 2, 3, 4, 5], 0))
        assert result == [()]

    def test_combinations_n_choose_n(self) -> None:
        assert binomial(5, 5) == 1
        result = list(combinations_iter([1, 2, 3], 3))
        assert result == [(1, 2, 3)]

    def test_combinations_k_greater_than_n(self) -> None:
        result = list(combinations_iter([1, 2], 5))
        assert result == []

    def test_combinations_5_choose_2(self) -> None:
        result = sorted(combinations_iter([1, 2, 3, 4, 5], 2))
        expected = sorted(itertools.combinations([1, 2, 3, 4, 5], 2))
        assert result == expected
        assert len(result) == 10

    def test_combinations_6_choose_3(self) -> None:
        result = sorted(combinations_iter([1, 2, 3, 4, 5, 6], 3))
        expected = sorted(itertools.combinations([1, 2, 3, 4, 5, 6], 3))
        assert result == expected
        assert len(result) == 20

    def test_binomial_identity(self) -> None:
        for n in range(10):
            for k in range(n + 1):
                assert binomial(n, k) == binomial(n, n - k)

    def test_binomial_sum_identity(self) -> None:
        for n in range(9):
            assert sum(binomial(n, k) for k in range(n + 1)) == 2**n

    def test_count_matches_generated(self) -> None:
        for n in range(10):
            for k in range(n + 1):
                gen_count = len(list(combinations_iter(list(range(n)), k)))
                assert gen_count == binomial(n, k)


# ── Power Set ─────────────────────────────────────────────────────────


def power_set_iter(seq: list[int]) -> Iterator[tuple[int, ...]]:
    """Iterative power set using binary counting."""
    n = len(seq)
    for mask in range(1 << n):
        yield tuple(seq[i] for i in range(n) if (mask >> i) & 1)


def power_set_recursive(seq: list[int]) -> list[tuple[int, ...]]:
    if not seq:
        return [()]
    rest = power_set_recursive(seq[1:])
    return rest + [(seq[0], *s) for s in rest]


class TestPowerSet:
    def test_power_set_empty(self) -> None:
        result = sorted(power_set_iter([]))
        assert result == [()]

    def test_power_set_single(self) -> None:
        result = sorted(power_set_iter([42]))
        assert result == [(), (42,)]

    def test_power_set_n3(self) -> None:
        result = sorted(power_set_iter([1, 2, 3]))
        expected = sorted(power_set_recursive([1, 2, 3]))
        assert result == expected
        assert len(result) == 8

    def test_power_set_n5_size(self) -> None:
        result = list(power_set_iter([1, 2, 3, 4, 5]))
        assert len(result) == 32

    def test_power_set_n8_size(self) -> None:
        seq = list(range(8))
        result = list(power_set_iter(seq))
        assert len(result) == 256

    def test_power_set_all_unique(self) -> None:
        seq = [1, 2, 3, 4]
        result = sorted(power_set_iter(seq))
        assert len(result) == len(set(result))

    def test_iterative_matches_recursive(self) -> None:
        for n in range(7):
            seq = list(range(n))
            a = sorted(power_set_iter(seq))
            b = sorted(power_set_recursive(seq))
            assert a == b

    def test_sum_empty_set_zero(self) -> None:
        result = list(power_set_iter([]))
        assert result == [()]


# ── Cartesian Product ──────────────────────────────────────────────────


def cartesian_product(*sets: list[int]) -> Iterator[tuple[int, ...]]:
    """Iterative Cartesian product using index counting."""
    if not sets:
        yield ()
        return
    lengths = [len(s) for s in sets]
    total = 1
    for ln in lengths:
        total *= ln
    indices = [0] * len(sets)
    for _ in range(total):
        yield tuple(sets[i][indices[i]] for i in range(len(sets)))
        for pos in range(len(sets) - 1, -1, -1):
            indices[pos] += 1
            if indices[pos] < lengths[pos]:
                break
            indices[pos] = 0


class TestCartesianProduct:
    def test_no_sets(self) -> None:
        result = list(cartesian_product())
        assert result == [()]

    def test_one_set(self) -> None:
        result = sorted(cartesian_product([1, 2, 3]))
        assert result == [(1,), (2,), (3,)]

    def test_empty_set_among_inputs(self) -> None:
        result = list(cartesian_product([1, 2], []))
        assert result == []

    def test_two_sets(self) -> None:
        result = sorted(cartesian_product([1, 2], [10, 11, 12]))
        expected = sorted(itertools.product([1, 2], [10, 11, 12]))
        assert result == expected
        assert len(result) == 6

    def test_three_sets(self) -> None:
        result = sorted(cartesian_product([1, 2], [10, 11], [100]))
        expected = sorted(itertools.product([1, 2], [10, 11], [100]))
        assert result == expected
        assert len(result) == 4

    def test_product_count(self) -> None:
        result = list(
            cartesian_product(
                [1, 2, 3],
                [10, 11],
                [100, 101, 102],
                [1000, 1001],
            )
        )
        assert len(result) == 3 * 2 * 3 * 2

    def test_matches_itertools(self) -> None:
        for n in range(1, 5):
            sets = [list(range(i + 1, i + 4)) for i in range(n)]
            a = sorted(cartesian_product(*sets))
            b = sorted(itertools.product(*sets))
            assert a == b


# ── Integer Partitions ─────────────────────────────────────────────────


def integer_partitions(n: int) -> Iterator[list[int]]:
    """Generate all integer partitions of n (non-increasing order)."""
    if n == 0:
        yield []
        return
    a = [0] * (n + 1)
    k = 1
    a[0] = 0
    a[1] = n
    while k != 0:
        x = a[k - 1] + 1
        y = a[k] - 1
        k -= 1
        while x <= y:
            a[k] = x
            y -= x
            k += 1
        a[k] = x + y
        yield a[: k + 1]


def count_partitions_p(n: int) -> int:
    """Euler's pentagonal-number recurrence for partition function p(n)."""
    if n < 0:
        return 0
    p = [0] * (n + 1)
    p[0] = 1
    for i in range(1, n + 1):
        total = 0
        k = 1
        while True:
            pent1 = k * (3 * k - 1) // 2
            if pent1 > i:
                break
            sign = 1 if k % 2 == 1 else -1
            total += sign * p[i - pent1]
            pent2 = k * (3 * k + 1) // 2
            if pent2 <= i:
                total += sign * p[i - pent2]
            k += 1
        p[i] = total
    return p[n]


class TestIntegerPartitions:
    def test_partitions_0(self) -> None:
        result = list(integer_partitions(0))
        assert result == [[]]

    def test_partitions_1(self) -> None:
        result = list(integer_partitions(1))
        assert result == [[1]]

    def test_partitions_4(self) -> None:
        result = sorted(tuple(sorted(p, reverse=True)) for p in integer_partitions(4))
        expected = [[4], [3, 1], [2, 2], [2, 1, 1], [1, 1, 1, 1]]
        expected_sorted = sorted(tuple(x) for x in expected)
        assert result == expected_sorted
        assert len(result) == 5

    def test_partitions_5(self) -> None:
        result = list(integer_partitions(5))
        assert len(result) == 7

    def test_partitions_partial_sequence(self) -> None:
        expected = [1, 1, 2, 3, 5, 7, 11, 15, 22, 30, 42, 56, 77, 101]
        for n, exp in enumerate(expected):
            assert count_partitions_p(n) == exp

    def test_partitions_valid(self) -> None:
        for n in range(1, 12):
            for p in integer_partitions(n):
                assert p == sorted(p, reverse=True) or p == sorted(p)
                assert sum(p) == n

    def test_count_matches_generated(self) -> None:
        for n in range(13):
            assert len(list(integer_partitions(n))) == count_partitions_p(n)


# ── Set Partitions (Bell Numbers) ─────────────────────────────────────


def set_partitions(seq: list[int]) -> Iterator[list[list[int]]]:
    """Generate all set partitions using restricted growth strings."""
    if not seq:
        yield []
        return
    n = len(seq)
    rgs = [0] * n
    while True:
        partition: list[list[int]] = [[] for _ in range(max(rgs) + 1)]
        for i, block in enumerate(rgs):
            partition[block].append(seq[i])
        yield partition
        j = n - 1
        while j > 0 and rgs[j] == max(rgs[:j]) + 1:
            j -= 1
        if j == 0:
            break
        rgs[j] += 1
        for k in range(j + 1, n):
            rgs[k] = 0


def bell_number(n: int) -> int:
    if n == 0:
        return 1
    bell = [0] * (n + 1)
    bell[0] = 1
    for i in range(1, n + 1):
        bell[i] = 0
        for j in range(i):
            bell[i] += binomial(i - 1, j) * bell[j]
    return bell[n]


class TestSetPartitions:
    def test_set_partitions_empty(self) -> None:
        result = list(set_partitions([]))
        assert result == [[]]

    def test_set_partitions_single(self) -> None:
        result = list(set_partitions([5]))
        assert len(result) == 1
        assert result == [[[5]]]

    def test_set_partitions_n2(self) -> None:
        result = list(set_partitions([1, 2]))
        assert len(result) == 2
        expected = [[[1, 2]], [[1], [2]]]
        for r in result:
            r_sorted = sorted([sorted(b) for b in r])
            assert r_sorted in expected

    def test_set_partitions_n3(self) -> None:
        result = list(set_partitions([1, 2, 3]))
        assert len(result) == 5

    def test_set_partitions_n4(self) -> None:
        result = list(set_partitions([1, 2, 3, 4]))
        assert len(result) == 15

    def test_bell_sequence(self) -> None:
        expected = [1, 1, 2, 5, 15, 52, 203, 877, 4140]
        for n, exp in enumerate(expected):
            assert bell_number(n) == exp

    def test_bell_matches_count(self) -> None:
        for n in range(9):
            assert len(list(set_partitions(list(range(n))))) == bell_number(n)

    def test_partition_covers_all_elements(self) -> None:
        for n in range(1, 6):
            seq = list(range(n))
            for partition in set_partitions(seq):
                flattened = [x for block in partition for x in block]
                assert sorted(flattened) == seq


# ── Stirling Numbers of the Second Kind ───────────────────────────────


def stirling2(n: int, k: int) -> int:
    """S(n, k) via recurrence with cache."""
    cache: dict[tuple[int, int], int] = {}
    for i in range(n + 1):
        cache[(i, 0)] = 0
        cache[(i, i)] = 1
    cache[(0, 0)] = 1
    for i in range(1, n + 1):
        for j in range(1, min(i, k) + 1):
            cache[(i, j)] = cache.get((i - 1, j), 0) * j + cache.get((i - 1, j - 1), 0)
    return cache.get((n, k), 0)


class TestStirlingNumbers:
    def test_stirling_triangle(self) -> None:
        rows = [
            [1],
            [0, 1],
            [0, 1, 1],
            [0, 1, 3, 1],
            [0, 1, 7, 6, 1],
            [0, 1, 15, 25, 10, 1],
        ]
        for n, row in enumerate(rows):
            for k, val in enumerate(row):
                assert stirling2(n, k) == val

    def test_stirling_sums_to_bell(self) -> None:
        for n in range(9):
            bell = sum(stirling2(n, k) for k in range(n + 1))
            assert bell == bell_number(n)

    def test_stirling_edge_cases(self) -> None:
        assert stirling2(0, 0) == 1
        assert stirling2(0, 1) == 0
        assert stirling2(5, 0) == 0
        assert stirling2(5, 5) == 1
        assert stirling2(5, 6) == 0
