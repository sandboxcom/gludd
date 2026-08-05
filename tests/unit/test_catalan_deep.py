"""Deep Catalan and combinatorial tests: Catalan numbers, binomial,
Stirling numbers (1st & 2nd kind), Bell numbers, partition count.
"""

from __future__ import annotations

import math
from typing import ClassVar

from general_ludd.algorithms.catalan import (
    bell_number,
    bell_triangle,
    binomial,
    catalan,
    catalan_numbers,
    count_partitions,
    stirling1,
    stirling2,
)


class TestBinomial:
    def test_small_known_values(self) -> None:
        assert binomial(5, 0) == 1
        assert binomial(5, 1) == 5
        assert binomial(5, 2) == 10
        assert binomial(5, 3) == 10
        assert binomial(5, 4) == 5
        assert binomial(5, 5) == 1

    def test_edge_cases(self) -> None:
        assert binomial(0, 0) == 1
        assert binomial(10, -1) == 0
        assert binomial(10, 11) == 0
        assert binomial(0, 1) == 0

    def test_symmetry(self) -> None:
        for n in range(30):
            for k in range(n + 1):
                assert binomial(n, k) == binomial(n, n - k)

    def test_pascal_identity(self) -> None:
        for n in range(1, 20):
            for k in range(1, n):
                assert binomial(n, k) == binomial(n - 1, k - 1) + binomial(n - 1, k)

    def test_row_sum_equals_pow2(self) -> None:
        for n in range(20):
            assert sum(binomial(n, k) for k in range(n + 1)) == 2**n


class TestCatalan:
    known: ClassVar[list[int]] = [1, 1, 2, 5, 14, 42, 132, 429, 1430, 4862, 16796, 58786]

    def test_known_sequence(self) -> None:
        for i, expected in enumerate(self.known):
            assert catalan(i) == expected

    def test_recurrence(self) -> None:
        for n in range(1, 11):
            recurrence = sum(catalan(i) * catalan(n - 1 - i) for i in range(n))
            assert catalan(n) == recurrence

    def test_closed_form(self) -> None:
        for n in range(20):
            expected = binomial(2 * n, n) // (n + 1)
            assert catalan(n) == expected

    def test_catalan_numbers_range(self) -> None:
        result = catalan_numbers(8)
        assert result == self.known[:8]

    def test_negative_raises(self) -> None:
        try:
            catalan(-1)
            raise AssertionError()
        except ValueError:
            pass

    def test_large_values(self) -> None:
        assert catalan(20) == 6564120420
        assert catalan(25) == 4861946401452


class TestStirling1:
    triangle: ClassVar[list[list[int]]] = [
        [1],
        [0, 1],
        [0, 1, 1],
        [0, 2, 3, 1],
        [0, 6, 11, 6, 1],
        [0, 24, 50, 35, 10, 1],
    ]

    def test_triangle_values(self) -> None:
        for n, row in enumerate(self.triangle):
            for k, val in enumerate(row):
                assert stirling1(n, k) == val

    def test_edge_cases(self) -> None:
        assert stirling1(0, 0) == 1
        assert stirling1(5, 0) == 0
        assert stirling1(5, 5) == 1
        assert stirling1(5, 6) == 0
        assert stirling1(-1, 0) == 0
        assert stirling1(3, -1) == 0

    def test_row_sums_to_factorial(self) -> None:
        for n in range(10):
            row_sum = sum(stirling1(n, k) for k in range(n + 1))
            assert row_sum == math.factorial(n)

    def test_larger_values(self) -> None:
        assert stirling1(10, 3) == 1172700
        assert stirling1(10, 5) == 269325


class TestStirling2:
    triangle: ClassVar[list[list[int]]] = [
        [1],
        [0, 1],
        [0, 1, 1],
        [0, 1, 3, 1],
        [0, 1, 7, 6, 1],
        [0, 1, 15, 25, 10, 1],
        [0, 1, 31, 90, 65, 15, 1],
    ]

    def test_triangle_values(self) -> None:
        for n, row in enumerate(self.triangle):
            for k, val in enumerate(row):
                assert stirling2(n, k) == val

    def test_edge_cases(self) -> None:
        assert stirling2(0, 0) == 1
        assert stirling2(5, 0) == 0
        assert stirling2(5, 5) == 1
        assert stirling2(5, 6) == 0
        assert stirling2(-1, 0) == 0
        assert stirling2(3, -1) == 0

    def test_sums_to_bell(self) -> None:
        for n in range(12):
            s2_sum = sum(stirling2(n, k) for k in range(n + 1))
            assert s2_sum == bell_number(n)

    def test_larger_values(self) -> None:
        assert stirling2(10, 4) == 34105
        assert stirling2(12, 6) == 1323652


class TestBellNumber:
    known: ClassVar[list[int]] = [1, 1, 2, 5, 15, 52, 203, 877, 4140, 21147]

    def test_known_sequence(self) -> None:
        for i, expected in enumerate(self.known):
            assert bell_number(i) == expected

    def test_bell_recurrence(self) -> None:
        for n in range(9):
            b_next = sum(binomial(n, k) * bell_number(k) for k in range(n + 1))
            assert bell_number(n + 1) == b_next

    def test_bell_triangle(self) -> None:
        bt = bell_triangle(8)
        assert bt == self.known[:8]

    def test_negative_raises(self) -> None:
        try:
            bell_number(-1)
            raise AssertionError()
        except ValueError:
            pass


class TestPartitionCount:
    known: ClassVar[list[int]] = [1, 1, 2, 3, 5, 7, 11, 15, 22, 30, 42, 56, 77, 101, 135]

    def test_known_sequence(self) -> None:
        for n, expected in enumerate(self.known):
            assert count_partitions(n) == expected

    def test_negative(self) -> None:
        assert count_partitions(-5) == 0

    def test_monotonic(self) -> None:
        for n in range(50):
            assert count_partitions(n + 1) >= count_partitions(n)
