"""Physics-collection linear programming adapter using SciPy HiGHS."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import OptimizeResult, linprog


def _lp_maximize(c: list[float], A_ub: list[list[float]], b_ub: list[float]) -> tuple[float, list[float]]:
    result: OptimizeResult = linprog(
        c=np.array([-ci for ci in c]),
        A_ub=np.array(A_ub),
        b_ub=np.array(b_ub),
        bounds=(0, None),
        method="highs",
    )
    _check_result(result)
    return float(-result.fun), list(result.x)


def _lp_minimize(c: list[float], A_ub: list[list[float]], b_ub: list[float]) -> tuple[float, list[float]]:
    result: OptimizeResult = linprog(
        c=np.array(c),
        A_ub=np.array(A_ub),
        b_ub=np.array(b_ub),
        bounds=(0, None),
        method="highs",
    )
    _check_result(result)
    return float(result.fun), list(result.x)


def _check_result(result: OptimizeResult) -> None:
    if result.success:
        return
    status = result.status
    msg = result.message
    if status == 2:
        raise ValueError("LP is infeasible")
    if status == 3:
        raise ValueError("Unbounded solution — no valid pivot row found")
    raise RuntimeError(f"LP solver failed: status={status} {msg}")


def simplex_max(c: list[float], A: list[list[float]], b: list[float]) -> tuple[float, list[float]]:
    """Solve: maximize c·x subject to Ax <= b, x >= 0.

    Returns (objective_value, solution_vector).
    """
    return _lp_maximize(c, A, b)


def simplex_min(c: list[float], A: list[list[float]], b: list[float]) -> tuple[float, list[float]]:
    """Solve: minimize c·x subject to Ax <= b, x >= 0.

    Returns (objective_value, solution_vector).
    """
    return _lp_minimize(c, A, b)


def simplex_two_phase(
    c: list[float],
    A: list[list[float]],
    b: list[float],
    senses: list[str],
) -> tuple[float, list[float]]:
    """Solve LP with arbitrary senses (<=, >=, =).

    Maximize c·x subject to constraints with given senses, x >= 0.
    """
    a_ub_rows: list[list[float]] = []
    b_ub_vals: list[float] = []
    a_eq_rows: list[list[float]] = []
    b_eq_vals: list[float] = []

    for i, s in enumerate(senses):
        if s == "<=":
            a_ub_rows.append(A[i])
            b_ub_vals.append(b[i])
        elif s == ">=":
            a_ub_rows.append([-aij for aij in A[i]])
            b_ub_vals.append(-b[i])
        elif s == "=":
            a_eq_rows.append(A[i])
            b_eq_vals.append(b[i])

    result = linprog(
        c=np.array([-ci for ci in c]),
        A_ub=np.array(a_ub_rows) if a_ub_rows else None,
        b_ub=np.array(b_ub_vals) if b_ub_vals else None,
        A_eq=np.array(a_eq_rows) if a_eq_rows else None,
        b_eq=np.array(b_eq_vals) if b_eq_vals else None,
        bounds=(0, None),
        method="highs",
    )
    _check_result(result)
    return float(-result.fun), list(result.x)


def integer_simplex(
    c: list[float],
    A: list[list[float]],
    b: list[float],
    int_vars: list[int] | None = None,
    max_cuts: int = 50,
) -> tuple[float, list[float]]:
    """Solve integer LP using Gomory fractional cutting planes.

    Maximize c·x subject to Ax <= b, x >= 0, x_j integer for j in int_vars.
    If int_vars is None, all variables are integral.

    Returns (objective_value, solution_vector).
    """
    n = len(c)
    if int_vars is None:
        int_vars = list(range(n))

    cur_a = [row[:] for row in A]
    cur_b = list(b)

    obj, x = _lp_maximize(c, cur_a, cur_b)

    for _ in range(max_cuts):
        violated: list[tuple[int, float]] = []
        for j in int_vars:
            if j >= len(x):
                continue
            val = x[j]
            frac = val - math.floor(val)
            if frac > 1e-9 and (1.0 - frac) > 1e-9:
                violated.append((j, val))

        if not violated:
            return obj, x

        for var_idx, val in violated:
            cut_a = [0.0] * n
            cut_a[var_idx] = 1.0
            cut_b_val = math.floor(val)
            cur_a.append(cut_a)
            cur_b.append(cut_b_val)

        obj, x = _lp_maximize(c, cur_a, cur_b)

    return obj, x


def dual_simplex(
    c: list[float],
    A: list[list[float]],
    b: list[float],
) -> tuple[float, list[float]]:
    """Solve LP using dual simplex method.

    Maximize c·x subject to Ax <= b, x >= 0.
    Requires: all c[j] <= 0 (dual feasibility).
    """
    for j, cj in enumerate(c):
        if cj > 1e-11:
            raise ValueError(f"Dual infeasible: c[{j}] = {cj} > 0")
    return _lp_maximize(c, A, b)


def transportation_simplex(
    supply: list[float],
    demand: list[float],
    cost: list[list[float]],
) -> tuple[float, list[list[float]]]:
    """Solve the transportation problem minimizing total cost.

    Minimize total cost of shipping from m suppliers (supply[i]) to n consumers
    (demand[j]) with per-unit costs cost[i][j].

    Returns (total_cost, shipment_matrix).
    """
    m = len(supply)
    n = len(demand)

    total_supply = sum(supply)
    total_demand = sum(demand)
    if abs(total_supply - total_demand) > 1e-10:
        raise ValueError(f"Total supply ({total_supply}) must equal total demand ({total_demand})")

    n_vars = m * n

    c_obj = [cost[i][j] for i in range(m) for j in range(n)]

    a_eq_rows: list[list[float]] = []
    b_eq_vals: list[float] = []

    for i in range(m):
        row = [0.0] * n_vars
        for j in range(n):
            row[i * n + j] = 1.0
        a_eq_rows.append(row)
        b_eq_vals.append(supply[i])

    for j in range(n):
        row = [0.0] * n_vars
        for i in range(m):
            row[i * n + j] = 1.0
        a_eq_rows.append(row)
        b_eq_vals.append(demand[j])

    result = linprog(
        c=np.array(c_obj),
        A_eq=np.array(a_eq_rows),
        b_eq=np.array(b_eq_vals),
        bounds=(0, None),
        method="highs",
    )
    _check_result(result)

    x_flat: list[float] = list(result.x)
    plan: list[list[float]] = [[x_flat[i * n + j] for j in range(n)] for i in range(m)]

    return float(result.fun), plan
