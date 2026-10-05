"""Deep tests for convex hull algorithms: Graham scan, Jarvis march,
QuickHull, and Chan's algorithm.
"""

from __future__ import annotations

import math

from ansible_collections.general_ludd.physics.plugins.module_utils.convex_hull import (
    chans_algorithm,
    graham_scan,
    jarvis_march,
    quickhull,
)

# ── shared helpers ──────────────────────────────────────────────────


def _is_ccw(hull: list[tuple[float, float]]) -> bool:
    if len(hull) < 3:
        return True
    s = 0.0
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        s += (x2 - x1) * (y2 + y1)
    return s < 0


def _hull_contains_all(points: list[tuple[float, float]], hull: list[tuple[float, float]]) -> bool:
    if len(hull) <= 2:
        return True
    n = len(hull)
    for px, py in points:
        inside = True
        for i in range(n):
            x1, y1 = hull[i]
            x2, y2 = hull[(i + 1) % n]
            if (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) < -1e-12:
                inside = False
                break
        if not inside:
            return False
    return True


def _hull_set(hull: list[tuple[float, float]]) -> set[tuple[float, float]]:
    return set(hull)


SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
TRIANGLE = [(0.0, 0.0), (2.0, 0.0), (1.0, 2.0)]
COLLINEAR = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]

# 25-point broad distribution
SCATTER = [
    (0.1, 0.2),
    (0.9, 0.1),
    (0.95, 0.8),
    (0.5, 0.95),
    (0.05, 0.7),
    (0.3, 0.4),
    (0.6, 0.3),
    (0.7, 0.6),
    (0.4, 0.7),
    (0.2, 0.5),
    (0.55, 0.55),
    (0.45, 0.45),
    (0.35, 0.35),
    (0.65, 0.45),
    (0.5, 0.3),
    (0.25, 0.6),
    (0.75, 0.25),
    (0.15, 0.15),
    (0.85, 0.65),
    (0.5, 0.5),
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
    (0.3, 0.8),
]


# ── Graham scan ─────────────────────────────────────────────────────


class TestGrahamScan:
    def test_empty(self) -> None:
        assert graham_scan([]) == []

    def test_single_point(self) -> None:
        assert graham_scan([(3.0, 4.0)]) == [(3.0, 4.0)]

    def test_two_points(self) -> None:
        assert graham_scan([(0.0, 0.0), (1.0, 1.0)]) == [(0.0, 0.0), (1.0, 1.0)]

    def test_square(self) -> None:
        hull = graham_scan(SQUARE)
        assert len(hull) == 4
        assert _is_ccw(hull)
        assert _hull_set(hull) == _hull_set(SQUARE)

    def test_triangle(self) -> None:
        hull = graham_scan(TRIANGLE)
        assert len(hull) == 3
        assert _is_ccw(hull)
        assert _hull_set(hull) == _hull_set(TRIANGLE)

    def test_collinear(self) -> None:
        hull = graham_scan(COLLINEAR)
        assert len(hull) == 2

    def test_vertical_collinear_uses_y_extrema(self) -> None:
        points = [(2.0, 4.0), (2.0, -1.0), (2.0, 2.0)]
        assert _hull_set(graham_scan(points)) == {(2.0, -1.0), (2.0, 4.0)}

    def test_duplicate_points_collapse_to_one_vertex(self) -> None:
        points = [(3.0, 4.0), (3.0, 4.0), (3.0, 4.0)]
        assert graham_scan(points) == [(3.0, 4.0)]

    def test_point_inside(self) -> None:
        pts = [*SQUARE, (0.5, 0.5)]
        hull = graham_scan(pts)
        assert len(hull) == 4
        assert (0.5, 0.5) not in _hull_set(hull)

    def test_scatter(self) -> None:
        hull = graham_scan(SCATTER)
        assert _is_ccw(hull)
        assert _hull_contains_all(SCATTER, hull)

    def test_circle_approx(self) -> None:
        n = 50
        pts = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)) for i in range(n)]
        hull = graham_scan(pts)
        assert len(hull) == n
        assert _is_ccw(hull)


# ── Jarvis march ────────────────────────────────────────────────────


class TestJarvisMarch:
    def test_square(self) -> None:
        hull = jarvis_march(SQUARE)
        assert len(hull) == 4
        assert _hull_set(hull) == _hull_set(SQUARE)

    def test_triangle(self) -> None:
        hull = jarvis_march(TRIANGLE)
        assert len(hull) == 3
        assert _hull_set(hull) == _hull_set(TRIANGLE)

    def test_collinear(self) -> None:
        hull = jarvis_march(COLLINEAR)
        assert len(hull) == 2

    def test_scatter(self) -> None:
        hull = jarvis_march(SCATTER)
        assert _is_ccw(hull)
        assert _hull_contains_all(SCATTER, hull)


# ── QuickHull ───────────────────────────────────────────────────────


class TestQuickHull:
    def test_square(self) -> None:
        hull = quickhull(SQUARE)
        assert len(hull) == 4
        assert _hull_set(hull) == _hull_set(SQUARE)

    def test_triangle(self) -> None:
        hull = quickhull(TRIANGLE)
        assert len(hull) == 3
        assert _hull_set(hull) == _hull_set(TRIANGLE)

    def test_scatter(self) -> None:
        hull = quickhull(SCATTER)
        assert _is_ccw(hull)
        assert _hull_contains_all(SCATTER, hull)


# ── Chan's algorithm ────────────────────────────────────────────────


class TestChansAlgorithm:
    def test_square(self) -> None:
        hull = chans_algorithm(SQUARE)
        assert len(hull) == 4
        assert _hull_set(hull) == _hull_set(SQUARE)

    def test_triangle(self) -> None:
        hull = chans_algorithm(TRIANGLE)
        assert len(hull) == 3
        assert _hull_set(hull) == _hull_set(TRIANGLE)

    def test_scatter(self) -> None:
        hull = chans_algorithm(SCATTER)
        assert _is_ccw(hull)
        assert _hull_contains_all(SCATTER, hull)
