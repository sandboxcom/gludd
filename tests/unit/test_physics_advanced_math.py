"""Tests for advanced_math module_utils at collections/.../physics/plugins/module_utils/advanced_math.py."""

from __future__ import annotations

import math
import os
import sys

import pytest

_COLLECTION_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "collections", "ansible_collections", "general_ludd",
        "physics", "plugins", "module_utils",
    )
)
if _COLLECTION_DIR not in sys.path:
    sys.path.insert(0, _COLLECTION_DIR)

am = pytest.importorskip("advanced_math")


class TestSolveLinearSystem:
    def test_solves_2x2_system(self):
        A = [[2.0, 1.0], [1.0, 3.0]]
        b = [5.0, 6.0]
        x = am.solve_linear_system(A, b)
        assert len(x) == 2
        assert abs(x[0] - 1.8) < 1e-10
        assert abs(x[1] - 1.4) < 1e-10

    def test_solves_3x3_system(self):
        A = [[1.0, 2.0, 3.0], [2.0, 1.0, 1.0], [3.0, 2.0, 1.0]]
        b = [14.0, 8.0, 10.0]
        x = am.solve_linear_system(A, b)
        for i in range(3):
            residual = sum(A[i][j] * x[j] for j in range(3)) - b[i]
            assert abs(residual) < 1e-10

    def test_identity_matrix_returns_b(self):
        A = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        b = [7.0, -3.0, 42.0]
        x = am.solve_linear_system(A, b)
        for i in range(3):
            assert abs(x[i] - b[i]) < 1e-10

    def test_raises_on_dimension_mismatch(self):
        A = [[1.0, 2.0], [3.0, 4.0]]
        b = [1.0]
        with pytest.raises(ValueError, match="Dimension mismatch"):
            am.solve_linear_system(A, b)

    def test_raises_on_non_square_A(self):
        A = [[1.0, 2.0], [3.0, 4.0, 5.0]]
        b = [1.0, 2.0]
        with pytest.raises(ValueError, match="Row"):
            am.solve_linear_system(A, b)

    def test_raises_on_singular_matrix(self):
        A = [[1.0, 2.0], [2.0, 4.0]]
        b = [1.0, 2.0]
        with pytest.raises(ValueError, match="singular"):
            am.solve_linear_system(A, b)

    def test_1x1_system(self):
        A = [[5.0]]
        b = [10.0]
        x = am.solve_linear_system(A, b)
        assert x == [2.0]

    def test_5x5_diagonal_dominant(self):
        n = 5
        A = [[3.0 if i == j else 1.0 for j in range(n)] for i in range(n)]
        b = [float(2 * i + 1) for i in range(n)]
        x = am.solve_linear_system(A, b)
        for i in range(n):
            residual = sum(A[i][j] * x[j] for j in range(n)) - b[i]
            assert abs(residual) < 1e-10


