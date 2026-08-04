"""Deep tests for knapsack-family algorithms: 0/1, unbounded, fractional,
subset-sum, partition, coin-change (min coins + count ways).
"""

from __future__ import annotations

from general_ludd.algorithms.knapsack import (
    coin_change_min,
    coin_change_ways,
    knapsack_01,
    knapsack_01_items,
    knapsack_fractional,
    knapsack_unbounded,
    partition,
    partition_sets,
    subset_sum,
    subset_sum_items,
)

# ── knapsack_01 (value-only) ─────────────────────────────────────────


class TestKnapsack01:
    def test_zero_capacity(self) -> None:
        assert knapsack_01([60, 100, 120], [10, 20, 30], 0) == 0

    def test_no_items(self) -> None:
        assert knapsack_01([], [], 50) == 0

    def test_single_item_fits(self) -> None:
        assert knapsack_01([42], [7], 10) == 42

    def test_single_item_too_heavy(self) -> None:
        assert knapsack_01([99], [20], 10) == 0

    def test_classic_case(self) -> None:
        values = [60, 100, 120]
        weights = [10, 20, 30]
        assert knapsack_01(values, weights, 50) == 220

    def test_tiebreaker_picks_best_combination(self) -> None:
        values = [6, 5, 5]
        weights = [3, 2, 2]
        assert knapsack_01(values, weights, 4) == 10


# ── knapsack_01_items (value + traceback) ────────────────────────────


class TestKnapsack01Items:
    def test_classic_with_traceback(self) -> None:
        values = [60, 100, 120]
        weights = [10, 20, 30]
        best, items = knapsack_01_items(values, weights, 50)
        assert best == 220
        total_w = sum(weights[i] for i in items)
        total_v = sum(values[i] for i in items)
        assert total_w <= 50
        assert total_v == 220

    def test_empty_input(self) -> None:
        best, items = knapsack_01_items([], [], 10)
        assert best == 0
        assert items == []

    def test_capacity_zero(self) -> None:
        best, items = knapsack_01_items([5, 10], [1, 2], 0)
        assert best == 0
        assert items == []


# ── knapsack_unbounded ───────────────────────────────────────────────


class TestKnapsackUnbounded:
    def test_capacity_zero(self) -> None:
        assert knapsack_unbounded([10, 40], [5, 10], 0) == 0

    def test_single_item_repeat(self) -> None:
        assert knapsack_unbounded([15], [10], 30) == 45

    def test_rod_cutting_style(self) -> None:
        values = [1, 5, 8, 9]
        weights = [1, 2, 3, 4]
        assert knapsack_unbounded(values, weights, 4) == 10


# ── knapsack_fractional ──────────────────────────────────────────────


class TestKnapsackFractional:
    def test_exact_fill(self) -> None:
        v = [60, 100, 120]
        w = [10, 20, 30]
        assert abs(knapsack_fractional(v, w, 50) - 240.0) < 1e-9

    def test_partial_item(self) -> None:
        v = [60, 100]
        w = [10, 20]
        assert abs(knapsack_fractional(v, w, 15) - 85.0) < 1e-9

    def test_capacity_zero(self) -> None:
        assert knapsack_fractional([10, 20], [5, 7], 0) == 0.0


# ── subset_sum ───────────────────────────────────────────────────────


class TestSubsetSum:
    def test_target_zero_always_possible(self) -> None:
        assert subset_sum([1, 2, 3], 0) is True

    def test_simple_possible(self) -> None:
        assert subset_sum([3, 34, 4, 12, 5, 2], 9) is True

    def test_simple_impossible(self) -> None:
        assert subset_sum([3, 34, 4, 12, 5, 2], 13) is False

    def test_single_element_match(self) -> None:
        assert subset_sum([7], 7) is True

    def test_target_larger_than_sum(self) -> None:
        assert subset_sum([1, 2, 3], 100) is False


# ── subset_sum_items (with traceback) ────────────────────────────────


class TestSubsetSumItems:
    def test_small_traceback(self) -> None:
        possible, indices = subset_sum_items([3, 4, 5, 2], 7)
        assert possible is True
        total = sum([3, 4, 5, 2][i] for i in indices)
        assert total == 7

    def test_impossible_returns_empty(self) -> None:
        possible, indices = subset_sum_items([1, 2], 10)
        assert possible is False
        assert indices == []


# ── partition ────────────────────────────────────────────────────────


class TestPartition:
    def test_even_simple(self) -> None:
        assert partition([1, 5, 11, 5]) is True

    def test_odd_total(self) -> None:
        assert partition([1, 2, 3, 5]) is False

    def test_single_element(self) -> None:
        assert partition([5]) is False

    def test_equal_pair(self) -> None:
        assert partition([7, 7]) is True


# ── partition_sets (with concrete split) ─────────────────────────────


class TestPartitionSets:
    def test_small_split(self) -> None:
        ok, s1, s2 = partition_sets([1, 2, 3, 4])
        assert ok is True
        assert sorted(s1 + s2) == [0, 1, 2, 3]
        assert sum((1, 2, 3, 4)[i] for i in s1) == sum((1, 2, 3, 4)[i] for i in s2)

    def test_impossible_partition(self) -> None:
        ok, s1, s2 = partition_sets([1, 2, 5])
        assert ok is False
        assert s1 == s2 == []


# ── coin_change_min ──────────────────────────────────────────────────


class TestCoinChangeMin:
    def test_exact_fit(self) -> None:
        assert coin_change_min([1, 2, 5], 11) == 3

    def test_amount_zero(self) -> None:
        assert coin_change_min([1, 2, 5], 0) == 0

    def test_impossible(self) -> None:
        assert coin_change_min([2], 3) == -1

    def test_greedy_fail(self) -> None:
        assert coin_change_min([1, 3, 4], 6) == 2


# ── coin_change_ways ─────────────────────────────────────────────────


class TestCoinChangeWays:
    def test_classic_ways(self) -> None:
        assert coin_change_ways([1, 2, 5], 5) == 4

    def test_amount_zero(self) -> None:
        assert coin_change_ways([1, 2, 5], 0) == 1

    def test_no_solution(self) -> None:
        assert coin_change_ways([3], 5) == 0

    def test_single_coin_type(self) -> None:
        assert coin_change_ways([3], 6) == 1
