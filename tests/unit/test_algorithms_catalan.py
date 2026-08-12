"""Tests for src/general_ludd/algorithms/catalan.py"""

from __future__ import annotations

import pytest

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
    def test_known_values(self):
        assert binomial(5, 2) == 10
        assert binomial(10, 5) == 252
        assert binomial(5, 0) == 1
        assert binomial(5, 5) == 1

    def test_k_out_of_range(self):
        assert binomial(5, 10) == 0
        assert binomial(5, -1) == 0

    def test_large_values(self):
        assert binomial(20, 10) == 184756
        assert binomial(30, 15) == 155117520

    def test_symmetry(self):
        n = 10
        for k in range(n + 1):
            assert binomial(n, k) == binomial(n, n - k)


class TestCatalan:
    def test_known_values(self):
        assert catalan(0) == 1
        assert catalan(1) == 1
        assert catalan(2) == 2
        assert catalan(3) == 5
        assert catalan(4) == 14
        assert catalan(5) == 42
        assert catalan(10) == 16796

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            catalan(-1)

    def test_catalan_numbers(self):
        result = catalan_numbers(6)
        assert result == [1, 1, 2, 5, 14, 42]


class TestStirling1:
    def test_base_cases(self):
        assert stirling1(0, 0) == 1
        assert stirling1(1, 1) == 1
        assert stirling1(2, 1) == 1

    def test_known_values(self):
        assert stirling1(4, 2) == 11
        assert stirling1(5, 3) == 35
        assert stirling1(5, 5) == 1

    def test_invalid_inputs(self):
        assert stirling1(-1, 0) == 0
        assert stirling1(5, -1) == 0
        assert stirling1(3, 5) == 0


class TestStirling2:
    def test_base_cases(self):
        assert stirling2(0, 0) == 1
        assert stirling2(1, 1) == 1

    def test_known_values(self):
        assert stirling2(4, 2) == 7
        assert stirling2(5, 3) == 25
        assert stirling2(5, 5) == 1

    def test_invalid_inputs(self):
        assert stirling2(-1, 0) == 0
        assert stirling2(5, -1) == 0
        assert stirling2(3, 5) == 0


class TestBellNumber:
    def test_known_values(self):
        assert bell_number(0) == 1
        assert bell_number(1) == 1
        assert bell_number(2) == 2
        assert bell_number(3) == 5
        assert bell_number(4) == 15
        assert bell_number(5) == 52

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            bell_number(-1)

    def test_bell_triangle(self):
        result = bell_triangle(6)
        assert result == [1, 1, 2, 5, 15, 52]


class TestCountPartitions:
    def test_known_values(self):
        assert count_partitions(0) == 1
        assert count_partitions(1) == 1
        assert count_partitions(2) == 2
        assert count_partitions(3) == 3
        assert count_partitions(4) == 5
        assert count_partitions(5) == 7
        assert count_partitions(10) == 42

    def test_negative(self):
        assert count_partitions(-1) == 0
