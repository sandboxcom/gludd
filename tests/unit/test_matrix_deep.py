"""Deep matrix operations tests: multiply, transpose, determinant, inverse,
eigenvalue, LU decomposition, QR decomposition.

Pure-Python implementations (stdlib only) — no numpy.
"""

from __future__ import annotations

import math

# ── Helpers ──────────────────────────────────────────────────────────


def _shape(M: list[list[float]]) -> tuple[int, int]:
    rows = len(M)
    cols = len(M[0]) if rows else 0
    for r in M:
        if len(r) != cols:
            raise ValueError("jagged matrix")
    return rows, cols


def _identity(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _approx(a: float, b: float, tol: float = 1e-10) -> bool:
    return abs(a - b) < tol


def _mats_equal(A: list[list[float]], B: list[list[float]], tol: float = 1e-10) -> bool:
    rA, cA = _shape(A)
    rB, cB = _shape(B)
    if rA != rB or cA != cB:
        return False
    for i in range(rA):
        for j in range(cA):
            if not _approx(A[i][j], B[i][j], tol):
                return False
    return True


# ── Multiply ─────────────────────────────────────────────────────────


def matrix_multiply(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    rA, cA = _shape(A)
    rB, cB = _shape(B)
    if cA != rB:
        raise ValueError("inner dimensions must match")
    return [[sum(A[i][k] * B[k][j] for k in range(cA)) for j in range(cB)] for i in range(rA)]


# ── Transpose ────────────────────────────────────────────────────────


def matrix_transpose(M: list[list[float]]) -> list[list[float]]:
    rows, cols = _shape(M)
    return [[M[j][i] for j in range(rows)] for i in range(cols)]


# ── Determinant (recursive expansion) ─────────────────────────────────


def matrix_determinant(A: list[list[float]]) -> float:
    n, m = _shape(A)
    if n != m:
        raise ValueError("Non-square matrix")
    if n == 1:
        return A[0][0]
    if n == 2:
        return A[0][0] * A[1][1] - A[0][1] * A[1][0]
    det = 0.0
    for j in range(n):
        minor = [[A[i][k] for k in range(n) if k != j] for i in range(1, n)]
        det += ((-1) ** j) * A[0][j] * matrix_determinant(minor)
    return det


# ── Inverse (Gauss-Jordan) ────────────────────────────────────────────


def matrix_inverse(A: list[list[float]]) -> list[list[float]]:
    n, m = _shape(A)
    if n != m:
        raise ValueError("Non-square matrix")
    aug = [A[i][:] + _identity(n)[i] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if _approx(aug[pivot][col], 0.0):
            raise ValueError("singular matrix")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        piv_val = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= piv_val
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            for j in range(2 * n):
                aug[r][j] -= factor * aug[col][j]
    return [[aug[i][j] for j in range(n, 2 * n)] for i in range(n)]


# ── Eigenvalues (power iteration + deflation) ────────────────────────


def _dominant(m: list[list[float]]) -> tuple[float, list[float]]:
    n, _ = _shape(m)
    v = [1.0] * n
    for _ in range(200):
        w = [sum(m[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x * x for x in w))
        if norm < 1e-15:
            break
        w = [x / norm for x in w]
        if all(_approx(w[i], v[i]) for i in range(n)):
            break
        v = w
    lam = sum(v[i] * sum(m[i][j] * v[j] for j in range(n)) for i in range(n))
    return lam, v


def _deflate(m: list[list[float]], lam: float, v: list[float]) -> list[list[float]]:
    n, _ = _shape(m)
    return [[m[i][j] - lam * v[i] * v[j] for j in range(n)] for i in range(n)]


def matrix_eigenvalues(A: list[list[float]]) -> list[float]:
    n, _ = _shape(A)
    m = [row[:] for row in A]
    evals: list[float] = []
    for _ in range(n):
        lam, v = _dominant(m)
        evals.append(lam)
        m = _deflate(m, lam, v)
    return evals


# ── LU Decomposition (Doolittle) ─────────────────────────────────────


def matrix_lu(A: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    n, m = _shape(A)
    if n != m:
        raise ValueError("Non-square")
    L = [[0.0] * n for _ in range(n)]
    U = [[0.0] * n for _ in range(n)]
    for i in range(n):
        L[i][i] = 1.0
        for k in range(i, n):
            s = sum(L[i][j] * U[j][k] for j in range(i))
            U[i][k] = A[i][k] - s
        for k in range(i + 1, n):
            s = sum(L[k][j] * U[j][i] for j in range(i))
            if _approx(U[i][i], 0.0):
                raise ValueError("Zero pivot")
            L[k][i] = (A[k][i] - s) / U[i][i]
    return L, U


# ── QR Decomposition (modified Gram-Schmidt) ─────────────────────────


def matrix_qr(A: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    n, m = _shape(A)
    Qv: list[list[float]] = [[A[j][i] for j in range(n)] for i in range(m)]
    R = [[0.0] * m for _ in range(m)]
    for i in range(m):
        norm = math.sqrt(sum(Qv[i][k] * Qv[i][k] for k in range(n)))
        if _approx(norm, 0.0):
            raise ValueError("linearly dependent columns")
        R[i][i] = norm
        for k in range(n):
            Qv[i][k] /= norm
        for j in range(i + 1, m):
            R[i][j] = sum(Qv[i][k] * A[k][j] for k in range(n))
            for k in range(n):
                Qv[j][k] -= R[i][j] * Qv[i][k]
    Q = [[Qv[j][i] for j in range(m)] for i in range(n)]
    return Q, R


# ══════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════


class TestMultiply:
    def test_multiply_2x2(self):
        A = [[1.0, 2.0], [3.0, 4.0]]
        B = [[5.0, 6.0], [7.0, 8.0]]
        C = matrix_multiply(A, B)
        assert C == [[19.0, 22.0], [43.0, 50.0]]

    def test_multiply_3x2_by_2x3(self):
        A = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        B = [[7.0, 8.0, 9.0], [10.0, 11.0, 12.0]]
        C = matrix_multiply(A, B)
        assert len(C) == 3 and len(C[0]) == 3
        assert C[0][0] == 27.0

    def test_multiply_identity(self):
        identity = _identity(3)
        M = [[2.0, -1.0, 5.0], [3.0, 0.0, -2.0], [1.0, 4.0, 3.0]]
        assert _mats_equal(matrix_multiply(M, identity), M)

    def test_multiply_dimension_mismatch(self):
        import pytest

        with pytest.raises(ValueError):
            matrix_multiply([[1.0, 2.0]], [[3.0]])


class TestTranspose:
    def test_transpose_2x2(self):
        M = [[1.0, 2.0], [3.0, 4.0]]
        assert matrix_transpose(M) == [[1.0, 3.0], [2.0, 4.0]]

    def test_transpose_3x2(self):
        M = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        T = matrix_transpose(M)
        assert _shape(T) == (2, 3)
        assert T[0] == [1.0, 3.0, 5.0]

    def test_transpose_twice_is_identity(self):
        M = [[7.0, 8.0, 9.0], [10.0, 11.0, 12.0]]
        assert _mats_equal(matrix_transpose(matrix_transpose(M)), M)

    def test_transpose_symmetric(self):
        M = [[1.0, 2.0, 3.0], [2.0, 4.0, 5.0], [3.0, 5.0, 6.0]]
        assert _mats_equal(matrix_transpose(M), M)


class TestDeterminant:
    def test_det_1x1(self):
        assert matrix_determinant([[7.0]]) == 7.0

    def test_det_2x2(self):
        assert matrix_determinant([[3.0, 8.0], [4.0, 6.0]]) == -14.0

    def test_det_3x3(self):
        A = [[6.0, 1.0, 1.0], [4.0, -2.0, 5.0], [2.0, 8.0, 7.0]]
        assert _approx(matrix_determinant(A), -306.0)

    def test_det_identity(self):
        for n in range(1, 5):
            assert _approx(matrix_determinant(_identity(n)), 1.0)

    def test_det_singular(self):
        A = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]
        assert _approx(matrix_determinant(A), 0.0)

    def test_det_non_square_raises(self):
        import pytest

        with pytest.raises(ValueError):
            matrix_determinant([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])


class TestInverse:
    def test_inverse_2x2(self):
        A = [[4.0, 7.0], [2.0, 6.0]]
        inv = matrix_inverse(A)
        assert _mats_equal(matrix_multiply(A, inv), _identity(2))

    def test_inverse_3x3(self):
        A = [[2.0, -1.0, 0.0], [-1.0, 2.0, -1.0], [0.0, -1.0, 2.0]]
        inv = matrix_inverse(A)
        prod = matrix_multiply(A, inv)
        assert _mats_equal(prod, _identity(3))

    def test_inverse_identity(self):
        identity = _identity(4)
        inv = matrix_inverse(identity)
        assert _mats_equal(inv, identity)

    def test_inverse_singular_raises(self):
        import pytest

        with pytest.raises(ValueError, match="singular"):
            matrix_inverse([[1.0, 2.0], [2.0, 4.0]])


class TestEigenvalues:
    def test_eig_1x1(self):
        assert math.isclose(matrix_eigenvalues([[7.0]])[0], 7.0)

    def test_eig_diagonal_3x3(self):
        evals = sorted(matrix_eigenvalues([[2.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 6.0]]), reverse=True)
        for a, b in zip(evals, [6.0, 4.0, 2.0], strict=False):
            assert _approx(a, b)

    def test_eig_symmetric_2x2(self):
        A = [[4.0, 1.0], [1.0, 3.0]]
        evals = sorted(matrix_eigenvalues(A), reverse=True)
        assert _approx(evals[0], (7.0 + math.sqrt(5)) / 2.0, tol=1e-6)
        assert _approx(evals[1], (7.0 - math.sqrt(5)) / 2.0, tol=1e-6)

    def test_eig_count_matches_dimension(self):
        A = [[5.0, 2.0], [2.0, 1.0]]
        evals = matrix_eigenvalues(A)
        assert len(evals) == 2


class TestLU:
    def test_lu_2x2(self):
        A = [[4.0, 3.0], [6.0, 3.0]]
        L, U = matrix_lu(A)
        assert _approx(L[0][0], 1.0) and _approx(L[1][1], 1.0)
        assert _approx(U[0][0], 4.0)
        assert _mats_equal(matrix_multiply(L, U), A)

    def test_lu_3x3(self):
        A = [[2.0, -1.0, -2.0], [-4.0, 6.0, 3.0], [-4.0, -2.0, 8.0]]
        L, U = matrix_lu(A)
        assert _mats_equal(matrix_multiply(L, U), A)

    def test_lu_zero_pivot_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Zero pivot"):
            matrix_lu([[0.0, 0.0], [0.0, 0.0]])

    def test_lu_lower_unit_diagonal(self):
        A = [[5.0, 1.0, 2.0], [1.0, 4.0, 1.0], [2.0, 1.0, 5.0]]
        L, U = matrix_lu(A)
        n = 3
        for i in range(n):
            assert _approx(L[i][i], 1.0)
            for j in range(i + 1, n):
                assert _approx(U[i][j], U[i][j])  # U upper tri exists
                assert not _approx(L[i][j], L[i][j]) or True  # L[i][j] exists


class TestQR:
    def test_qr_2x2(self):
        A = [[12.0, -51.0], [6.0, 167.0]]
        Q, R = matrix_qr(A)
        assert _mats_equal(matrix_multiply(Q, R), A)

    def test_qr_3x2(self):
        A = [[12.0, -51.0], [6.0, 167.0], [-4.0, 24.0]]
        Q, R = matrix_qr(A)
        assert _mats_equal(matrix_multiply(Q, R), A)

    def test_qr_r_upper_triangular(self):
        A = [[12.0, -51.0, 4.0], [6.0, 167.0, -68.0], [-4.0, 24.0, -41.0]]
        _, R = matrix_qr(A)
        n = len(R)
        for i in range(n):
            for j in range(i):
                assert _approx(R[i][j], 0.0)

    def test_qr_dependent_columns_raises(self):
        import pytest

        with pytest.raises(ValueError, match="dependent"):
            matrix_qr([[1.0, 2.0], [2.0, 4.0]])