class TestComputeEigenvalues:
    def test_1x1_eigenvalue(self):
        evals = am.compute_eigenvalues([[7.0]])
        assert evals == [7.0]

    def test_2x2_eigenvalues_real(self):
        evals = am.compute_eigenvalues([[2.0, 0.0], [0.0, 3.0]])
        assert sorted(evals, reverse=True) == [3.0, 2.0]

    def test_2x2_eigenvalues_diagonal(self):
        evals = am.compute_eigenvalues([[5.0, 1.0], [1.0, 5.0]])
        assert sorted(evals, reverse=True) == [6.0, 4.0]

    def test_3x3_eigenvalues_identity(self):
        evals = am.compute_eigenvalues([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        for e in evals:
            assert abs(e - 1.0) < 1e-10

    def test_3x3_eigenvalues_diagonal(self):
        evals = am.compute_eigenvalues([[2.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 6.0]])
        assert sorted(evals, reverse=True) == [6.0, 4.0, 2.0]

    def test_4x4_power_iteration_returns_dominant(self):
        A = [[4.0, 1.0, 0.0, 0.0], [1.0, 4.0, 1.0, 0.0], [0.0, 1.0, 4.0, 1.0], [0.0, 0.0, 1.0, 4.0]]
        evals = am.compute_eigenvalues(A)
        assert len(evals) >= 1
        assert evals[0] > 0

    def test_raises_on_non_square(self):
        with pytest.raises(ValueError):
            am.compute_eigenvalues([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])


class TestLUDecompose:
    def test_decomposes_2x2(self):
        A = [[4.0, 3.0], [6.0, 3.0]]
        L, U = am.lu_decompose(A)
        assert len(L) == 2
        assert L[0][0] == 1.0 and L[1][1] == 1.0
        assert abs(U[0][0] - 4.0) < 1e-10
        for i in range(2):
            for j in range(2):
                reconstructed = sum(L[i][k] * U[k][j] for k in range(2))
                assert abs(reconstructed - A[i][j]) < 1e-10

    def test_decomposes_3x3(self):
        A = [[2.0, -1.0, -2.0], [-4.0, 6.0, 3.0], [-4.0, -2.0, 8.0]]
        L, U = am.lu_decompose(A)
        for i in range(3):
            assert L[i][i] == 1.0
        for i in range(3):
            for j in range(3):
                reconstructed = sum(L[i][k] * U[k][j] for k in range(3))
                assert abs(reconstructed - A[i][j]) < 1e-10

    def test_raises_on_singular(self):
        A = [[0.0, 0.0], [0.0, 0.0]]
        with pytest.raises(ValueError, match="Zero pivot"):
            am.lu_decompose(A)

    def test_raises_on_non_square(self):
        A = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        with pytest.raises(ValueError, match="Non-square"):
            am.lu_decompose(A)

    def test_identity_yields_identity(self):
        A = [[1.0, 0.0], [0.0, 1.0]]
        L, U = am.lu_decompose(A)
        for i in range(2):
            for j in range(2):
                if i == j:
                    assert abs(L[i][j] - 1.0) < 1e-10
                    assert abs(U[i][j] - 1.0) < 1e-10
                else:
                    assert abs(L[i][j]) < 1e-10
                    assert abs(U[i][j]) < 1e-10


class TestQRDecompose:
    def test_decomposes_2x2(self):
        A = [[12.0, -51.0], [6.0, 167.0]]
        Q, R = am.qr_decompose(A)
        for i in range(2):
            for j in range(2):
                reconstructed = sum(Q[i][k] * R[k][j] for k in range(2))
                assert abs(reconstructed - A[i][j]) < 1e-10

    def test_Q_is_orthogonal_2x2(self):
        A = [[1.0, 2.0], [3.0, 4.0]]
        Q, _ = am.qr_decompose(A)
        for i in range(2):
            norm = math.sqrt(sum(Q[k][i] * Q[k][i] for k in range(2)))
            assert abs(norm - 1.0) < 1e-10

    def test_R_is_upper_triangular(self):
        A = [[12.0, -51.0, 4.0], [6.0, 167.0, -68.0], [-4.0, 24.0, -41.0]]
        _Q, R = am.qr_decompose(A)
        n = len(R)
        for i in range(n):
            for j in range(i):
                assert abs(R[i][j]) < 1e-10

    def test_raises_on_dependent_columns(self):
        A = [[1.0, 2.0], [2.0, 4.0]]
        with pytest.raises(ValueError, match="dependent"):
            am.qr_decompose(A)


class TestDeterminant:
    def test_1x1(self):
        assert am.determinant([[5.0]]) == 5.0

    def test_2x2(self):
        assert am.determinant([[3.0, 8.0], [4.0, 6.0]]) == -14.0

    def test_3x3(self):
        det = am.determinant([[6.0, 1.0, 1.0], [4.0, -2.0, 5.0], [2.0, 8.0, 7.0]])
        assert abs(det - -306.0) < 1e-10

    def test_identity(self):
        assert abs(am.determinant([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]) - 1.0) < 1e-10

    def test_singular_returns_zero(self):
        assert am.determinant([[1.0, 2.0], [2.0, 4.0]]) == 0.0


class TestTensorContraction:
    def test_contraction_0_and_2(self):
        T = [
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, 6.0], [7.0, 8.0]],
        ]
        result = am.tensor_contraction(T, (0, 2))
        assert len(result) == 2
        assert abs(result[0] - (1.0 + 6.0)) < 1e-10
        assert abs(result[1] - (3.0 + 8.0)) < 1e-10

    def test_contraction_0_and_1(self):
        T = [
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, 6.0], [7.0, 8.0]],
        ]
        result = am.tensor_contraction(T, (0, 1))
        assert len(result) == 2
        assert abs(result[0] - (1.0 + 7.0)) < 1e-10
        assert abs(result[1] - (2.0 + 8.0)) < 1e-10

    def test_raises_on_same_dim(self):
        T = [[[1.0]]]
        with pytest.raises(ValueError, match="same dimension"):
            am.tensor_contraction(T, (1, 1))

    def test_raises_on_wrong_dims_count(self):
        T = [[[1.0]]]
        with pytest.raises(ValueError, match="exactly 2"):
            am.tensor_contraction(T, (0,))


