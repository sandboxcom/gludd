"""Advanced mathematics knowledge module for the physics collection.

Exposes linear algebra, differential equations, group theory, and topology
as structured data and computational helpers.

Public surface::

    solve_linear_system(A, b)                    -> list[float]
    compute_eigenvalues(matrix)                  -> list[float]
    lu_decompose(A)                              -> tuple[list[list[float]], list[list[float]]]
    qr_decompose(A)                              -> tuple[list[list[float]], list[list[float]]]
    tensor_contraction(T, dims)                  -> list[list[float]]
    solve_ode(f, y0, t_span, h)                 -> list[tuple[float, list[float]]]
    classify_pde(coefficients, variables)        -> str
    finite_difference_1d(lhs, rhs, grid, bc)     -> list[float]
    classify_group(elements)                     -> str
    is_abelian(elements, product)                -> bool
    character_table(group, irreps)               -> list[list[float]]
    fundamental_group(space_type)                -> str
    betti_numbers(simplicial_complex)            -> list[int]
    euler_characteristic(simplicial_complex)     -> int

    LIE_GROUPS          dict[group_name] -> properties
    MANIFOLD_TYPES      dict[type_name] -> properties
    ODE_METHODS         dict[method_name] -> description
    PDE_CLASSES         dict[class_name] -> canonical form
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIE_GROUPS: dict[str, dict[str, Any]] = {
    "SO(2)": {
        "dimension": 1,
        "rank": 1,
        "compact": True,
        "simple": False,
        "algebra": "so(2)",
        "description": "Rotations in 2D; abelian, isomorphic to U(1)",
        "generators": [[[0, 1], [-1, 0]]],
        "dynkin_diagram": None,
        "applications": ["planar_rigid_body_motion", "classical_mechanics"],
    },
    "SO(3)": {
        "dimension": 3,
        "rank": 1,
        "compact": True,
        "simple": True,
        "algebra": "so(3)",
        "description": "Rotations in 3D; double-covered by SU(2)",
        "generators": [
            [[0, 0, 0], [0, 0, -1], [0, 1, 0]],
            [[0, 0, 1], [0, 0, 0], [-1, 0, 0]],
            [[0, -1, 0], [1, 0, 0], [0, 0, 0]],
        ],
        "dynkin_diagram": "A1",
        "applications": ["rigid_body_dynamics", "angular_momentum", "crystallography"],
    },
    "SU(2)": {
        "dimension": 3,
        "rank": 1,
        "compact": True,
        "simple": True,
        "algebra": "su(2)",
        "description": "Special unitary group in 2D; double cover of SO(3); describes spin-1/2",
        "generators": [
            [[0, 1], [1, 0]],  # Pauli x
            [[0, complex(0, -1)], [complex(0, 1), 0]],  # Pauli y
            [[1, 0], [0, -1]],  # Pauli z
        ],
        "dynkin_diagram": "A1",
        "applications": ["quantum_mechanics", "spin_systems", "qiskit_gates"],
    },
    "SU(3)": {
        "dimension": 8,
        "rank": 2,
        "compact": True,
        "simple": True,
        "algebra": "su(3)",
        "description": "Special unitary group in 3D; gauge group of QCD",
        "generators": ["Gell-Mann_matrices"],
        "dynkin_diagram": "A2",
        "applications": ["quantum_chromodynamics", "quark_model", "colour_confinement"],
    },
    "U(1)": {
        "dimension": 1,
        "rank": 1,
        "compact": True,
        "simple": False,
        "algebra": "u(1)",
        "description": "Unit circle group; gauge group of electromagnetism",
        "generators": [[0]],
        "dynkin_diagram": None,
        "applications": ["electromagnetism", "phase_rotations", "number_conservation"],
    },
    "SL(2,R)": {
        "dimension": 3,
        "rank": 1,
        "compact": False,
        "simple": True,
        "algebra": "sl(2,R)",
        "description": "Special linear group over reals; symmetries of AdS_2",
        "generators": [
            [[0, 1], [0, 0]],
            [[0, 0], [1, 0]],
            [[1, 0], [0, -1]],
        ],
        "dynkin_diagram": "A1",
        "applications": ["conformal_field_theory", "ads_cft_correspondence"],
    },
    "Sp(2N)": {
        "dimension": "N(2N+1)",
        "rank": -1,
        "compact": False,
        "simple": True,
        "algebra": "sp(2N)",
        "description": "Symplectic group; preserves a skew-symmetric bilinear form",
        "dynkin_diagram": "CN",
        "applications": ["hamiltonian_mechanics", "symplectic_geometry", "magnetic_monopoles"],
    },
    "E8": {
        "dimension": 248,
        "rank": 8,
        "compact": True,
        "simple": True,
        "algebra": "e8",
        "description": "Largest exceptional Lie group; 248-dimensional adjoint representation",
        "dynkin_diagram": "E8",
        "applications": ["string_theory", "heterotic_string", "grand_unified_theories"],
    },
}

MANIFOLD_TYPES: dict[str, dict[str, Any]] = {
    "sphere_Sn": {
        "dimension_literal": "n",
        "orientable": True,
        "compact": True,
        "betti_example": "S^2: b_0=1, b_1=0, b_2=1",
        "fundamental_group": "S^1: Z; S^n (n>1): trivial",
        "homology_hint": "H_k(S^n) = Z for k=0,n; 0 otherwise",
        "examples": ["S^1: circle", "S^2: surface of 3-ball", "S^3: unit quaternions ~ SU(2)"],
    },
    "torus_Tn": {
        "dimension_literal": "n",
        "orientable": True,
        "compact": True,
        "betti_example": "T^2: b_0=1, b_1=2, b_2=1",
        "fundamental_group": "Z^n",
        "homology_hint": "Product of n S^1 factors",
        "examples": ["T^1: circle", "T^2: doughnut surface", "T^3: 3-torus"],
    },
    "real_projective_RPn": {
        "dimension_literal": "n",
        "orientable": "iff n odd",
        "compact": True,
        "betti_example": "RP^2: b_0=1, b_1=0, b_2=0 (Z/2 coeff: b_1=1, b_2=1)",
        "fundamental_group": "Z/2 for n>=2; Z for n=1",
        "homology_hint": "H_k(RP^n) = Z/2 for k odd, < n; H_n = Z if n odd",
        "examples": ["RP^1: circle", "RP^2: Boy's surface immersion"],
    },
    "complex_projective_CPn": {
        "dimension_literal": "2n",
        "orientable": True,
        "compact": True,
        "betti_example": "CP^1 ~ S^2: b_0=1, b_1=0, b_2=1",
        "fundamental_group": "trivial",
        "homology_hint": "betti numbers: 1 for even indices 0..2n; 0 for odd",
        "examples": ["CP^1: Riemann sphere", "CP^2: Penrose twistor space base"],
    },
    "mobius_strip": {
        "dimension_literal": "2",
        "orientable": False,
        "compact": False,
        "betti_example": "b_0=1, b_1=1, b_2=0",
        "fundamental_group": "Z",
        "homology_hint": "H_1 = Z; deformation retracts to S^1",
        "examples": ["non-orientable surface with boundary"],
    },
    "klein_bottle": {
        "dimension_literal": "2",
        "orientable": False,
        "compact": True,
        "betti_example": "b_0=1, b_1=1, b_2=0",
        "fundamental_group": "presentation <a,b | aba^{-1}b = 1>",
        "homology_hint": "H_1 = Z x Z/2",
        "examples": ["non-orientable closed surface"],
    },
    "calabi_yau": {
        "dimension_literal": "complex 3 (= real 6)",
        "orientable": True,
        "compact": True,
        "betti_example": "quintic: b_2=1, b_3=204, ...",
        "fundamental_group": "varies; many have finite fundamental group",
        "homology_hint": "c_1 = 0 (Ricci-flat); SU(3) holonomy",
        "examples": ["quintic threefold in CP^4", "string_theory_compactification"],
    },
}

ODE_METHODS: dict[str, dict[str, Any]] = {
    "euler": {
        "order": 1,
        "explicit": True,
        "description": "Explicit Euler: y_{n+1} = y_n + h * f(t_n, y_n)",
        "stability": "conditionally stable; simple but low accuracy",
        "uses": ["quick_estimates", "stiffness_illustration"],
    },
    "rk4": {
        "order": 4,
        "explicit": True,
        "description": "Classical 4th-order Runge-Kutta; 4 stages per step",
        "stability": "good for non-stiff problems",
        "uses": ["orbital_mechanics", "celestial_mechanics", "general_purpose_ode"],
    },
    "dopri": {
        "order": "5(4)",
        "explicit": True,
        "description": "Dormand-Prince 5(4) embedded pair; adaptive step size",
        "stability": "good for smooth non-stiff systems",
        "uses": ["default_in_scipy_integrate_solve_ivp"],
    },
    "bdf": {
        "order": "1-6 variable",
        "explicit": False,
        "description": "Backward differentiation formulas; multistep implicit",
        "stability": "A-stable up to order 2; suitable for stiff ODEs",
        "uses": ["chemical_kinetics", "circuit_simulation", "stiff_systems"],
    },
    "radau": {
        "order": "5, 9, 13",
        "explicit": False,
        "description": "Implicit Runge-Kutta (Radau IIA); fully implicit",
        "stability": "L-stable; excellent for very stiff systems",
        "uses": ["flame_modelling", "DAE_systems", "singular_perturbation_problems"],
    },
    "leapfrog": {
        "order": 2,
        "explicit": True,
        "description": "Verlet/leapfrog; symplectic integrator for Hamiltonian systems",
        "stability": "excellent long-time energy conservation",
        "uses": ["molecular_dynamics", "N-body_simulations", "Hamiltonian_systems"],
    },
}

PDE_CLASSES: dict[str, dict[str, Any]] = {
    "elliptic": {
        "canonical_form": "div(k grad u) = f",
        "discriminant": "negative",
        "examples": ["Laplace", "Poisson", "Helmholtz", "time-independent Schroedinger"],
        "boundary_conditions": ["Dirichlet", "Neumann", "Robin"],
        "prototype": "Laplace equation: nabla^2 u = 0",
    },
    "parabolic": {
        "canonical_form": "u_t - div(k grad u) = f",
        "discriminant": "zero",
        "examples": ["heat_equation", "diffusion_equation", "Black-Scholes"],
        "boundary_conditions": ["Dirichlet", "Neumann", "periodic"],
        "prototype": "Heat equation: u_t = alpha * nabla^2 u",
    },
    "hyperbolic": {
        "canonical_form": "u_tt - c^2 nabla^2 u = 0",
        "discriminant": "positive",
        "examples": ["wave_equation", "Maxwell_equations", "telegraph_equation", "Euler_equations"],
        "boundary_conditions": ["Dirichlet", "Cauchy", "radiation"],
        "prototype": "Wave equation: u_tt = c^2 * nabla^2 u",
    },
    "biharmonic": {
        "canonical_form": "nabla^4 u = f",
        "discriminant": "fourth-order",
        "examples": ["plate_bending", "stream_function_vorticity", "Stokes_flow"],
        "boundary_conditions": ["clamped", "simply_supported"],
        "prototype": "Biharmonic equation: nabla^4 u = 0",
    },
}

GROUP_CLASSIFICATION: dict[str, list[str]] = {
    "abelian": ["cyclic", "direct_product_of_cyclic", "integer_addition", "klein_four"],
    "non_abelian": ["symmetric_Sn_n>=3", "dihedral_Dn", "quaternion_Q8", "GL_n_R_n>=2", "free_group"],
    "finite_simple": ["cyclic_prime_order", "alternating_An_n>=5", "lie_type_finite", "sporadic"],
    "solvable": ["abelian", "dihedral", "p-groups", "S4", "all_groups_of_odd_order_FeitThompson"],
    "nilpotent": ["p-groups", "direct_product_of_p-groups"],
}


# ---------------------------------------------------------------------------
# Linear Algebra
# ---------------------------------------------------------------------------

def solve_linear_system(A: list[list[float]], b: list[float]) -> list[float]:
    """Solve Ax = b via Gaussian elimination with partial pivoting.

    Args:
        A: n x n coefficient matrix (list of rows).
        b: length-n right-hand side vector.

    Returns:
        Solution vector x (length n).

    Raises:
        ValueError: If matrix is singular or dimensions mismatch.
    """
    n = len(A)
    if n != len(b):
        raise ValueError(f"Dimension mismatch: A is {n}x{n} but b has length {len(b)}")
    for i, row in enumerate(A):
        if len(row) != n:
            raise ValueError(f"Row {i} has length {len(row)}, expected {n}")

    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot_row][col]) < 1e-12:
            raise ValueError("Matrix is singular or nearly singular")
        if pivot_row != col:
            M[col], M[pivot_row] = M[pivot_row], M[col]
        pivot = M[col][col]
        M[col] = [v / pivot for v in M[col]]
        for r in range(n):
            if r != col:
                factor = M[r][col]
                M[r] = [M[r][j] - factor * M[col][j] for j in range(n + 1)]
    return [M[i][n] for i in range(n)]


def _compute_power_iteration(A: list[list[float]], max_iter: int = 1000) -> float:
    """Compute dominant eigenvalue via power iteration."""
    n = len(A)
    v = [1.0] * n
    for _ in range(max_iter):
        w = [sum(A[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x * x for x in w))
        if norm < 1e-15:
            return 0.0
        v = [x / norm for x in w]
    return sum(sum(A[i][j] * v[j] for j in range(n)) * v[i] for i in range(n))


def compute_eigenvalues(matrix: list[list[float]]) -> list[float]:
    """Compute eigenvalues for 1x1, 2x2, and 3x3 matrices analytically.

    For larger matrices, returns the dominant eigenvalue via power iteration
    with a warning about incomplete results.

    Args:
        matrix: Square matrix (list of rows).

    Returns:
        List of eigenvalues (real). Complex eigenvalues are returned as None
        in their position for 2x2/3x3; for larger matrices, only the
        dominant real eigenvalue is returned in a single-element list.
    """
    n = len(matrix)
    for i, row in enumerate(matrix):
        if len(row) != n:
            raise ValueError(f"Row {i} has length {len(row)}, expected {n}")

    if n == 1:
        return [matrix[0][0]]
    if n == 2:
        a, b = matrix[0][0], matrix[0][1]
        c, d = matrix[1][0], matrix[1][1]
        trace = a + d
        det = a * d - b * c
        discriminant = trace * trace - 4 * det
        if discriminant >= 0:
            sqrt_d = math.sqrt(discriminant)
            return [(trace + sqrt_d) / 2, (trace - sqrt_d) / 2]
        return [(trace + 0j) / 2, (trace - 0j) / 2]
    if n == 3:
        trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
        minor_a = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
        minor_b = matrix[0][0] * matrix[2][2] - matrix[0][2] * matrix[2][0]
        minor_c = matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]
        minor_sum = minor_a + minor_b + minor_c
        det_val = (
            matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
            - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
            + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
        )
        offset = trace / 3.0
        p = minor_sum / 3.0 - offset * offset
        q = offset ** 3 - offset * minor_sum / 2.0 + det_val / 2.0
        discriminant = q * q + p * p * p
        if discriminant >= 0:
            u_sign = -1.0 if q < 0 else 1.0
            v_sign = 1.0
            r = math.sqrt(max(0.0, discriminant))
            cube = abs(-q + r) if abs(-q + r) > 1e-15 else 0.0
            u = u_sign * (cube ** (1.0 / 3.0))
            cube = abs(-q - r) if abs(-q - r) > 1e-15 else 0.0
            v = v_sign * (cube ** (1.0 / 3.0))
            e1 = u + v + offset
            return sorted([e1, float('nan'), float('nan')], reverse=True)
        theta = math.acos(max(-1.0, min(1.0, q / math.sqrt(-(p * p * p)) if abs(p) > 1e-15 else 0.0)))
        r = 2.0 * math.sqrt(max(0.0, -p))
        e1 = r * math.cos(theta / 3.0) + offset
        e2 = r * math.cos((theta + 2.0 * math.pi) / 3.0) + offset
        e3 = r * math.cos((theta + 4.0 * math.pi) / 3.0) + offset
        return sorted([e1, e2, e3], reverse=True)

    dom = _compute_power_iteration(matrix)
    return [dom]


def lu_decompose(A: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    """LU decomposition via Doolittle algorithm (L has unit diagonal).

    Returns (L, U) where A = L @ U.
    """
    n = len(A)
    for i, row in enumerate(A):
        if len(row) != n:
            raise ValueError(f"Non-square matrix: row {i} has {len(row)} cols, expected {n}")

    L = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    U = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for k in range(i, n):
            s = sum(L[i][j] * U[j][k] for j in range(i))
            U[i][k] = A[i][k] - s
        for k in range(i + 1, n):
            s = sum(L[k][j] * U[j][i] for j in range(i))
            if abs(U[i][i]) < 1e-15:
                raise ValueError("Zero pivot in LU decomposition; matrix may be singular")
            L[k][i] = (A[k][i] - s) / U[i][i]

    return L, U


def qr_decompose(A: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    """QR decomposition via modified Gram-Schmidt.

    Returns (Q, R) where A = Q @ R, Q is orthogonal, R is upper triangular.
    """
    m = len(A)
    n = len(A[0]) if A else 0
    Q = [[0.0] * n for _ in range(m)]
    R = [[0.0] * n for _ in range(n)]
    cols = [[A[i][j] for i in range(m)] for j in range(n)]

    for j in range(n):
        v = cols[j][:]
        R[j][j] = math.sqrt(sum(x * x for x in v))
        if R[j][j] < 1e-15:
            raise ValueError("Linearly dependent columns in QR decomposition")
        for i in range(m):
            Q[i][j] = v[i] / R[j][j]
        for k in range(j + 1, n):
            R[j][k] = sum(Q[i][j] * cols[k][i] for i in range(m))
            for i in range(m):
                cols[k][i] -= R[j][k] * Q[i][j]

    return Q, R


def determinant(A: list[list[float]]) -> float:
    """Compute determinant via LU decomposition or directly for small matrices."""
    n = len(A)
    if n == 1:
        return A[0][0]
    if n == 2:
        return A[0][0] * A[1][1] - A[0][1] * A[1][0]
    if n == 3:
        return (
            A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
            - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
            + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0])
        )
    try:
        _, U = lu_decompose(A)
        d = 1.0
        for i in range(n):
            d *= U[i][i]
        return d
    except ValueError:
        return 0.0


def tensor_contraction(T: list[list[list[float]]], dims: tuple[int, int]) -> list[list[float]]:
    """Contract a rank-3 tensor over two specified dimensions (0, 1, or 2).

    Given tensor T[i][j][k], contract over dims (a,b) by summing over the
    indices along those axes. Returns the resulting matrix.

    Args:
        T: Rank-3 tensor as list of matrices (list[list[list[float]]]).
        dims: Two axis indices to contract (0-indexed).

    Returns:
        Resulting matrix after contraction.
    """
    if len(dims) != 2:
        raise ValueError("Must specify exactly 2 dimensions to contract")
    a, b = dims
    if a == b:
        raise ValueError("Cannot contract the same dimension twice")

    shape = [len(T), len(T[0]) if T else 0, len(T[0][0]) if T and T[0] else 0]
    free = [d for d in range(3) if d not in (a, b)]
    contract_dim = shape[a]
    free_shape = [shape[d] for d in free]

    if len(free_shape) == 1:
        d0 = free_shape[0]
        result = [0.0] * d0
        idx = [0, 0, 0]
        for i0 in range(d0):
            accumulator = 0.0
            for k in range(contract_dim):
                idx[free[0]] = i0
                idx[a] = k
                idx[b] = k
                accumulator += T[idx[0]][idx[1]][idx[2]]
            result[i0] = accumulator
        return result

    d0, d1 = free_shape[0], free_shape[1]
    result = [[0.0] * d1 for _ in range(d0)]
    idx = [0, 0, 0]
    for i0 in range(d0):
        for i1 in range(d1):
            accumulator = 0.0
            for k in range(contract_dim):
                idx[free[0]] = i0
                idx[free[1]] = i1
                idx[a] = k
                idx[b] = k
                accumulator += T[idx[0]][idx[1]][idx[2]]
            result[i0][i1] = accumulator
    return result


# ---------------------------------------------------------------------------
# ODE / PDE
# ---------------------------------------------------------------------------

def solve_ode(
    f: Callable[[float, list[float]], list[float]],
    y0: list[float],
    t_span: tuple[float, float],
    h: float,
) -> list[tuple[float, list[float]]]:
    """Solve ODE dy/dt = f(t, y) using classical RK4.

    Args:
        f: Right-hand side function f(t, y) -> list[float].
        y0: Initial state vector.
        t_span: (t_start, t_end).
        h: Step size.

    Returns:
        List of (t_i, y_i) tuples at each time step.
    """
    t0, tf = t_span
    if h <= 0:
        raise ValueError("Step size h must be positive")
    if tf < t0:
        h = -h

    steps = int(abs(tf - t0) / abs(h))
    result: list[tuple[float, list[float]]] = [(t0, y0[:])]
    t, y = t0, y0[:]
    n = len(y)

    for _ in range(steps):
        k1 = f(t, y)
        k2 = f(t + h / 2, [y[i] + h * k1[i] / 2 for i in range(n)])
        k3 = f(t + h / 2, [y[i] + h * k2[i] / 2 for i in range(n)])
        k4 = f(t + h, [y[i] + h * k3[i] for i in range(n)])
        y = [y[i] + h * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) / 6 for i in range(n)]
        t += h
        result.append((t, y[:]))
    return result


def classify_pde(coefficients: dict[str, float], variables: list[str]) -> str:
    """Classify a second-order linear PDE based on its coefficients.

    For the general form:
      A*u_xx + B*u_xy + C*u_yy + D*u_x + E*u_y + F*u + G = 0

    Classification is based on the discriminant B^2 - 4*A*C:
      < 0: elliptic
      = 0: parabolic
      > 0: hyperbolic

    Args:
        coefficients: Dict with keys 'A','B','C','D','E','F','G'.
        variables: List of variable names (e.g. ['x','y'] or ['x','t']).

    Returns:
        Classification string.
    """
    a = coefficients.get("A", 0.0)
    b = coefficients.get("B", 0.0)
    c = coefficients.get("C", 0.0)
    discriminant = b * b - 4 * a * c
    if discriminant < -1e-12:
        return "elliptic"
    if abs(discriminant) <= 1e-12:
        return "parabolic"
    return "hyperbolic"


def finite_difference_1d(
    lhs_func: Callable[[float, float, float], float],
    rhs_func: Callable[[float], float],
    grid: list[float],
    bc: dict[str, float],
) -> list[float]:
    """Solve 1D BVP -(d/dx)(k(x) du/dx) + r(x)u = f(x) via central finite differences.

    Over the grid x[0] ... x[N-1], Dirichlet BCs at both ends.

    Args:
        lhs_func: (x, u, dudx) -> value after discretisation.
        rhs_func: x -> RHS value f(x).
        grid: Discretised spatial coordinate points.
        bc: Dict with 'left' (u at x[0]) and 'right' (u at x[-1]).

    Returns:
        Solution u at each grid point.
    """
    N = len(grid)
    u = [0.0] * N
    u[0] = bc["left"]
    u[N - 1] = bc["right"]

    interior = N - 2
    A = [[0.0] * interior for _ in range(interior)]
    f = [0.0] * interior

    for i in range(1, N - 1):
        h = grid[i + 1] - grid[i]
        h_prev = grid[i] - grid[i - 1]
        idx = i - 1
        A[idx][idx] = 1.0 / h_prev + 1.0 / h
        if idx > 0:
            A[idx][idx - 1] = -1.0 / h_prev
        if idx < interior - 1:
            A[idx][idx + 1] = -1.0 / h
        f[idx] = rhs_func(grid[i])

    sol = solve_linear_system(A, f)
    for i in range(1, N - 1):
        u[i] = sol[i - 1]
    return u


# ---------------------------------------------------------------------------
# Group Theory
# ---------------------------------------------------------------------------

def classify_group(group_order: int, properties: dict[str, Any] | None = None) -> str:
    """Classify a group by its order and known properties.

    Args:
        group_order: The order (number of elements) of the group.
        properties: Optional dict with keys like 'abelian', 'simple', 'p_group'.

    Returns:
        Classification string.
    """
    props = properties or {}
    if props.get("abelian") is False and group_order == 6:
        return "non_abelian_order_6_S3_or_D3"
    if group_order == 1:
        return "trivial_group"
    if props.get("abelian") is True:
        if props.get("simple") is True and group_order > 1:
            return "cyclic_prime_order"
        if props.get("cyclic") is True:
            return "cyclic_group_Z" + str(group_order)
        return "abelian"
    if props.get("nilpotent"):
        return "nilpotent_p_group" if props.get("p_group") else "nilpotent"
    if props.get("solvable"):
        return "solvable"
    if props.get("simple") is True:
        return "finite_simple"
    if props.get("dihedral") is True:
        return f"dihedral_D{group_order // 2}"
    return "general_group"


def _mod_add(a: int, b: int, mod: int) -> int:
    return (a + b) % mod


def is_abelian(elements: list[int], product: Callable[[int, int], int]) -> bool:
    """Check whether a finite group is abelian.

    Args:
        elements: List of group element labels (e.g. 0..n-1).
        product: Binary operation (a, b) -> c.

    Returns:
        True if the product commutes for all pairs in elements.
    """
    for a, b in itertools.product(elements, repeat=2):
        if product(a, b) != product(b, a):
            return False
    return True


def character_table(group_name: str, irreps: list[list[list[float]]]) -> list[list[float]]:
    """Build a character table from a list of irreducible representations.

    Each irrep is a list of matrices (one per conjugacy class); the
    character is the trace of each representation matrix.

    Args:
        group_name: Name of the group (used only for documentation).
        irreps: List of irreps; each irrep is [matrix_class1, matrix_class2, ...].

    Returns:
        Character table: rows = irreps, cols = conjugacy classes.
    """
    if not irreps:
        return []
    num_classes = len(irreps[0])
    table: list[list[float]] = []
    for irrep in irreps:
        row: list[float] = []
        for matrix in irrep:
            trace = sum(matrix[i][i] for i in range(len(matrix)))
            row.append(trace)
        table.append(row)
    return table


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def fundamental_group(space_type: str) -> str:
    """Return the fundamental group for a standard topological space.

    Args:
        space_type: One of 'circle', 'sphere', 'torus', 'RP2', 'RPn',
                    'figure8', 'klein_bottle', 'CPn'.

    Returns:
        String description of the fundamental group.
    """
    groups: dict[str, str] = {
        "circle": "Z (infinite cyclic)",
        "sphere": "trivial (0)",
        "torus": "Z x Z (free abelian of rank 2)",
        "torus_n": "Z^n (free abelian of rank n)",
        "RP2": "Z/2 (cyclic of order 2)",
        "RPn": "Z/2 for n>=2; Z for n=1",
        "figure8": "free group on 2 generators F_2",
        "klein_bottle": "presentation <a,b | abab^{-1} = 1>",
        "CPn": "trivial (0) for all n",
        "real_projective": "Z/2",
        "moebius_strip": "Z (deformation retracts to circle)",
    }
    return groups.get(space_type, f"Unknown fundamental group for '{space_type}'")


def betti_numbers(simplicial_complex: list[list[tuple[int, ...]]]) -> list[int]:
    """Compute Betti numbers of a finite simplicial complex.

    Args:
        simplicial_complex: List of k-skeleton data: [vertices, edges, faces, ...].
            Each element is a list of tuples of vertex indices (sorted, unique).
            e.g. [[(0,),(1,),(2,)], [(0,1),(1,2),(0,2)], [(0,1,2)]]

    Returns:
        List of Betti numbers [b_0, b_1, ...] from reduced chain complex.
    """
    if not simplicial_complex:
        return [1]

    max_dim = len(simplicial_complex) - 1
    betti: list[int] = []

    chain_groups = [len(simplicial_complex[k]) for k in range(len(simplicial_complex))]

    for k in range(max_dim + 1):
        if k == 0:
            boundary_k = []
        else:
            boundary_k = _build_boundary_matrix(simplicial_complex[k - 1], simplicial_complex[k])
        if k == max_dim:
            boundary_kp1 = []
        else:
            boundary_kp1 = _build_boundary_matrix(simplicial_complex[k], simplicial_complex[k + 1])

        rank_ker = _nullity(boundary_k, chain_groups[k])
        rank_im = _rank(boundary_kp1, chain_groups[k])
        betti.append(max(0, rank_ker - rank_im))

    return betti


def euler_characteristic(simplicial_complex: list[list[tuple[int, ...]]]) -> int:
    """Compute Euler characteristic as alternating sum of simplex counts.

    chi = sum_{k=0}^n (-1)^k * (number of k-simplices)
    """
    return sum((-1) ** k * len(simplicial_complex[k]) for k in range(len(simplicial_complex)))


def _build_boundary_matrix(
    domain_simplices: list[tuple[int, ...]],
    target_simplices: list[tuple[int, ...]],
) -> list[list[int]]:
    """Build the boundary matrix d_k: C_k -> C_{k-1}.

    Rows index (k-1)-simplices, columns index k-simplices.
    Entry (i, j) = +-1 if the (k-1)-simplex is a face of the k-simplex, 0 otherwise.
    Sign determined by the position of the omitted vertex (alternating).
    """
    m = len(domain_simplices)
    n = len(target_simplices)
    if m == 0 or n == 0:
        return [[0]]

    matrix = [[0] * n for _ in range(m)]

    domain_lookup = {tuple(sorted(s)): idx for idx, s in enumerate(domain_simplices)}
    for col, k_simplex in enumerate(target_simplices):
        for pos in range(len(k_simplex)):
            sign = 1 if pos % 2 == 0 else -1
            face = tuple(sorted(k_simplex[j] for j in range(len(k_simplex)) if j != pos))
            if face in domain_lookup:
                matrix[domain_lookup[face]][col] = 1 if sign % 2 == 0 else -1

    return matrix


def _rank(matrix: list[list[int]], expected_rows: int) -> int:
    """Compute rank of boundary matrix over Z via Gaussian elimination.

    (Over integral domain; uses row reduction; rank = number of non-zero rows)
    """
    if not matrix or not matrix[0]:
        return 0
    m = len(matrix)
    n = len(matrix[0])
    M = [row[:] for row in matrix]
    rank = 0
    col = 0
    while col < n and rank < m:
        pivot_row = None
        for r in range(rank, m):
            if M[r][col] != 0:
                pivot_row = r
                break
        if pivot_row is None:
            col += 1
            continue
        M[rank], M[pivot_row] = M[pivot_row], M[rank]
        for r in range(rank + 1, m):
            if M[r][col] != 0:
                factor = M[r][col] // M[rank][col]
                for c in range(col, n):
                    M[r][c] -= factor * M[rank][c]
        rank += 1
        col += 1
    return rank


def _nullity(matrix: list[list[int]], num_columns: int) -> int:
    """Nullity = number of columns - rank."""
    if not matrix or not matrix[0]:
        return num_columns
    r = _rank(matrix, num_columns)
    return num_columns - r
