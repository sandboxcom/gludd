"""Deep tests for ZDD — union, intersection, difference, count, enumerate."""

from __future__ import annotations

from general_ludd.algorithms.zdd import (
    zdd_base,
    zdd_count,
    zdd_diff,
    zdd_empty,
    zdd_enumerate,
    zdd_int,
    zdd_powerset,
    zdd_union,
    zdd_unit,
)


class TestZDDTerminals:
    def test_empty_is_bottom(self) -> None:
        e = zdd_empty()
        assert e.idx == -2
        assert zdd_count(e) == 0

    def test_base_is_top(self) -> None:
        b = zdd_base()
        assert b.idx == -1
        assert zdd_count(b) == 1

    def test_empty_enumerate_gives_empty_list(self) -> None:
        assert zdd_enumerate(zdd_empty()) == []

    def test_base_enumerate_gives_empty_set(self) -> None:
        assert zdd_enumerate(zdd_base()) == [frozenset[int]()]


class TestZDDUnit:
    def test_unit_count_is_one(self) -> None:
        assert zdd_count(zdd_unit(3)) == 1

    def test_unit_enumerate_has_singleton(self) -> None:
        assert zdd_enumerate(zdd_unit(7)) == [frozenset({7})]

    def test_unit_variable_is_non_negative(self) -> None:
        u = zdd_unit(0)
        assert zdd_count(u) == 1
        assert zdd_enumerate(u) == [frozenset({0})]


class TestZDDUnion:
    def test_empty_union_x_equals_x(self) -> None:
        x = zdd_unit(1)
        assert zdd_union(zdd_empty(), x) is x
        assert zdd_union(x, zdd_empty()) is x

    def test_base_union_unit_adds_singleton(self) -> None:
        result = zdd_union(zdd_base(), zdd_unit(1))
        assert zdd_count(result) == 2
        sets = zdd_enumerate(result)
        assert frozenset() in sets
        assert frozenset({1}) in sets

    def test_union_of_same_returns_same(self) -> None:
        x = zdd_unit(5)
        assert zdd_union(x, x) is x

    def test_union_two_units_same_var(self) -> None:
        result = zdd_union(zdd_unit(3), zdd_unit(3))
        assert zdd_count(result) == 1
        assert zdd_enumerate(result) == [frozenset({3})]

    def test_union_two_disjoint_units(self) -> None:
        result = zdd_union(zdd_unit(1), zdd_unit(2))
        assert zdd_count(result) == 2
        sets = zdd_enumerate(result)
        assert frozenset({1}) in sets
        assert frozenset({2}) in sets

    def test_union_three_units_gives_three_singletons(self) -> None:
        u = zdd_empty()
        for i in range(3):
            u = zdd_union(u, zdd_unit(i))
        assert zdd_count(u) == 3
        sets = zdd_enumerate(u)
        assert len(sets) == 3
        assert frozenset({0}) in sets
        assert frozenset({1}) in sets
        assert frozenset({2}) in sets

    def test_base_and_n_units_gives_n_plus_one(self) -> None:
        u = zdd_base()
        for i in range(5):
            u = zdd_union(u, zdd_unit(i))
        assert zdd_count(u) == 6
        assert frozenset() in zdd_enumerate(u)

    def test_union_of_two_triples(self) -> None:
        a = zdd_empty()
        b = zdd_empty()
        for i in range(3):
            a = zdd_union(a, zdd_unit(i))
        for i in range(2, 5):
            b = zdd_union(b, zdd_unit(i))
        result = zdd_union(a, b)
        assert zdd_count(result) == 5


class TestZDDPowerset:
    def test_powerset_of_empty_is_base(self) -> None:
        ps = zdd_powerset([])
        assert zdd_count(ps) == 1
        assert zdd_enumerate(ps) == [frozenset[int]()]

    def test_powerset_of_one_var(self) -> None:
        ps = zdd_powerset([0])
        assert zdd_count(ps) == 2
        sets = zdd_enumerate(ps)
        assert frozenset() in sets
        assert frozenset({0}) in sets

    def test_powerset_of_three_vars(self) -> None:
        ps = zdd_powerset([0, 1, 2])
        assert zdd_count(ps) == 1 << 3
        assert len(zdd_enumerate(ps)) == 8

    def test_powerset_of_12_variables(self) -> None:
        ps = zdd_powerset(list(range(12)))
        assert zdd_count(ps) == 1 << 12
        assert len(zdd_enumerate(ps)) == 4096

    def test_powerset_variable_order_does_not_affect_family(self) -> None:
        ps1 = zdd_powerset([2, 1, 0])
        ps2 = zdd_powerset([0, 1, 2])
        assert zdd_count(ps1) == zdd_count(ps2)
        assert set(zdd_enumerate(ps1)) == set(zdd_enumerate(ps2))


class TestZDDIntersection:
    def test_int_with_empty_returns_empty(self) -> None:
        x = zdd_unit(1)
        assert zdd_int(x, zdd_empty()) is zdd_empty()
        assert zdd_int(zdd_empty(), x) is zdd_empty()

    def test_int_of_same_returns_same(self) -> None:
        x = zdd_unit(5)
        assert zdd_int(x, x) is x

    def test_int_base_with_unit_returns_empty(self) -> None:
        result = zdd_int(zdd_base(), zdd_unit(1))
        assert zdd_count(result) == 0

    def test_int_base_with_base_returns_base(self) -> None:
        result = zdd_int(zdd_base(), zdd_base())
        assert zdd_count(result) == 1

    def test_int_two_disjoint_units_returns_empty(self) -> None:
        assert zdd_count(zdd_int(zdd_unit(1), zdd_unit(2))) == 0

    def test_int_two_same_var_units(self) -> None:
        result = zdd_int(zdd_unit(3), zdd_unit(3))
        assert zdd_count(result) == 1
        assert zdd_enumerate(result) == [frozenset({3})]

    def test_int_of_overlapping_powersets(self) -> None:
        a = zdd_powerset([0, 1, 2, 3])
        b = zdd_powerset([2, 3, 4, 5])
        result = zdd_int(a, b)
        assert zdd_count(result) == 1 << 2

    def test_int_with_unit_not_in_powerset(self) -> None:
        ps = zdd_powerset([0, 1, 2])
        u = zdd_unit(9)
        assert zdd_count(zdd_int(ps, u)) == 0

    def test_int_powerset_with_self_is_self(self) -> None:
        ps = zdd_powerset([0, 1, 2, 3])
        result = zdd_int(ps, ps)
        assert result is ps
        assert zdd_count(result) == 16


