"""Deep interpolation / resampling tests.

Covers linear, cubic spline, nearest-neighbour, bilinear, trilinear,
and extrapolation-guard behaviour — all backed by scipy.interpolate.
"""

from __future__ import annotations

import math

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils.interpolation import (
    bilinear_interp,
    cubic_spline_coeffs,
    cubic_spline_eval,
    linear_interp_1d,
    nearest_interp_1d,
    trilinear_interp,
)

# ---------------------------------------------------------------------------
# linear interpolation
# ---------------------------------------------------------------------------


class TestLinearInterpolation1D:
    def test_endpoints(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 2.0, 4.0, 6.0]
        assert linear_interp_1d(xs, ys, 0.0) == 0.0
        assert linear_interp_1d(xs, ys, 3.0) == 6.0

    def test_midpoints(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 2.0, 4.0, 6.0]
        assert linear_interp_1d(xs, ys, 0.5) == 1.0
        assert linear_interp_1d(xs, ys, 1.5) == 3.0
        assert linear_interp_1d(xs, ys, 2.5) == 5.0

    def test_identity(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 1.0, 2.0, 3.0]
        assert linear_interp_1d(xs, ys, 0.7) == pytest.approx(0.7)
        assert linear_interp_1d(xs, ys, 2.3) == pytest.approx(2.3)

    def test_uneven_spacing(self):
        xs = [0.0, 0.5, 2.0, 5.0]
        ys = [10.0, 12.0, 8.0, 2.0]
        assert linear_interp_1d(xs, ys, 0.25) == pytest.approx(11.0)
        assert linear_interp_1d(xs, ys, 1.25) == pytest.approx(10.0)

    def test_extrapolation_raises(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        with pytest.raises(ValueError, match="outside data range"):
            linear_interp_1d(xs, ys, -0.1)
        with pytest.raises(ValueError, match="outside data range"):
            linear_interp_1d(xs, ys, 1.1)

    def test_two_points(self):
        assert linear_interp_1d([0.0, 10.0], [0.0, 100.0], 7.0) == pytest.approx(70.0)

    def test_insufficient_points(self):
        with pytest.raises(ValueError, match="need at least 2"):
            linear_interp_1d([1.0], [1.0], 1.0)


# ---------------------------------------------------------------------------
# cubic spline interpolation
# ---------------------------------------------------------------------------


class TestCubicSpline:
    def test_spline_identity(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [0.0, 1.0, 2.0, 3.0, 4.0]
        spline, xs_out = cubic_spline_coeffs(xs, ys)
        for x in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
            assert cubic_spline_eval(xs_out, spline, x) == pytest.approx(x, abs=1e-10)

    def test_spline_parabola(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 1.0, 4.0, 9.0]
        spline, xs_out = cubic_spline_coeffs(xs, ys)
        assert cubic_spline_eval(xs_out, spline, 0.0) == pytest.approx(0.0, abs=1e-10)
        assert cubic_spline_eval(xs_out, spline, 1.0) == pytest.approx(1.0, abs=1e-10)
        assert cubic_spline_eval(xs_out, spline, 2.0) == pytest.approx(4.0, abs=1e-10)
        mid = cubic_spline_eval(xs_out, spline, 0.5)
        assert 0.0 < mid < 1.0

    def test_spline_sinusoid_approximation(self):
        xs = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2, 2 * math.pi]
        ys = [math.sin(x) for x in xs]
        spline, xs_out = cubic_spline_coeffs(xs, ys)
        for x in [math.pi / 4, math.pi, 5 * math.pi / 4]:
            val = cubic_spline_eval(xs_out, spline, x)
            assert val == pytest.approx(math.sin(x), abs=0.3)

    def test_spline_extrapolation_raises(self):
        xs = [0.0, 1.0, 2.0]
        ys = [0.0, 1.0, 0.0]
        spline, xs_out = cubic_spline_coeffs(xs, ys)
        with pytest.raises(ValueError, match="outside spline domain"):
            cubic_spline_eval(xs_out, spline, -0.5)

    def test_spline_outputs(self):
        spline, _xs_out = cubic_spline_coeffs([0.0, 1.0, 2.0], [0.0, 2.0, 0.0])
        assert spline.c.shape[1] == 2


# ---------------------------------------------------------------------------
# nearest-neighbour
# ---------------------------------------------------------------------------


class TestNearestInterpolation:
    def test_exact_match(self):
        xs = [0.0, 1.0, 2.0]
        ys = [10.0, 20.0, 30.0]
        assert nearest_interp_1d(xs, ys, 1.0) == 20.0

    def test_halfway_left(self):
        xs = [0.0, 2.0]
        ys = [100.0, 200.0]
        assert nearest_interp_1d(xs, ys, 0.99) == 100.0

    def test_halfway_right(self):
        xs = [0.0, 2.0]
        ys = [100.0, 200.0]
        assert nearest_interp_1d(xs, ys, 1.01) == 200.0

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty data"):
            nearest_interp_1d([], [], 5.0)

    def test_single_point(self):
        assert nearest_interp_1d([7.0], [42.0], 7.0) == 42.0
        assert nearest_interp_1d([7.0], [42.0], 999.0) == 42.0


# ---------------------------------------------------------------------------
# bilinear
# ---------------------------------------------------------------------------


class TestBilinearInterpolation:
    def test_corners(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[10.0, 20.0], [30.0, 40.0]]
        assert bilinear_interp(xs, ys, z, 0.0, 0.0) == 10.0
        assert bilinear_interp(xs, ys, z, 1.0, 0.0) == 20.0
        assert bilinear_interp(xs, ys, z, 0.0, 1.0) == 30.0
        assert bilinear_interp(xs, ys, z, 1.0, 1.0) == 40.0

    def test_center(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[10.0, 20.0], [30.0, 40.0]]
        assert bilinear_interp(xs, ys, z, 0.5, 0.5) == pytest.approx(25.0)

    def test_3x3_grid(self):
        xs = [0.0, 1.0, 2.0]
        ys = [0.0, 1.0, 2.0]
        z = [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]]
        assert bilinear_interp(xs, ys, z, 0.5, 0.5) == pytest.approx(1.0)
        assert bilinear_interp(xs, ys, z, 1.5, 1.5) == pytest.approx(3.0)

    def test_extrapolation_raises(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        z = [[0.0, 1.0], [1.0, 2.0]]
        with pytest.raises(ValueError, match="outside grid bounds"):
            bilinear_interp(xs, ys, z, -0.1, 0.5)
        with pytest.raises(ValueError, match="outside grid bounds"):
            bilinear_interp(xs, ys, z, 0.5, 1.1)

    def test_small_grid_raises(self):
        with pytest.raises(ValueError, match="at least 2x2"):
            bilinear_interp([0.0], [0.0], [[1.0]], 0.0, 0.0)


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
        assert trilinear_interp(xs, ys, zs, v, 0.0, 0.0, 0.0) == 10.0
        assert trilinear_interp(xs, ys, zs, v, 1.0, 1.0, 1.0) == 80.0
        assert trilinear_interp(xs, ys, zs, v, 0.0, 0.0, 1.0) == 50.0

    def test_center(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        zs = [0.0, 1.0]
        v = [
            [[10.0, 20.0], [30.0, 40.0]],
            [[50.0, 60.0], [70.0, 80.0]],
        ]
        result = trilinear_interp(xs, ys, zs, v, 0.5, 0.5, 0.5)
        assert result == pytest.approx(45.0)

    def test_extrapolation_raises_x(self):
        xs = [0.0, 1.0]
        ys = [0.0, 1.0]
        zs = [0.0, 1.0]
        v = [[[0.0, 0.0], [0.0, 0.0]], [[0.0, 0.0], [0.0, 0.0]]]
        with pytest.raises(ValueError, match="outside grid bounds"):
            trilinear_interp(xs, ys, zs, v, -0.1, 0.5, 0.5)

    def test_small_grid_raises(self):
        with pytest.raises(ValueError, match="at least 2x2x2"):
            trilinear_interp([0.0], [0.0], [0.0], [[[1.0]]], 0.0, 0.0, 0.0)
