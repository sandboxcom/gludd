"""Deep tests for integer-partition algorithms: counting (Euler),
restricted, conjugate, generating-function, and exhaustive enumeration.
"""

from __future__ import annotations

from general_ludd.algorithms.partition import (
    partition_all_counts,
    partition_conjugate,
    partition_count,
    partition_count_mod,
    partition_generating_coeffs,
    partition_into_distinct_parts,
    partition_into_k_parts,
    partition_list,
    partition_restricted_count,
)


class TestPartitionCountEuler:
    def test_negative_returns_zero(self) -> None:
        assert partition_count(-1) == 0
        assert partition_count(-5) == 0

    def test_zero_has_one_partition(self) -> None:
        assert partition_count(0) == 1

    def test_oeis_small_values(self) -> None:
        expected = {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 11, 7: 15, 8: 22}
        for n, v in expected.items():
            assert partition_count(n) == v, f"p({n}) should be {v}"

    def test_moderate_value(self) -> None:
        assert partition_count(10) == 42

    def test_at_least_30(self) -> None:
        assert partition_count(30) == 5604


class TestPartitionCountMod:
    def test_mod_small(self) -> None:
        assert partition_count_mod(10, 7) == partition_count(10) % 7

    def test_mod_large(self) -> None:
        assert partition_count_mod(30, 97) == partition_count(30) % 97

    def test_mod_negative(self) -> None:
        assert partition_count_mod(-5, 13) == 0


class TestPartitionAllCounts:
    def test_first_several_match_oeis(self) -> None:
        seq = partition_all_counts(10)
        assert seq[0] == 1
        assert seq[1] == 1
        assert seq[2] == 2
        assert seq[3] == 3
        assert seq[4] == 5
        assert seq[5] == 7
        assert seq[6] == 11
        assert seq[7] == 15
        assert seq[10] == 42

    def test_prefix_matches_partition_count(self) -> None:
        seq = partition_all_counts(15)
        for i in range(len(seq)):
            assert seq[i] == partition_count(i)


class TestPartitionRestrictedCount:
    def test_max_part_one(self) -> None:
        assert partition_restricted_count(5, 1) == 1

    def test_max_part_unbounded_equals_full(self) -> None:
        for n in range(10):
            assert partition_restricted_count(n, n) == partition_count(n)

    def test_specific_restricted(self) -> None:
        assert partition_restricted_count(5, 2) == 3

    def test_negative_n(self) -> None:
        assert partition_restricted_count(-3, 5) == 0


class TestPartitionIntoKParts:
    def test_k_equal_one(self) -> None:
        assert partition_into_k_parts(7, 1) == 1

    def test_k_equals_n(self) -> None:
        assert partition_into_k_parts(4, 4) == 1

    def test_k_greater_than_n(self) -> None:
        assert partition_into_k_parts(3, 5) == 0

    def test_specific_case(self) -> None:
        assert partition_into_k_parts(7, 3) == 4

    def test_zero_zero(self) -> None:
        assert partition_into_k_parts(0, 0) == 1

    def test_nonzero_zero(self) -> None:
        assert partition_into_k_parts(5, 0) == 0


class TestPartitionIntoDistinctParts:
    def test_small_values(self) -> None:
        assert partition_into_distinct_parts(0) == 1
        assert partition_into_distinct_parts(1) == 1
        assert partition_into_distinct_parts(2) == 1
        assert partition_into_distinct_parts(3) == 2
        assert partition_into_distinct_parts(4) == 2
        assert partition_into_distinct_parts(5) == 3
        assert partition_into_distinct_parts(6) == 4
        assert partition_into_distinct_parts(8) == 6

    def test_equals_odd_parts_identity(self) -> None:
        for n in range(1, 13):
            d = partition_into_distinct_parts(n)
            r = partition_restricted_count_odd(n)
            assert d == r, f"n={n}: distinct={d} odd_parts={r}"

    def test_negative(self) -> None:
        assert partition_into_distinct_parts(-3) == 0


class TestPartitionConjugate:
    def test_symmetric_identity(self) -> None:
        assert partition_conjugate(()) == ()

    def test_self_conjugate(self) -> None:
        assert partition_conjugate((3, 2, 1)) == (3, 2, 1)
        assert partition_conjugate((4, 3, 2, 1)) == (4, 3, 2, 1)

    def test_non_symmetric(self) -> None:
        assert partition_conjugate((4, 2, 1)) == (3, 2, 1, 1)
        assert partition_conjugate((3, 3, 2)) == (3, 3, 2)

    def test_double_conjugate_is_identity(self) -> None:
        for parts in [(5, 2), (4, 3, 1), (6, 5, 2, 1), (7, 7, 3), (8, 4, 2, 2)]:
            assert partition_conjugate(partition_conjugate(parts)) == parts

    def test_conjugate_sums_match(self) -> None:
        for parts in [(5, 2), (4, 3, 1), (6, 5, 2, 1), (7, 7, 3)]:
            conj = partition_conjugate(parts)
            assert sum(conj) == sum(parts)
            assert len(conj) == parts[0]


class TestPartitionGeneratingCoeffs:
    def test_matches_euler_for_small_n(self) -> None:
        for n in range(13):
            assert partition_generating_coeffs(n) == partition_count(n)

    def test_negative(self) -> None:
        assert partition_generating_coeffs(-1) == 0


class TestPartitionList:
    def test_zero_has_empty_partition(self) -> None:
        assert partition_list(0) == [()]

    def test_one(self) -> None:
        assert partition_list(1) == [(1,)]

    def test_all_partitions_of_four(self) -> None:
        parts = partition_list(4)
        as_sets = {tuple(sorted(p, reverse=True)) for p in parts}
        expected = {
            (4,),
            (3, 1),
            (2, 2),
            (2, 1, 1),
            (1, 1, 1, 1),
        }
        assert as_sets == expected
        assert len(parts) == 5

    def test_count_matches_partition_count(self) -> None:
        for n in range(1, 11):
            assert len(partition_list(n)) == partition_count(n)


def partition_restricted_count_odd(n: int) -> int:
    """Number of partitions of n using only odd parts.  O(n²) time."""
    if n < 0:
        return 0
    dp = [0] * (n + 1)
    dp[0] = 1
    for part in range(1, n + 1, 2):
        for s in range(part, n + 1):
            dp[s] += dp[s - part]
    return dp[n]
