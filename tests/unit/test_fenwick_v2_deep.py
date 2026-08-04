"""Deep tests for Fenwick tree v2 — BIT, range update/query, 2D BIT, order statistic tree.

Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.fenwick_v2 import (
    BIT,
    BIT2D,
    OrderStatisticTree,
    RangeUpdatePointQuery,
    RangeUpdateRangeQuery,
)

# ── BIT (Fenwick tree) ─────────────────────────────────────────────────


class TestBIT:
    def test_construct_and_prefix_zero(self) -> None:
        bit = BIT(5)
        for i in range(5):
            assert bit.prefix_sum(i) == 0

    def test_single_add_prefix(self) -> None:
        bit = BIT(5)
        bit.add(2, 7)
        assert bit.prefix_sum(0) == 0
        assert bit.prefix_sum(1) == 0
        assert bit.prefix_sum(2) == 7
        assert bit.prefix_sum(3) == 7
        assert bit.prefix_sum(4) == 7

    def test_multiple_adds_cumulative(self) -> None:
        bit = BIT(10)
        bit.add(3, 5)
        bit.add(3, 3)
        bit.add(7, 2)
        assert bit.prefix_sum(3) == 8
        assert bit.prefix_sum(6) == 8
        assert bit.prefix_sum(7) == 10

    def test_range_sum(self) -> None:
        bit = BIT(8)
        for i, val in enumerate([3, 1, 4, 1, 5, 9, 2, 6]):
            bit.add(i, val)
        assert bit.range_sum(0, 3) == 9
        assert bit.range_sum(2, 5) == 19
        assert bit.range_sum(4, 7) == 22
        assert bit.range_sum(0, 7) == 31

    def test_range_sum_edge_empty(self) -> None:
        bit = BIT(5)
        bit.add(1, 10)
        assert bit.range_sum(3, 2) == 0

    def test_range_sum_single_element(self) -> None:
        bit = BIT(5)
        bit.add(2, 42)
        assert bit.range_sum(2, 2) == 42

    def test_from_array(self) -> None:
        bit = BIT(6).from_array([10, 20, 30, 40, 50, 60])
        assert bit.prefix_sum(0) == 10
        assert bit.prefix_sum(2) == 60
        assert bit.range_sum(1, 4) == 140

    def test_large_values(self) -> None:
        bit = BIT(3)
        bit.add(0, 10**9)
        bit.add(2, -(10**9))
        assert bit.range_sum(0, 2) == 0

    def test_negative_range_from_array(self) -> None:
        bit = BIT(4).from_array([-5, 10, -3, 8])
        assert bit.range_sum(0, 3) == 10
        assert bit.range_sum(1, 2) == 7


# ── RangeUpdatePointQuery ──────────────────────────────────────────────


class TestRangeUpdatePointQuery:
    def test_range_add_point_value(self) -> None:
        rup = RangeUpdatePointQuery(6)
        rup.range_add(1, 3, 5)
        assert rup.point_value(0) == 0
        assert rup.point_value(1) == 5
        assert rup.point_value(3) == 5
        assert rup.point_value(4) == 0

    def test_overlapping_ranges(self) -> None:
        rup = RangeUpdatePointQuery(10)
        rup.range_add(2, 7, 3)
        rup.range_add(4, 9, 2)
        assert rup.point_value(1) == 0
        assert rup.point_value(3) == 3
        assert rup.point_value(5) == 5
        assert rup.point_value(8) == 2
        rup.range_add(0, 9, -1)
        assert rup.point_value(5) == 4

    def test_from_values_then_query(self) -> None:
        rup = RangeUpdatePointQuery(5).from_values([7, 3, 9, 2, 4])
        assert rup.point_value(0) == 7
        assert rup.point_value(2) == 9
        assert rup.point_value(4) == 4
        rup.range_add(1, 3, 10)
        assert rup.point_value(0) == 7
        assert rup.point_value(1) == 13
        assert rup.point_value(3) == 12
        assert rup.point_value(4) == 4

    def test_single_element_update(self) -> None:
        rup = RangeUpdatePointQuery(5)
        rup.range_add(2, 2, 100)
        assert rup.point_value(2) == 100
        assert rup.point_value(1) == 0


# ── RangeUpdateRangeQuery ──────────────────────────────────────────────


class TestRangeUpdateRangeQuery:
    def test_build_and_range_sum(self) -> None:
        rur = RangeUpdateRangeQuery(5).from_values([1, 2, 3, 4, 5])
        assert rur.range_sum(0, 0) == 1
        assert rur.range_sum(0, 4) == 15
        assert rur.range_sum(2, 3) == 7

    def test_range_add_then_range_sum(self) -> None:
        rur = RangeUpdateRangeQuery(6).from_values([0, 0, 0, 0, 0, 0])
        rur.range_add(1, 4, 3)
        assert rur.range_sum(0, 0) == 0
        assert rur.range_sum(1, 4) == 12
        assert rur.range_sum(1, 2) == 6
        assert rur.range_sum(0, 5) == 12

    def test_multiple_range_adds(self) -> None:
        rur = RangeUpdateRangeQuery(8).from_values([1, 1, 1, 1, 1, 1, 1, 1])
        rur.range_add(2, 5, 2)
        rur.range_add(4, 6, -1)
        assert rur.range_sum(0, 1) == 2
        assert rur.range_sum(2, 3) == 6
        assert rur.range_sum(4, 5) == 4
        assert rur.range_sum(6, 7) == 1
        assert rur.range_sum(0, 7) == 13

    def test_large_range_update(self) -> None:
        rur = RangeUpdateRangeQuery(1000).from_values([0] * 1000)
        rur.range_add(0, 999, 1)
        assert rur.range_sum(0, 999) == 1000
        assert rur.range_sum(500, 599) == 100


# ── BIT2D ─────────────────────────────────────────────────────────────


class TestBIT2D:
    def test_single_point_add_prefix(self) -> None:
        bit2d = BIT2D(3, 4)
        bit2d.add(1, 2, 5)
        assert bit2d.prefix_sum(0, 0) == 0
        assert bit2d.prefix_sum(1, 2) == 5
        assert bit2d.prefix_sum(1, 0) == 0
        assert bit2d.prefix_sum(2, 3) == 5

    def test_multiple_adds_2d(self) -> None:
        bit2d = BIT2D(4, 4)
        bit2d.add(1, 1, 3)
        bit2d.add(2, 2, 4)
        bit2d.add(0, 0, 2)
        assert bit2d.prefix_sum(0, 0) == 2
        assert bit2d.prefix_sum(1, 1) == 5
        assert bit2d.prefix_sum(2, 2) == 9
        assert bit2d.prefix_sum(3, 3) == 9

    def test_rect_sum_random(self) -> None:
        bit2d = BIT2D(5, 5)
        for r in range(5):
            for c in range(5):
                bit2d.add(r, c, r * 10 + c)
        assert bit2d.rect_sum(1, 1, 3, 3) == 198
        assert bit2d.rect_sum(0, 0, 0, 4) == 10
        assert bit2d.rect_sum(0, 0, 4, 0) == 100

    def test_rect_sum_single_cell(self) -> None:
        bit2d = BIT2D(3, 3)
        bit2d.add(2, 2, 99)
        assert bit2d.rect_sum(2, 2, 2, 2) == 99

    def test_rect_sum_full_board(self) -> None:
        bit2d = BIT2D(2, 3)
        bit2d.add(0, 0, 1)
        bit2d.add(0, 2, 2)
        bit2d.add(1, 1, 3)
        assert bit2d.rect_sum(0, 0, 1, 2) == 6


# ── OrderStatisticTree ─────────────────────────────────────────────────


class TestOrderStatisticTree:
    def test_insert_and_count(self) -> None:
        ost = OrderStatisticTree(100)
        ost.insert(10)
        ost.insert(20)
        ost.insert(10)
        assert ost.count(10) == 2
        assert ost.count(20) == 1
        assert ost.count(0) == 0
        assert len(ost) == 3

    def test_remove_present_and_absent(self) -> None:
        ost = OrderStatisticTree(100)
        ost.insert(7)
        ost.insert(7)
        ost.insert(3)
        ost.remove(7)
        assert ost.count(7) == 1
        ost.remove(7)
        assert ost.count(7) == 0
        try:
            ost.remove(7)
            raise AssertionError("Expected KeyError")
        except KeyError:
            pass

    def test_count_less_than(self) -> None:
        ost = OrderStatisticTree(100)
        for v in [5, 1, 3, 8, 2]:
            ost.insert(v)
        assert ost.count_less_than(0) == 0
        assert ost.count_less_than(3) == 2
        assert ost.count_less_than(5) == 3
        assert ost.count_less_than(9) == 5

    def test_count_range(self) -> None:
        ost = OrderStatisticTree(100)
        for v in [2, 4, 6, 8, 10]:
            ost.insert(v)
        assert ost.count_range(3, 7) == 2
        assert ost.count_range(0, 100) == 5
        assert ost.count_range(20, 30) == 0

    def test_kth_element(self) -> None:
        ost = OrderStatisticTree(100)
        for v in [42, 17, 99, 3, 55]:
            ost.insert(v)
        assert ost.kth(0) == 3
        assert ost.kth(1) == 17
        assert ost.kth(2) == 42
        assert ost.kth(3) == 55
        assert ost.kth(4) == 99

    def test_kth_with_duplicates(self) -> None:
        ost = OrderStatisticTree(50)
        for v in [5, 5, 2, 8, 5, 2]:
            ost.insert(v)
        assert ost.kth(0) == 2
        assert ost.kth(1) == 2
        assert ost.kth(2) == 5
        assert ost.kth(3) == 5
        assert ost.kth(4) == 5
        assert ost.kth(5) == 8

    def test_kth_after_removal(self) -> None:
        ost = OrderStatisticTree(100)
        for v in [10, 20, 30, 40]:
            ost.insert(v)
        ost.remove(20)
        assert ost.kth(0) == 10
        assert ost.kth(1) == 30
        assert ost.kth(2) == 40

    def test_order_statistic_large_range(self) -> None:
        ost = OrderStatisticTree(50000)
        for i in range(1000):
            ost.insert(i * 2)
        assert len(ost) == 1000
        assert ost.kth(0) == 0
        assert ost.kth(999) == 1998
        assert ost.kth(500) == 1000
        assert ost.count_less_than(1000) == 500  # values 0,2,...,998 → 500 entries

    def test_out_of_range_insert(self) -> None:
        ost = OrderStatisticTree(10)
        try:
            ost.insert(-1)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass
        try:
            ost.insert(11)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass

    def test_out_of_range_kth(self) -> None:
        ost = OrderStatisticTree(50)
        ost.insert(5)
        try:
            ost.kth(1)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass
        try:
            ost.kth(-1)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass
