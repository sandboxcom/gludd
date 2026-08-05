"""Linear programming algorithms: tableau simplex, two-phase, dual simplex,
integer branch-and-bound with Gomory cuts, and transportation simplex.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import math
from typing import TypeAlias

Matrix: TypeAlias = list[list[float]]


# ── Tableau construction ───────────────────────────────────────────────


def _make_tableau(
    c: list[float],
    A: list[list[float]],
    b: list[float],
    senses: list[str],
) -> tuple[Matrix, list[int], list[int]]:
    """Build initial tableau for standard-form <= constraints.

    Returns (tableau, basic_vars, nonbasic_vars).

    Tableau layout (m rows + objective, n + m + 1 columns):
      Columns 0..n-1       = original variables
      Columns n..n+m-1     = slack variables
      Column  n+m          = RHS

    For each constraint: ax <= b  =>  ax + s = b  (s >= 0, slack)
    For each constraint: ax >= b  =>  ax - s = b  (s >= 0, surplus)
    For each constraint: ax = b   =>  artificial handled by two-phase
    """
    m = len(b)
    n = len(c)
    cols = n + m + 1
    tableau: Matrix = [[0.0] * cols for _ in range(m + 1)]

    for i in range(m):
        for j in range(n):
            tableau[i][j] = A[i][j]
        if senses[i] == "<=":
            tableau[i][n + i] = 1.0
        elif senses[i] == ">=":
            tableau[i][n + i] = -1.0
        tableau[i][cols - 1] = b[i]

    for j in range(n):
        tableau[m][j] = -c[j]

    basic: list[int] = [n + i for i in range(m)]
    nonbasic: list[int] = list(range(n))

    return tableau, basic, nonbasic


# ── Pivot ──────────────────────────────────────────────────────────────


def _pivot(
    tableau: Matrix,
    basic: list[int],
    nonbasic: list[int],
    pivot_row: int,
    pivot_col: int,
) -> None:
    """Perform one pivot operation in-place."""
    m = len(tableau) - 1
    cols = len(tableau[0])

    pivot_val = tableau[pivot_row][pivot_col]

    for j in range(cols):
        tableau[pivot_row][j] /= pivot_val

    for i in range(m + 1):
        if i == pivot_row:
            continue
        factor = tableau[i][pivot_col]
        if factor == 0:
            continue
        for j in range(cols):
            tableau[i][j] -= factor * tableau[pivot_row][j]

    leaving = basic[pivot_row]
    entering = nonbasic[pivot_col]
    basic[pivot_row] = entering
    nonbasic[pivot_col] = leaving


# ── Bland's rule ───────────────────────────────────────────────────────


def _bland_entering(tableau: Matrix, nonbasic: list[int], n_orig: int) -> int | None:
    """Find entering variable using Bland's rule: smallest-index with negative reduced cost."""
    m = len(tableau) - 1
    best_idx: int | None = None
    best_var: int = 10**9
    for j, v in enumerate(nonbasic):
        if tableau[m][j] < -1e-11 and v < best_var:
            best_var = v
            best_idx = j
    return best_idx


def _bland_leaving(tableau: Matrix, basic: list[int], pivot_col: int) -> int | None:
    """Find leaving row using Bland's rule (minimum ratio, tiebreak by basic var index)."""
    m = len(tableau) - 1
    cols = len(tableau[0])
    rhs_col = cols - 1
    best_row: int | None = None
    min_ratio = float("inf")
    best_var: int = 10**9
    for i in range(m):
        a = tableau[i][pivot_col]
        if a <= 1e-11:
            continue
        ratio = tableau[i][rhs_col] / a
        if ratio < min_ratio - 1e-11 or (abs(ratio - min_ratio) <= 1e-11 and basic[i] < best_var):
            min_ratio = ratio
            best_row = i
            best_var = basic[i]
    return best_row


# ── Standard simplex (maximization, <= constraints) ────────────────────


def simplex_max(c: list[float], A: list[list[float]], b: list[float]) -> tuple[float, list[float]]:
    """Solve: maximize c·x subject to Ax <= b, x >= 0.

    Returns (objective_value, solution_vector).

    Assumes all b[i] >= 0 (canonical form). Uses Bland's rule for anti-cycling.
    """
    m = len(b)
    n = len(c)
    senses = ["<="] * m
    tableau, basic, nonbasic = _make_tableau(c, A, b, senses)
    _simplex_iterate(tableau, basic, nonbasic, n)
    return _extract_solution(tableau, basic, n)


def _simplex_iterate(
    tableau: Matrix,
    basic: list[int],
    nonbasic: list[int],
    n_orig: int,
    max_iters: int = 2000,
) -> None:
    """Iterate simplex pivots until optimal or unbounded."""
    for _ in range(max_iters):
        pivot_col = _bland_entering(tableau, nonbasic, n_orig)
        if pivot_col is None:
            return
        pivot_row = _bland_leaving(tableau, basic, pivot_col)
        if pivot_row is None:
            raise ValueError("Unbounded solution — no valid pivot row found")
        _pivot(tableau, basic, nonbasic, pivot_row, pivot_col)
    raise RuntimeError("Simplex did not converge within iteration limit")


def _extract_solution(
    tableau: Matrix,
    basic: list[int],
    n_orig: int,
) -> tuple[float, list[float]]:
    """Extract objective value and solution vector from final tableau."""
    m = len(tableau) - 1
    cols = len(tableau[0])
    rhs_col = cols - 1

    x = [0.0] * n_orig
    for i in range(m):
        var = basic[i]
        if var < n_orig:
            x[var] = tableau[i][rhs_col]

    obj = tableau[m][rhs_col]
    return obj, x


# ── Two-phase simplex (handles >= and = constraints) ────────────────────


def simplex_two_phase(
    c: list[float],
    A: list[list[float]],
    b: list[float],
    senses: list[str],
) -> tuple[float, list[float]]:
    """Solve LP with arbitrary senses (<=, >=, =).

    Maximize c·x subject to constraints with given senses, x >= 0.
    Uses two-phase simplex: Phase I finds a feasible basis;
    Phase II optimizes the original objective.
    """
    m = len(b)
    n = len(c)

    artificial_rows: list[int] = []
    for i, s in enumerate(senses):
        if s in (">=", "="):
            artificial_rows.append(i)

    if not artificial_rows:
        return simplex_max(c, A, b)

    n_art = len(artificial_rows)
    total_cols = n + m + n_art + 1
    n_rows = m + 1

    phase1: Matrix = [[0.0] * total_cols for _ in range(n_rows)]

    art_var = n + m
    for i in range(m):
        for j in range(n):
            phase1[i][j] = A[i][j]
        if senses[i] == "<=":
            phase1[i][n + i] = 1.0
        elif senses[i] == ">=":
            phase1[i][n + i] = -1.0
        elif senses[i] == "=":
            pass
        if i in artificial_rows:
            phase1[i][art_var] = 1.0
            art_var += 1

        phase1[i][total_cols - 1] = b[i]

    basic_p1 = [n + i for i in range(m)]
    for idx, i in enumerate(artificial_rows):
        art_col = n + m + idx
        basic_p1[i] = art_col

    nonbasic_p1 = list(range(n)) + [n + i for i in range(m) if i not in artificial_rows]

    for i in artificial_rows:
        for j in range(total_cols):
            phase1[m][j] -= phase1[i][j]

    _simplex_iterate(phase1, basic_p1, nonbasic_p1, n)

    rhs_col = total_cols - 1
    if abs(phase1[m][rhs_col]) > 1e-10:
        raise ValueError("LP is infeasible — positive artificial objective value in Phase I")

    for i in range(m):
        if basic_p1[i] >= n + m:
            for j in range(n):
                if abs(phase1[i][j]) > 1e-11:
                    _pivot(phase1, basic_p1, nonbasic_p1, i, j)
                    break

    phase2 = [row[: n + m + 1] for row in phase1]
    for j in range(n):
        phase2[m][j] = -c[j]
    for j in range(n, n + m + 1):
        phase2[m][j] = 0.0

    for i in range(m):
        bv = basic_p1[i]
        if bv < n:
            coeff = phase2[m][bv]
            if coeff != 0:
                for j in range(n + m + 1):
                    phase2[m][j] -= coeff * phase2[i][j]

    basic_p2 = [v for v in basic_p1]
    nonbasic_p2 = [v for v in nonbasic_p1 if v < n + m]

    _simplex_iterate(phase2, basic_p2, nonbasic_p2, n)

    return _extract_solution(phase2, basic_p2, n)


# ── Gomory cutting plane (integer simplex) ─────────────────────────────


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

    obj, x = simplex_max(c, A, b)

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
            cut_A = [0.0] * n
            cut_A[var_idx] = 1.0
            cut_b = math.floor(val)

            A.append(cut_A)
            b.append(cut_b)

        obj, x = simplex_max(c, A, b)

    return obj, x


# ── Dual simplex ────────────────────────────────────────────────────────


def dual_simplex(
    c: list[float],
    A: list[list[float]],
    b: list[float],
) -> tuple[float, list[float]]:
    """Solve LP using dual simplex method.

    Maximize c·x subject to Ax <= b, x >= 0.
    Requires: all c[j] <= 0 (dual feasibility).
    """
    m = len(b)
    n = len(c)
    senses = ["<="] * m
    tableau, basic, nonbasic = _make_tableau(c, A, b, senses)
    cols = len(tableau[0])
    rhs_col = cols - 1

    for _ in range(2000):
        leave_row: int | None = None
        min_rhs = 1e-11
        for i in range(m):
            if tableau[i][rhs_col] < -1e-11 and tableau[i][rhs_col] < min_rhs:
                min_rhs = tableau[i][rhs_col]
                leave_row = i

        if leave_row is None:
            break

        enter_col: int | None = None
        best_ratio = float("inf")
        for j, _ in enumerate(nonbasic):
            a = tableau[leave_row][j]
            if a >= -1e-11:
                continue
            ratio = tableau[m][j] / (-a)
            if ratio < best_ratio - 1e-11:
                best_ratio = ratio
                enter_col = j

        if enter_col is None:
            raise ValueError("Dual unbounded / primal infeasible")

        _pivot(tableau, basic, nonbasic, leave_row, enter_col)

    return _extract_solution(tableau, basic, n)


# ── Transportation simplex ──────────────────────────────────────────────


def transportation_simplex(
    supply: list[float],
    demand: list[float],
    cost: list[list[float]],
) -> tuple[float, list[list[float]]]:
    """Solve the transportation problem using the transportation simplex.

    Minimize total cost of shipping from m suppliers (supply[i]) to n consumers
    (demand[j]) with per-unit costs cost[i][j].

    Returns (total_cost, shipment_matrix).
    """
    m = len(supply)
    n = len(demand)

    total_supply = sum(supply)
    total_demand = sum(demand)
    if total_supply != total_demand:
        raise ValueError(f"Total supply ({total_supply}) must equal total demand ({total_demand})")

    x = _northwest_corner(supply, demand, cost, m, n)
    u: list[float] = [0.0] * m
    v_list: list[float] = [0.0] * n

    for _ in range(2000):
        basis = [(i, j) for i in range(m) for j in range(n) if x[i][j] is not None]

        _compute_potentials(basis, cost, u, v_list, m, n)

        enter: tuple[int, int] | None = None
        best_reduced = -1e-11
        for i in range(m):
            for j in range(n):
                if x[i][j] is not None:
                    continue
                red = cost[i][j] - u[i] - v_list[j]
                if red < best_reduced:
                    best_reduced = red
                    enter = (i, j)

        if enter is None:
            break

        cycle = _find_cycle(basis, enter[0], enter[1], m, n)
        if cycle is None:
            continue

        leaving: tuple[int, int] | None = None
        min_val = float("inf")
        for k in range(1, len(cycle), 2):
            pi, pj = cycle[k]
            qty = x[pi][pj]
            if qty is not None and qty < min_val:
                min_val = qty
                leaving = (pi, pj)

        if min_val == 0:
            continue

        x[enter[0]][enter[1]] = 0.0
        for k, (ci, cj) in enumerate(cycle):
            if k % 2 == 0:
                x[ci][cj] = (x[ci][cj] or 0.0) + min_val
            else:
                x[ci][cj] = (x[ci][cj] or 0.0) - min_val

        if leaving is not None:
            li, lj = leaving
            v = x[li][lj]
            if v is not None and abs(v) < 1e-11:
                x[li][lj] = None

    total = 0.0
    result: list[list[float]] = []
    for i in range(m):
        row: list[float] = []
        for j in range(n):
            v = x[i][j]
            val: float = v if v is not None else 0.0
            row.append(val)
            total += cost[i][j] * val
        result.append(row)

    return total, result


def _northwest_corner(
    supply: list[float],
    demand: list[float],
    cost: list[list[float]],
    m: int,
    n: int,
) -> list[list[float | None]]:
    x: list[list[float | None]] = [[None] * n for _ in range(m)]
    s = list(supply)
    d = list(demand)
    i = j = 0
    while i < m and j < n:
        qty = min(s[i], d[j])
        x[i][j] = qty
        s[i] -= qty
        d[j] -= qty
        if s[i] <= 1e-11:
            i += 1
        elif d[j] <= 1e-11:
            j += 1
    return x


def _compute_potentials(
    basis: list[tuple[int, int]],
    cost: list[list[float]],
    u: list[float],
    v: list[float],
    m: int,
    n: int,
) -> None:
    visited = [False] * m
    visited_v = [False] * n
    u[0] = 0.0
    visited[0] = True

    changed = True
    while changed:
        changed = False
        for i, j in basis:
            if visited[i] and not visited_v[j]:
                v[j] = cost[i][j] - u[i]
                visited_v[j] = True
                changed = True
            elif visited_v[j] and not visited[i]:
                u[i] = cost[i][j] - v[j]
                visited[i] = True
                changed = True


def _find_cycle(
    basis: list[tuple[int, int]],
    enter_i: int,
    enter_j: int,
    m: int,
    n: int,
) -> list[tuple[int, int]] | None:
    graph: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, j in basis:
        graph[(i, j)] = []

    for i, j in basis:
        for i2, j2 in basis:
            if (i2, j2) == (i, j):
                continue
            if i2 == i or j2 == j:
                graph.setdefault((i, j), []).append((i2, j2))

    nodes = set(basis)

    def _dfs(
        current: tuple[int, int],
        target: tuple[int, int],
        path: list[tuple[int, int]],
        visited_set: set[tuple[int, int]],
    ) -> list[tuple[int, int]] | None:
        if len(path) > 1 and (current[0] == target[0] or current[1] == target[1]):
            return [*path, target]
        adjs = [
            (i2, j2)
            for (i2, j2) in nodes
            if (i2, j2) not in visited_set and (i2 == current[0] or j2 == current[1]) and (i2, j2) != current
        ]
        for adj in adjs:
            visited_set.add(adj)
            result = _dfs(adj, target, [*path, current], visited_set)
            if result is not None:
                return result
            visited_set.discard(adj)
        return None

    for start_node in nodes:
        if start_node[0] != enter_i and start_node[1] != enter_j:
            continue
        visited = {start_node}
        result = _dfs(start_node, (enter_i, enter_j), [start_node], visited)
        if result is not None and len(result) >= 3:
            result = [result[-1], *result[:-1]]
            return result

    return None


# ── Convenience ─────────────────────────────────────────────────────────


def simplex_min(c: list[float], A: list[list[float]], b: list[float]) -> tuple[float, list[float]]:
    """Solve: minimize c·x subject to Ax <= b, x >= 0.

    Converts to maximization by negating the objective.
    """
    neg_c = [-ci for ci in c]
    obj, x = simplex_max(neg_c, A, b)
    return -obj, x