class TestZDDDifference:
    def test_diff_x_minus_empty_equals_x(self) -> None:
        x = zdd_unit(1)
        assert zdd_diff(x, zdd_empty()) is x

    def test_diff_x_minus_x_is_empty(self) -> None:
        assert zdd_diff(zdd_unit(5), zdd_unit(5)) is zdd_empty()

    def test_diff_empty_minus_x_is_empty(self) -> None:
        assert zdd_diff(zdd_empty(), zdd_unit(1)) is zdd_empty()

    def test_diff_disjoint_units(self) -> None:
        result = zdd_diff(zdd_unit(1), zdd_unit(2))
        assert zdd_count(result) == 1
        assert zdd_enumerate(result) == [frozenset({1})]

    def test_diff_base_minus_base_is_empty(self) -> None:
        assert zdd_diff(zdd_base(), zdd_base()) is zdd_empty()

    def test_diff_base_minus_unit_keeps_empty_set(self) -> None:
        result = zdd_diff(zdd_base(), zdd_unit(1))
        assert zdd_count(result) == 1
        assert zdd_enumerate(result) == [frozenset[int]()]

    def test_diff_powerset_minus_smaller_powerset(self) -> None:
        full = zdd_powerset([0, 1, 2, 3])
        sub = zdd_powerset([2, 3])
        result = zdd_diff(full, sub)
        assert zdd_count(result) == (1 << 4) - (1 << 2)

    def test_diff_non_overlapping_powersets(self) -> None:
        a = zdd_powerset([0, 1])
        b = zdd_powerset([2, 3])
        result = zdd_diff(a, b)
        assert zdd_count(result) == zdd_count(a) - 1


class TestZDDCombineOperations:
    def test_union_then_int(self) -> None:
        a = zdd_powerset([0, 1, 2])
        b = zdd_powerset([2, 3])
        zdd_union(a, b)
        inter = zdd_int(a, b)
        assert zdd_count(inter) == 1 << 1

    def test_diff_after_union(self) -> None:
        a = zdd_powerset([0, 1, 2, 3])
        b = zdd_powerset([2, 3])
        result = zdd_diff(a, b)
        assert zdd_count(result) == (1 << 4) - (1 << 2)

    def test_int_after_diff(self) -> None:
        full = zdd_powerset([0, 1, 2, 3, 4, 5])
        minus = zdd_diff(full, zdd_powerset([0]))
        sub = zdd_powerset([1, 2, 3, 4, 5])
        inter = zdd_int(minus, sub)
        assert zdd_count(inter) == 31

    def test_four_powerset_intersection(self) -> None:
        a = zdd_powerset([0, 1, 2, 3])
        b = zdd_powerset([2, 3, 4, 5])
        c = zdd_powerset([1, 3, 5, 7])
        d = zdd_powerset([0, 3, 6])
        result = a
        for other in (b, c, d):
            result = zdd_int(result, other)
        assert zdd_count(result) == 1 << 1


class TestZDDCountConsistency:
    def test_count_matches_enumerate_length(self) -> None:
        u = zdd_powerset([0, 1, 2, 3, 4, 5])
        for _ in range(3):
            a = zdd_diff(u, zdd_powerset([1]))
            b = zdd_int(a, zdd_powerset([0, 2, 3, 4, 5]))
            assert zdd_count(b) == len(zdd_enumerate(b))

    def test_union_count_equals_sum_minus_overlap(self) -> None:
        a = zdd_powerset([0, 1])
        b = zdd_powerset([1, 2])
        inter_count = zdd_count(zdd_int(a, b))
        union_count = zdd_count(zdd_union(a, b))
        expected = zdd_count(a) + zdd_count(b) - inter_count
        assert union_count == expected


class TestZDDEnumerateDeep:
    def test_large_powerset_enumerate(self) -> None:
        ps = zdd_powerset(list(range(10)))
        sets = zdd_enumerate(ps)
        assert len(sets) == 1 << 10
        assert frozenset[int]() in sets
        seen = set(sets)
        assert len(seen) == len(sets)

    def test_enumerate_union_has_empty_set(self) -> None:
        u = zdd_base()
        for i in range(4):
            u = zdd_union(u, zdd_unit(i))
        assert frozenset[int]() in zdd_enumerate(u)

    def test_enumerate_all_subsets_are_correct(self) -> None:
        ps = zdd_powerset([0, 1, 2, 3, 4])
        sets = zdd_enumerate(ps)
        for s in sets:
            for v in s:
                assert 0 <= v < 5

    def test_enumerate_diff_result(self) -> None:
        full = zdd_powerset([0, 1, 2, 3])
        remove = zdd_powerset([1, 2])
        result = zdd_diff(full, remove)
        sets = zdd_enumerate(result)
        assert frozenset({1, 2}) not in sets
        assert frozenset({1}) not in sets
        assert frozenset({2}) not in sets
        assert zdd_count(result) == 12