class TestSolveODE:
    def test_simple_exponential(self):
        def f(t: float, y: list[float]) -> list[float]:
            return [y[0]]

        result = am.solve_ode(f, [1.0], (0.0, 1.0), 0.1)
        assert len(result) >= 5
        t_last, y_last = result[-1]
        assert abs(t_last - 1.0) < 1e-10
        expected = math.exp(1.0)
        assert abs(y_last[0] - expected) < 0.05

    def test_harmonic_oscillator_energy_conserved(self):
        def f(t: float, y: list[float]) -> list[float]:
            return [y[1], -y[0]]

        result = am.solve_ode(f, [1.0, 0.0], (0.0, 2 * math.pi), 0.01)
        _, y_last = result[-1]
        assert abs(y_last[0] - 1.0) < 0.2
        assert abs(y_last[1]) < 0.2

    def test_raises_on_negative_h(self):
        def f(t: float, y: list[float]) -> list[float]:
            return [y[0]]

        with pytest.raises(ValueError, match="positive"):
            am.solve_ode(f, [1.0], (0.0, 1.0), -0.1)


class TestClassifyPDE:
    def test_elliptic_laplace(self):
        coeffs = {"A": 1.0, "B": 0.0, "C": 1.0}
        assert am.classify_pde(coeffs, ["x", "y"]) == "elliptic"

    def test_hyperbolic_wave(self):
        coeffs = {"A": 1.0, "B": 0.0, "C": -1.0}
        assert am.classify_pde(coeffs, ["x", "t"]) == "hyperbolic"

    def test_parabolic_heat(self):
        coeffs = {"A": 1.0, "B": 0.0, "C": 0.0}
        assert am.classify_pde(coeffs, ["x", "t"]) == "parabolic"

    def test_hyperbolic_with_cross_term(self):
        coeffs = {"A": 1.0, "B": 3.0, "C": 1.0}
        assert am.classify_pde(coeffs, ["x", "y"]) == "hyperbolic"

    def test_parabolic_zero_discriminant(self):
        coeffs = {"A": 1.0, "B": 2.0, "C": 1.0}
        assert am.classify_pde(coeffs, ["x", "y"]) == "parabolic"


class TestFiniteDifference1D:
    def test_simple_poisson(self):
        grid = [0.0, 0.25, 0.5, 0.75, 1.0]

        def lhs(x: float, u: float, dudx: float) -> float:
            return -dudx

        def rhs(x: float) -> float:
            return 1.0

        bc = {"left": 0.0, "right": 0.0}
        u = am.finite_difference_1d(lhs, rhs, grid, bc)
        assert abs(u[0] - 0.0) < 1e-10
        assert abs(u[4] - 0.0) < 1e-10
        for val in u:
            assert val >= -0.01


class TestClassifyGroup:
    def test_trivial_group(self):
        assert am.classify_group(1) == "trivial_group"

    def test_abelian_cyclic(self):
        result = am.classify_group(5, {"abelian": True, "cyclic": True})
        assert "cyclic" in result

    def test_non_abelian_order_6(self):
        result = am.classify_group(6, {"abelian": False})
        assert "S3" in result or "D3" in result

    def test_nilpotent_p_group(self):
        result = am.classify_group(8, {"nilpotent": True, "p_group": True})
        assert "nilpotent_p_group" in result

    def test_solvable(self):
        result = am.classify_group(12, {"solvable": True})
        assert "solvable" in result

    def test_finite_simple(self):
        result = am.classify_group(168, {"simple": True})
        assert "finite_simple" in result

    def test_dihedral(self):
        result = am.classify_group(8, {"dihedral": True})
        assert "dihedral_D4" in result


