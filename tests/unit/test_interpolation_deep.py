"""Deep interpolation / resampling tests.

Covers linear, cubic spline, nearest-neighbour, bilinear, trilinear,
and extrapolation-guard behaviour.
"""

from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# pure-function interpolation routines under test
# ---------------------------------------------------------------------------


def _linear_interp_1d(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation on sorted *xs*."""
    if len(xs) < 2:
        raise ValueError("need at least 2 points for linear interpolation")
    if x < xs[0] or x > xs[-1]:
        raise ValueError(f"x={x} is outside data range [{xs[0]}, {xs[-1]}]")
    if x == xs[0]:
        return ys[0]
    if x == xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    raise RuntimeError("unreachable")


def _nearest_interp_1d(xs: list[float], ys: list[float], x: float) -> float:
    """Nearest-neighbour interpolation — returns *y* of the closest *x*."""
    if not xs:
        raise ValueError("empty data")
    best_idx = 0
    best_dist = abs(x - xs[0])
    for i in range(1, len(xs)):
        d = abs(x - xs[i])
        if d < best_dist:
            best_dist = d
            best_idx = i
    return ys[best_idx]


def _bilinear_interp(
    x_grid: list[float],
    y_grid: list[float],
    z: list[list[float]],
    x: float,
    y: float,
) -> float:
    """Bilinear interpolation on a regular 2-D grid.

    *z* is ``z[row][col]`` where *row* indexes *y_grid* and *col* indexes *x_grid*.
    """
    nx, ny = len(x_grid), len(y_grid)
    if nx < 2 or ny < 2:
        raise ValueError("grid must be at least 2x2")
    if x < x_grid[0] or x > x_grid[-1] or y < y_grid[0] or y > y_grid[-1]:
        raise ValueError("point outside grid bounds")

    xi = next(i for i in range(nx - 1) if x_grid[i] <= x <= x_grid[i + 1])
    yi = next(i for i in range(ny - 1) if y_grid[i] <= y <= y_grid[i + 1])

    tx = (x - x_grid[xi]) / (x_grid[xi + 1] - x_grid[xi])
    ty = (y - y_grid[yi]) / (y_grid[yi + 1] - y_grid[yi])

    z00, z10 = z[yi][xi], z[yi][xi + 1]
    z01, z11 = z[yi + 1][xi], z[yi + 1][xi + 1]

    z0 = z00 + tx * (z10 - z00)
    z1 = z01 + tx * (z11 - z01)
    return z0 + ty * (z1 - z0)


def _trilinear_interp(
    x_grid: list[float],
    y_grid: list[float],
    z_grid: list[float],
    v: list[list[list[float]]],
    x: float,
    y: float,
    z_val: float,
) -> float:
    """Trilinear interpolation on a regular 3-D grid.

    *v* is ``v[zi][yi][xi]``.
    """
    nx, ny, nz = len(x_grid), len(y_grid), len(z_grid)
    if nx < 2 or ny < 2 or nz < 2:
        raise ValueError("grid must be at least 2x2x2")
    if x < x_grid[0] or x > x_grid[-1] or y < y_grid[0] or y > y_grid[-1] or z_val < z_grid[0] or z_val > z_grid[-1]:
        raise ValueError("point outside grid bounds")

    xi = next(i for i in range(nx - 1) if x_grid[i] <= x <= x_grid[i + 1])
    yi = next(i for i in range(ny - 1) if y_grid[i] <= y <= y_grid[i + 1])
    zi = next(i for i in range(nz - 1) if z_grid[i] <= z_val <= z_grid[i + 1])

    tx = (x - x_grid[xi]) / (x_grid[xi + 1] - x_grid[xi])
    ty = (y - y_grid[yi]) / (y_grid[yi + 1] - y_grid[yi])
    tz = (z_val - z_grid[zi]) / (z_grid[zi + 1] - z_grid[zi])

    c00 = v[zi][yi][xi] + tx * (v[zi][yi][xi + 1] - v[zi][yi][xi])
    c10 = v[zi][yi + 1][xi] + tx * (v[zi][yi + 1][xi + 1] - v[zi][yi + 1][xi])
    c01 = v[zi + 1][yi][xi] + tx * (v[zi + 1][yi][xi + 1] - v[zi + 1][yi][xi])
    c11 = v[zi + 1][yi + 1][xi] + tx * (v[zi + 1][yi + 1][xi + 1] - v[zi + 1][yi + 1][xi])

    c0 = c00 + ty * (c10 - c00)
    c1 = c01 + ty * (c11 - c01)
    return c0 + tz * (c1 - c0)


def _cubic_spline_coeffs(xs: list[float], ys: list[float]) -> list[tuple[float, float, float, float]]:
    """Natural cubic spline coefficients for sorted *xs*.

    Returns ``(a, b, c, d)`` per segment s.t.
    ``S_i(t) = a_i + b_i*(t-x_i) + c_i*(t-x_i)^2 + d_i*(t-x_i)^3``.
    """
    n = len(xs)
    if n < 2:
        raise ValueError("need at least 2 points")
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    alpha = [0.0] * n
    for i in range(1, n - 1):
        alpha[i] = (3.0 / h[i]) * (ys[i + 1] - ys[i]) - (3.0 / h[i - 1]) * (ys[i] - ys[i - 1])

    li = [1.0] + [0.0] * (n - 1)
    mu = [0.0] * n
    z = [0.0] * n
    for i in range(1, n - 1):
        li[i] = 2.0 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1]
        if li[i] == 0:
            raise ValueError("singular spline system")
        mu[i] = h[i] / li[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / li[i]
    li[-1] = 1.0
    z[-1] = 0.0

    c = [0.0] * n
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]

    coeffs: list[tuple[float, float, float, float]] = []
    for i in range(n - 1):
        hi = h[i]
        a = ys[i]
        b = (ys[i + 1] - ys[i]) / hi - hi * (2.0 * c[i] + c[i + 1]) / 3.0
        d = (c[i + 1] - c[i]) / (3.0 * hi)
        coeffs.append((a, b, c[i], d))
    return coeffs


def _cubic_spline_eval(
    xs: list[float],
    coeffs: list[tuple[float, float, float, float]],
    x: float,
) -> float:
    """Evaluate natural cubic spline at *x*."""
    if x < xs[0] or x > xs[-1]:
        raise ValueError(f"x={x} outside spline domain [{xs[0]}, {xs[-1]}]")
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            dx = x - xs[i]
            a, b, c, d = coeffs[i]
            return a + b * dx + c * dx * dx + d * dx * dx * dx
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# linear interpolation
# ---------------------------------------------------------------------------


class TestLinearInterpolation1D:
    def test_endpoints(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 2.0, 4.0, 6.0]
        assert _linear_interp_1d(xs, ys, 0.0) == 0.0
        assert _linear_interp_1d(xs, ys, 3.0) == 6.0

    def test_midpoints(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 2.0, 4.0, 6.0]
        assert _linear_interp_1d(xs, ys, 0.5) == 1.0
        assert _linear_interp_1d(xs, ys, 1.5) == 3.0
        assert _linear_interp_1d(xs, ys, 2.5) == 5.0

    def test_identity(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 1.0, 2.0, 3.0]
        assert _linear_interp_1d(xs, ys, 0.7) == pytest.approx(0.7)
        assert _linear_interp_1d(xs, ys, 2.3) == pytest.approx(2.3)

    def test_uneven_spacing(self):
        xs = [0.0, 0.5, 2.0, 5.0]
        ys = [10.0, 12.0, 8.0, 2.0]
        assert _linear_interp_1d(xs, ys, 0.25) == pytest.approx(11.0)
        assert _linear_interp_1d(xs, ys, 1.25) == pytest.approx(10.0)

    def test_extrapolation_raises(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        with pytest.raises(ValueError, match="outside data range"):
            _linear_interp_1d(xs, ys, -0.1)
        with pytest.raises(ValueError, match="outside data range"):
            _linear_interp_1d(xs, ys, 1.1)

    def test_two_points(self):
        assert _linear_interp_1d([0.0, 10.0], [0.0, 100.0], 7.0) == pytest.approx(70.0)

    def test_insufficient_points(self):
        with pytest.raises(ValueError, match="need at least 2"):
            _linear_interp_1d([1.0], [1.0], 1.0)


# ---------------------------------------------------------------------------
# cubic spline interpolation
# ---------------------------------------------------------------------------


class TestCubicSpline:
    def test_spline_identity(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [0.0, 1.0, 2.0, 3.0, 4.0]
        coeffs = _cubic_spline_coeffs(xs, ys)
        for x in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
            assert _cubic_spline_eval(xs, coeffs, x) == pytest.approx(x, abs=1e-10)

    def test_spline_parabola(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 1.0, 4.0, 9.0]
        coeffs = _cubic_spline_coeffs(xs, ys)
        assert _cubic_spline_eval(xs, coeffs, 0.0) == pytest.approx(0.0, abs=1e-10)
        assert _cubic_spline_eval(xs, coeffs, 1.0) == pytest.approx(1.0, abs=1e-10)
        assert _cubic_spline_eval(xs, coeffs, 2.0) == pytest.approx(4.0, abs=1e-10)
        mid = _cubic_spline_eval(xs, coeffs, 0.5)
        assert 0.0 < mid < 1.0

    def test_spline_sinusoid_approximation(self):
        xs = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2, 2 * math.pi]
        ys = [math.sin(x) for x in xs]
        coeffs = _cubic_spline_coeffs(xs, ys)
        for x in [math.pi / 4, math.pi, 5 * math.pi / 4]:
            val = _cubic_spline_eval(xs, coeffs, x)
            assert val == pytest.approx(math.sin(x), abs=0.3)

    def test_spline_extrapolation_raises(self):
        xs = [0.0, 1.0, 2.0]
        ys = [0.0, 1.0, 0.0]
        coeffs = _cubic_spline_coeffs(xs, ys)
        with pytest.raises(ValueError, match="outside spline domain"):
            _cubic_spline_eval(xs, coeffs, -0.5)

    def test_spline_outputs(self):
        coeffs = _cubic_spline_coeffs([0.0, 1.0, 2.0], [0.0, 2.0, 0.0])
        assert len(coeffs) == 2
        assert len(coeffs[0]) == 4


# ---------------------------------------------------------------------------
# nearest-neighbour
# ---------------------------------------------------------------------------


class TestNearestInterpolation:
    def test_exact_match(self):
        xs = [0.0, 1.0, 2.0]
        ys = [10.0, 20.0, 30.0]
        assert _nearest_interp_1d(xs, ys, 1.0) == 20.0

    def test_halfway_left(self):
        xs = [0.0, 2.0]
        ys = [100.0, 200.0]
        assert _nearest_interp_1d(xs, ys, 0.99) == 100.0

    def test_halfway_right(self):
        xs = [0.0, 2.0]
        ys = [100.0, 200.0]
        assert _nearest_interp_1d(xs, ys, 1.01) == 200.0

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty data"):
            _nearest_interp_1d([], [], 5.0)

    def test_single_point(self):
        assert _nearest_interp_1d([7.0], [42.0], 7.0) == 42.0
        assert _nearest_interp_1d([7.0], [42.0], 999.0) == 42.0


# ---------------------------------------------------------------------------
# bilinear
# ---------------------------------------------------------------------------


class TestBilinearInterpolation:
    def test_corners(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[10.0, 20.0], [30.0, 40.0]]
        assert _bilinear_interp(xs, ys, z, 0.0, 0.0) == 10.0
        assert _bilinear_interp(xs, ys, z, 1.0, 0.0) == 20.0
        assert _bilinear_interp(xs, ys, z, 0.0, 1.0) == 30.0
        assert _bilinear_interp(xs, ys, z, 1.0, 1.0) == 40.0

    def test_center(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[10.0, 20.0], [30.0, 40.0]]
        assert _bilinear_interp(xs, ys, z, 0.5, 0.5) == pytest.approx(25.0)

    def test_3x3_grid(self):
        xs = [0.0, 1.0, 2.0]
        ys = [0.0, 1.0, 2.0]
        z = [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]]
        assert _bilinear_interp(xs, ys, z, 0.5, 0.5) == pytest.approx(1.0)
        assert _bilinear_interp(xs, ys, z, 1.5, 1.5) == pytest.approx(3.0)

    def test_extrapolation_raises(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[0.0, 1.0], [1.0, 2.0]]
        with pytest.raises(ValueError, match="outside grid bounds"):
            _bilinear_interp(xs, ys, z, -0.1, 0.5)
        with pytest.raises(ValueError, match="outside grid bounds"):
            _bilinear_interp(xs, ys, z, 0.5, 1.1)

    def test_small_grid_raises(self):
        with pytest.raises(ValueError, match="at least 2x2"):
            _bilinear_interp([0.0], [0.0], [[1.0]], 0.0, 0.0)


# ---------------------------------------------------------------------------
# trilinear
# ---------------------------------------------------------------------------


class TestTrilinearInterpolation:
    def test_corners(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        zs = [0.0, 1.0]
        v = [
            [[10.0, 20.0], [30.0, 40.0]],
            [[50.0, 60.0], [70.0, 80.0]],
        ]
        assert _trilinear_interp(xs, ys, zs, v, 0.0, 0.0, 0.0) == 10.0
        assert _trilinear_interp(xs, ys, zs, v, 1.0, 1.0, 1.0) == 80.0
        assert _trilinear_interp(xs, ys, zs, v, 0.0, 0.0, 1.0) == 50.0

    def test_center(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        zs = [0.0, 1.0]
        v = [
            [[10.0, 20.0], [30.0, 40.0]],
            [[50.0, 60.0], [70.0, 80.0]],
        ]
        result = _trilinear_interp(xs, ys, zs, v, 0.5, 0.5, 0.5)
        assert result == pytest.approx(45.0)

    def test_extrapolation_raises_x(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        zs = [0.0, 1.0]
        v = [[[0.0, 0.0], [0.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]]]
        with pytest.raises(ValueError, match="outside grid bounds"):
            _trilinear_interp(xs, ys, zs, v, -0.1, 0.5, 0.5)

    def test_small_grid_raises(self):
        with pytest.raises(ValueError, match="at least 2x2x2"):
            _trilinear_interp([0.0], [0.0], [0.0], [[[1.0]]], 0.0, 0.0, 0.0)
