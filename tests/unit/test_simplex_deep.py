"""Deep tests for simplex-family algorithms wrapping scipy.optimize.linprog."""

from __future__ import annotations

import math

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils.simplex import (
    dual_simplex,
    integer_simplex,
    simplex_max,
    simplex_min,
    simplex_two_phase,
    transportation_simplex,
)

# ── simplex_max (standard maximization) ─────────────────────────────────


class TestSimplexMax:
    def test_trivial(self) -> None:
        obj, x = simplex_max([3.0, 5.0], [[1.0, 0.0], [0.0, 2.0]], [4.0, 12.0])
        assert obj == pytest.approx(42.0)
        assert x[0] == pytest.approx(4.0)
        assert x[1] == pytest.approx(6.0)

    def test_zero_objective(self) -> None:
        obj, _x = simplex_max([0.0, 0.0], [[1.0, 1.0]], [5.0])
        assert obj == pytest.approx(0.0)

    def test_single_variable(self) -> None:
        obj, x = simplex_max([7.0], [[1.0]], [10.0])
        assert obj == pytest.approx(70.0)
        assert x[0] == pytest.approx(10.0)

    def test_inactive_constraint(self) -> None:
        obj, x = simplex_max([2.0, 3.0], [[1.0, 0.0], [0.0, 1.0]], [100.0, 5.0])
        assert obj == pytest.approx(215.0)
        assert x[0] == pytest.approx(100.0)
        assert x[1] == pytest.approx(5.0)

    def test_multiple_constraints(self) -> None:
        obj, x = simplex_max(
            [3.0, 2.0],
            [[1.0, 1.0], [1.0, 0.0], [0.0, 1.0]],
            [4.0, 2.0, 3.0],
        )
        assert obj == pytest.approx(10.0)
        assert x[0] == pytest.approx(2.0)
        assert x[1] == pytest.approx(2.0)

    def test_degenerate_basic(self) -> None:
        obj, _x = simplex_max([2.0, 1.0], [[1.0, 1.0], [1.0, 1.0]], [4.0, 4.0])
        assert obj == pytest.approx(8.0)

    def test_unbounded(self) -> None:
        with pytest.raises(ValueError, match="Unbounded"):
            simplex_max([1.0, 1.0], [[0.0, -1.0], [-1.0, 0.0]], [1.0, 1.0])


# ── simplex_min (converted maximization) ────────────────────────────────


class TestSimplexMin:
    def test_standard_min(self) -> None:
        obj, x = simplex_min([2.0, 1.0], [[1.0, 0.0], [0.0, 1.0]], [3.0, 4.0])
        assert obj == pytest.approx(0.0)
        assert x[0] == pytest.approx(0.0)
        assert x[1] == pytest.approx(0.0)

    def test_min_with_active_constraint(self) -> None:
        obj, x = simplex_min([1.0, 2.0], [[1.0, 1.0], [1.0, 0.0]], [3.0, 2.0])
        assert obj == pytest.approx(0.0)
        assert x[0] == pytest.approx(0.0)
        assert x[1] == pytest.approx(0.0)


# ── simplex_two_phase (>= and = constraints) ───────────────────────────


class TestSimplexTwoPhase:
    def test_equality_constraint(self) -> None:
        obj, x = simplex_two_phase(
            [3.0, 2.0],
            [[1.0, 1.0], [2.0, 1.0]],
            [4.0, 5.0],
            ["=", ">="],
        )
        assert obj == pytest.approx(12.0)
        assert x[0] == pytest.approx(4.0)
        assert x[1] == pytest.approx(0.0)

    def test_bounded_mixed_senses(self) -> None:
        obj, x = simplex_two_phase(
            [2.0, 3.0],
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            [1.0, 1.0, 5.0],
            [">=", ">=", "<="],
        )
        assert obj == pytest.approx(14.0)
        assert x[0] == pytest.approx(1.0)
        assert x[1] == pytest.approx(4.0)

    def test_unbounded_lower_only(self) -> None:
        with pytest.raises(ValueError, match="Unbounded"):
            simplex_two_phase(
                [1.0, 1.0],
                [[1.0, 1.0]],
                [10.0],
                [">="],
            )

    def test_infeasible(self) -> None:
        with pytest.raises(ValueError, match="infeasible"):
            simplex_two_phase(
                [1.0],
                [[1.0], [1.0]],
                [5.0, 3.0],
                [">=", "<="],
            )

    def test_feasible_greater_than(self) -> None:
        obj, x = simplex_two_phase(
            [1.0, 1.0],
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]],
            [2.0, 2.0, 6.0, 5.0, 5.0],
            [">=", ">=", ">=", "<=", "<="],
        )
        assert obj == pytest.approx(10.0)
        assert x[0] == pytest.approx(5.0)
        assert x[1] == pytest.approx(5.0)


