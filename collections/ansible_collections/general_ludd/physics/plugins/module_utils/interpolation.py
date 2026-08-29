"""Physics-collection interpolation adapters using SciPy."""

from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicSpline, RegularGridInterpolator, interp1d


def linear_interp_1d(xs: list[float], ys: list[float], x: float) -> float:
    if len(xs) < 2:
        raise ValueError("need at least 2 points for linear interpolation")
    if x < xs[0] or x > xs[-1]:
        raise ValueError(f"x={x} is outside data range [{xs[0]}, {xs[-1]}]")
    f = interp1d(np.array(xs, dtype=float), np.array(ys, dtype=float), kind="linear")
    return float(f(x))


def nearest_interp_1d(xs: list[float], ys: list[float], x: float) -> float:
    if not xs:
        raise ValueError("empty data")
    f = interp1d(
        np.array(xs, dtype=float),
        np.array(ys, dtype=float),
        kind="nearest",
        bounds_error=False,
        fill_value="extrapolate",
    )
    return float(f(x))


def cubic_spline_coeffs(xs: list[float], ys: list[float]) -> tuple[CubicSpline, list[float]]:
    if len(xs) < 2:
        raise ValueError("need at least 2 points")
    spline = CubicSpline(np.array(xs, dtype=float), np.array(ys, dtype=float), bc_type="natural")
    return spline, list(xs)


def cubic_spline_eval(
    xs: list[float],
    spline: CubicSpline,
    x: float,
) -> float:
    if x < xs[0] or x > xs[-1]:
        raise ValueError(f"x={x} outside spline domain [{xs[0]}, {xs[-1]}]")
    return float(spline(x))


def bilinear_interp(
    x_grid: list[float],
    y_grid: list[float],
    z: list[list[float]],
    x: float,
    y: float,
) -> float:
    nx, ny = len(x_grid), len(y_grid)
    if nx < 2 or ny < 2:
        raise ValueError("grid must be at least 2x2")
    if x < x_grid[0] or x > x_grid[-1] or y < y_grid[0] or y > y_grid[-1]:
        raise ValueError("point outside grid bounds")
    interp = RegularGridInterpolator(
        (np.array(y_grid, dtype=float), np.array(x_grid, dtype=float)),
        np.array(z, dtype=float),
        method="linear",
        bounds_error=True,
    )
    return float(interp((y, x)))


def trilinear_interp(
    x_grid: list[float],
    y_grid: list[float],
    z_grid: list[float],
    v: list[list[list[float]]],
    x: float,
    y: float,
    z_val: float,
) -> float:
    nx, ny, nz = len(x_grid), len(y_grid), len(z_grid)
    if nx < 2 or ny < 2 or nz < 2:
        raise ValueError("grid must be at least 2x2x2")
    if x < x_grid[0] or x > x_grid[-1] or y < y_grid[0] or y > y_grid[-1] or z_val < z_grid[0] or z_val > z_grid[-1]:
        raise ValueError("point outside grid bounds")
    interp = RegularGridInterpolator(
        (
            np.array(z_grid, dtype=float),
            np.array(y_grid, dtype=float),
            np.array(x_grid, dtype=float),
        ),
        np.array(v, dtype=float),
        method="linear",
        bounds_error=True,
    )
    return float(interp((z_val, y, x)))