class TestIsAbelian:
    def test_cyclic_group_is_abelian(self):
        elements = [0, 1, 2, 3, 4]
        def product(a, b):
            return (a + b) % 5
        assert am.is_abelian(elements, product) is True

    def test_S3_is_not_abelian(self):
        perm_map = {
            (0, 1): 1, (0, 2): 2, (1, 0): 3, (1, 2): 4, (2, 0): 5, (2, 1): 6,
            (0, 3): 3, (0, 4): 4, (0, 5): 5, (0, 6): 6,
            (1, 3): 0, (1, 5): 2,
            (2, 4): 0, (2, 6): 1,
            (3, 0): 3, (3, 1): 0, (3, 2): 5,
            (4, 0): 4, (4, 1): 2, (4, 2): 6,
            (5, 0): 5, (5, 1): 6, (5, 2): 0,
            (6, 0): 6, (6, 1): 4, (6, 2): 1,
        }
        def product(a, b):
            return perm_map.get((a, b), (a + b) % 7)
        assert am.is_abelian([0, 1, 2], product) is False


class TestCharacterTable:
    def test_S3_character_table(self):
        irreps = [
            [[[1]], [[1]], [[1]]],
            [[[1]], [[1]], [[-1]]],
            [[[1, 0], [0, 1]], [[-0.5, -0.866], [0.866, -0.5]], [[-0.5, 0.866], [-0.866, -0.5]]],
        ]
        table = am.character_table("S3", irreps)
        assert len(table) == 3
        assert len(table[0]) == 3
        assert abs(table[0][0] - 1.0) < 1e-10
        assert abs(table[1][2] - (-1.0)) < 1e-10
        assert abs(table[2][0] - 2.0) < 1e-10

    def test_empty_irreps(self):
        assert am.character_table("C1", []) == []


class TestFundamentalGroup:
    def test_circle(self):
        assert "Z" in am.fundamental_group("circle")

    def test_sphere(self):
        assert "trivial" in am.fundamental_group("sphere")

    def test_torus(self):
        result = am.fundamental_group("torus")
        assert "Z" in result

    def test_RP2(self):
        result = am.fundamental_group("RP2")
        assert "Z/2" in result or "2" in result

    def test_figure8(self):
        result = am.fundamental_group("figure8")
        assert "free" in result.lower() or "F_2" in result

    def test_CPn(self):
        result = am.fundamental_group("CPn")
        assert "trivial" in result.lower()

    def test_unknown_space(self):
        result = am.fundamental_group("schwifty_space")
        assert "Unknown" in result


class TestBettiNumbers:
    def test_simplex_triangle(self):
        sc = [[(0,), (1,), (2,)], [(0, 1), (1, 2), (0, 2)]]
        betti = am.betti_numbers(sc)
        assert betti[0] == 1

    def test_two_simplices_two_components(self):
        sc = [[(0,), (1,), (2,), (3,)], [(0, 1), (2, 3)]]
        betti = am.betti_numbers(sc)
        assert betti[0] == 2

    def test_empty_complex(self):
        betti = am.betti_numbers([])
        assert betti == [1]

    def test_single_vertex(self):
        betti = am.betti_numbers([[(0,)]])
        assert betti[0] == 1


class TestEulerCharacteristic:
    def test_simplex_triangle(self):
        sc = [[(0,), (1,), (2,)], [(0, 1), (1, 2), (0, 2)]]
        chi = am.euler_characteristic(sc)
        assert chi == 0

    def test_tetrahedron(self):
        sc = [
            [(0,), (1,), (2,), (3,)],
            [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)],
            [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)],
        ]
        chi = am.euler_characteristic(sc)
        assert chi == 2

    def test_circle_as_simplicial(self):
        sc = [[(0,), (1,), (2,)], [(0, 1), (1, 2), (0, 2)]]
        chi = am.euler_characteristic(sc)
        assert chi == 0


class TestModuleConstants:
    def test_lie_groups_has_entry(self):
        assert "SO(3)" in am.LIE_GROUPS
        assert am.LIE_GROUPS["SO(3)"]["dimension"] == 3

    def test_manifold_types_has_entry(self):
        assert "sphere_Sn" in am.MANIFOLD_TYPES
        assert am.MANIFOLD_TYPES["torus_Tn"]["orientable"] is True

    def test_ode_methods_has_entry(self):
        assert "rk4" in am.ODE_METHODS
        assert am.ODE_METHODS["rk4"]["order"] == 4

    def test_pde_classes_has_entry(self):
        assert "elliptic" in am.PDE_CLASSES
        assert "Laplace" in am.PDE_CLASSES["elliptic"]["examples"]

    def test_group_classification_has_entry(self):
        assert "abelian" in am.GROUP_CLASSIFICATION
        assert "symmetric_Sn_n>=3" in am.GROUP_CLASSIFICATION["non_abelian"]