# ── integer_simplex ────────────────────────────────────────────────────


class TestIntegerSimplex:
    def test_fractional_cut(self) -> None:
        _obj, x = integer_simplex(
            [1.0, 1.0],
            [[3.0, 2.0], [1.0, 3.0]],
            [6.0, 6.0],
        )
        for j in range(len(x)):
            frac = x[j] - math.floor(x[j])
            assert frac == pytest.approx(0.0, abs=1e-9) or (1.0 - frac) == pytest.approx(0.0, abs=1e-9)

    def test_already_integral(self) -> None:
        _obj, x = integer_simplex(
            [2.0, 1.0],
            [[1.0, 0.0], [0.0, 1.0]],
            [5.0, 3.0],
        )
        assert x[0] == pytest.approx(5.0)
        assert x[1] == pytest.approx(3.0)

    def test_single_integer_var(self) -> None:
        obj, x = integer_simplex(
            [1.0, 2.0],
            [[1.0, 1.0], [2.0, 1.0]],
            [4.5, 6.5],
            int_vars=[0],
        )
        assert x[0] == pytest.approx(int(x[0]))
        assert obj > 0

    def test_no_int_vars(self) -> None:
        obj, _x = integer_simplex([1.0, 1.0], [[1.0, 1.0]], [3.0], int_vars=[])
        assert obj == pytest.approx(3.0)


# ── dual_simplex ───────────────────────────────────────────────────────


class TestDualSimplex:
    def test_dual_feasible(self) -> None:
        obj, _x = dual_simplex(
            [-1.0, -1.0],
            [[1.0, 2.0], [1.0, -1.0]],
            [4.0, 1.0],
        )
        assert obj > -1e-9

    def test_dual_optimal_zero(self) -> None:
        obj, _x = dual_simplex(
            [0.0, 0.0],
            [[1.0, 0.0], [0.0, 1.0]],
            [5.0, 3.0],
        )
        assert obj == pytest.approx(0.0)


# ── transportation_simplex ─────────────────────────────────────────────


class TestTransportationSimplex:
    def test_balanced_2x2(self) -> None:
        total, plan = transportation_simplex(
            [20.0, 30.0],
            [10.0, 40.0],
            [[4.0, 6.0], [8.0, 7.0]],
        )
        assert total > 0
        for i, row in enumerate(plan):
            assert sum(row) == pytest.approx([20.0, 30.0][i])

    def test_balanced_3x3(self) -> None:
        supply = [50.0, 70.0, 30.0]
        demand = [30.0, 60.0, 60.0]
        cost = [[10.0, 12.0, 8.0], [9.0, 7.0, 6.0], [15.0, 11.0, 13.0]]
        total, plan = transportation_simplex(supply, demand, cost)
        assert total > 0
        for i in range(3):
            assert sum(plan[i]) == pytest.approx(supply[i])
        for j in range(3):
            col_sum = sum(plan[i][j] for i in range(3))
            assert col_sum == pytest.approx(demand[j])

    def test_unbalanced_raises(self) -> None:
        with pytest.raises(ValueError, match="must equal"):
            transportation_simplex(
                [10.0, 20.0],
                [5.0, 10.0],
                [[1.0, 2.0], [3.0, 4.0]],
            )

    def test_single_supplier(self) -> None:
        _total, plan = transportation_simplex(
            [100.0],
            [40.0, 60.0],
            [[5.0, 7.0]],
        )
        assert plan[0][0] == pytest.approx(40.0)
        assert plan[0][1] == pytest.approx(60.0)

    def test_zero_cost(self) -> None:
        total, _plan = transportation_simplex(
            [5.0, 5.0],
            [5.0, 5.0],
            [[0.0, 0.0], [0.0, 0.0]],
        )
        assert total == pytest.approx(0.0)


# ── Edge cases ─────────────────────────────────────────────────────────


class TestSimplexEdgeCases:
    def test_high_precision(self) -> None:
        c = [1e10, 1.0]
        A = [[1.0, 0.0], [0.0, 1.0]]
        b = [1000.0, 1000.0]
        obj, x = simplex_max(c, A, b)
        assert obj == pytest.approx(1e10 * 1000 + 1000.0)
        assert x[0] == pytest.approx(1000.0)
        assert x[1] == pytest.approx(1000.0)

    def test_large_scale(self) -> None:
        n = 50
        c = [1.0] * n
        A = [[float(i == j) for j in range(n)] for i in range(n)]
        b = [1.0] * n
        obj, _x = simplex_max(c, A, b)
        assert obj == pytest.approx(n * 1.0)
