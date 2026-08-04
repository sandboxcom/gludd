"""Deep tests for polygon triangulation and geometric queries:
ear clipping, monotone partition, area, centroid, point-in-polygon.
"""

from __future__ import annotations

import math

from general_ludd.algorithms.polygon import (
    area,
    centroid,
    ear_clipping,
    is_monotone,
    is_simple,
    monotone_triangulate,
    point_in_polygon,
    triangulate,
)

# ── shared helpers ──────────────────────────────────────────────────

SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
SQUARE_CW = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
TRIANGLE = [(0.0, 0.0), (2.0, 0.0), (0.0, 3.0)]
PENTAGON = [(0.0, 0.0), (2.0, 0.0), (2.5, 1.5), (1.0, 2.5), (-0.5, 1.5)]
HEXAGON = [
    (1.0, 0.0),
    (0.5, math.sqrt(3) / 2),
    (-0.5, math.sqrt(3) / 2),
    (-1.0, 0.0),
    (-0.5, -math.sqrt(3) / 2),
    (0.5, -math.sqrt(3) / 2),
]

# A simple concave polygon (arrowhead shape)
CONCAVE = [(0.0, 0.0), (4.0, 0.0), (2.0, 1.0), (4.0, 2.0), (0.0, 2.0)]

# Regular octagon (y-monotone)
OCTAGON = []
for k in range(8):
    a = math.pi / 4 * k + math.pi / 8
    OCTAGON.append((math.cos(a), math.sin(a)))

# A non-y-monotone polygon (crescent-like dip)
NON_MONOTONE = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (3.0, 1.0), (4.0, -1.0), (4.0, -3.0), (0.0, -3.0), (0.0, 3.0)]


def _tri_area(poly: list[tuple[float, float]], tri: tuple[int, int, int]) -> float:
    a, b, c = poly[tri[0]], poly[tri[1]], poly[tri[2]]
    return 0.5 * abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))


def _total_tri_area(poly: list[tuple[float, float]], tris: list[tuple[int, int, int]]) -> float:
    return sum(_tri_area(poly, t) for t in tris)


def _all_vertices_covered(n: int, tris: list[tuple[int, int, int]]) -> bool:
    seen: set[int] = set()
    for t in tris:
        seen.update(t)
    return seen == set(range(n))


def _orient_tri(poly: list[tuple[float, float]], tri: tuple[int, int, int]) -> float:
    a, b, c = poly[tri[0]], poly[tri[1]], poly[tri[2]]
    return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])


# ── ear clipping ────────────────────────────────────────────────────


class TestEarClipping:
    def test_square(self) -> None:
        tris = ear_clipping(SQUARE)
        assert len(tris) == 2
        assert _all_vertices_covered(4, tris)
        assert math.isclose(_total_tri_area(SQUARE, tris), 1.0)

    def test_triangle(self) -> None:
        tris = ear_clipping(TRIANGLE)
        assert len(tris) == 1
        assert tris == [(0, 1, 2)] or tris == [(1, 2, 0)] or tris == [(2, 0, 1)]

    def test_pentagon(self) -> None:
        tris = ear_clipping(PENTAGON)
        assert len(tris) == 3
        assert _all_vertices_covered(5, tris)
        assert math.isclose(_total_tri_area(PENTAGON, tris), area(PENTAGON))

    def test_concave(self) -> None:
        tris = ear_clipping(CONCAVE)
        assert len(tris) == 3
        assert _all_vertices_covered(5, tris)
        assert math.isclose(_total_tri_area(CONCAVE, tris), area(CONCAVE))

    def test_ccw_orientation(self) -> None:
        tris = ear_clipping(SQUARE)
        for t in tris:
            assert _orient_tri(SQUARE, t) > 0

    def test_clockwise_input_reversed(self) -> None:
        tris = ear_clipping(SQUARE_CW)
        assert len(tris) == 2
        assert _all_vertices_covered(4, tris)
        ccw_verts = list(reversed(SQUARE_CW))
        assert math.isclose(_total_tri_area(ccw_verts, tris), 1.0)
        for t in tris:
            assert _orient_tri(ccw_verts, t) > 0

    def test_degenerate_2_points(self) -> None:
        assert ear_clipping([(0.0, 0.0), (1.0, 1.0)]) == []

    def test_degenerate_empty(self) -> None:
        assert ear_clipping([]) == []

    def test_n_minus_2_triangles(self) -> None:
        for pts in (SQUARE, PENTAGON, CONCAVE):
            tris = ear_clipping(pts)
            assert len(tris) == len(pts) - 2


# ── monotone / sweep ────────────────────────────────────────────────


class TestMonotone:
    def test_is_monotone_true(self) -> None:
        assert is_monotone(SQUARE)
        assert is_monotone(HEXAGON)
        assert is_monotone(OCTAGON)

    def test_is_monotone_false(self) -> None:
        assert not is_monotone(NON_MONOTONE)

    def test_monotone_triangulate_y_monotone(self) -> None:
        tris = monotone_triangulate(OCTAGON)
        assert len(tris) == 6
        assert _all_vertices_covered(8, tris)
        assert math.isclose(_total_tri_area(OCTAGON, tris), area(OCTAGON))

    def test_monotone_triangulate_hexagon(self) -> None:
        tris = monotone_triangulate(HEXAGON)
        assert len(tris) == 4
        assert _all_vertices_covered(6, tris)

    def test_monotone_falls_back_to_ear(self) -> None:
        tris = monotone_triangulate(CONCAVE)
        assert len(tris) == 3
        assert math.isclose(_total_tri_area(CONCAVE, tris), area(CONCAVE))


# ── area ────────────────────────────────────────────────────────────


class TestArea:
    def test_unit_square(self) -> None:
        assert math.isclose(area(SQUARE), 1.0)

    def test_triangle_formula(self) -> None:
        assert math.isclose(area(TRIANGLE), 3.0)

    def test_ccw_positive(self) -> None:
        assert area(SQUARE) > 0

    def test_cw_negative(self) -> None:
        assert area(SQUARE_CW) < 0

    def test_degenerate_1_point(self) -> None:
        assert area([(1.0, 2.0)]) == 0.0

    def test_degenerate_empty(self) -> None:
        assert area([]) == 0.0

    def test_regular_octagon_positive(self) -> None:
        assert area(OCTAGON) > 0.0

    def test_collinear_triangle_zero(self) -> None:
        assert math.isclose(area([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]), 0.0)


# ── centroid ────────────────────────────────────────────────────────


class TestCentroid:
    def test_unit_square(self) -> None:
        cx, cy = centroid(SQUARE)
        assert math.isclose(cx, 0.5)
        assert math.isclose(cy, 0.5)

    def test_isosceles_triangle(self) -> None:
        cx, cy = centroid(TRIANGLE)
        assert math.isclose(cx, 2.0 / 3.0)
        assert math.isclose(cy, 1.0)

    def test_regular_polygon_near_origin(self) -> None:
        cx, cy = centroid(OCTAGON)
        assert math.isclose(cx, 0.0, abs_tol=1e-12)
        assert math.isclose(cy, 0.0, abs_tol=1e-12)

    def test_single_vertex(self) -> None:
        assert centroid([(3.0, 4.0)]) == (3.0, 4.0)

    def test_line_segment(self) -> None:
        cx, cy = centroid([(0.0, 0.0), (4.0, 6.0)])
        assert math.isclose(cx, 2.0)
        assert math.isclose(cy, 3.0)

    def test_empty(self) -> None:
        assert centroid([]) == (0.0, 0.0)

    def test_zero_area_polygon(self) -> None:
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (0.0, 0.0)]
        cx, cy = centroid(pts)
        assert math.isclose(cx, 0.5)
        assert math.isclose(cy, 0.0)


# ── point-in-polygon ────────────────────────────────────────────────


class TestPointInPolygon:
    def test_inside_square(self) -> None:
        assert point_in_polygon((0.5, 0.5), SQUARE)

    def test_outside_square(self) -> None:
        assert not point_in_polygon((2.0, 2.0), SQUARE)

    def test_on_boundary_edge(self) -> None:
        assert point_in_polygon((0.5, 0.0), SQUARE)

    def test_on_vertex(self) -> None:
        assert point_in_polygon((0.0, 0.0), SQUARE)

    def test_inside_concave(self) -> None:
        assert point_in_polygon((1.0, 1.0), CONCAVE)

    def test_outside_concave(self) -> None:
        assert not point_in_polygon((3.0, 1.0), CONCAVE)

    def test_degenerate_2_point(self) -> None:
        assert not point_in_polygon((0.5, 0.5), [(0.0, 0.0), (1.0, 1.0)])

    def test_ray_through_vertex(self) -> None:
        diamond = [(0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0)]
        assert point_in_polygon((0.0, 0.0), diamond)


# ── is_simple ───────────────────────────────────────────────────────


class TestIsSimple:
    def test_convex_simple(self) -> None:
        assert is_simple(SQUARE)
        assert is_simple(HEXAGON)
        assert is_simple(OCTAGON)

    def test_concave_simple(self) -> None:
        assert is_simple(CONCAVE)

    def test_self_intersecting(self) -> None:
        bowtie = [(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)]
        assert not is_simple(bowtie)

    def test_2_point_simple(self) -> None:
        assert is_simple([(0.0, 0.0), (1.0, 1.0)])


# ── triangulate convenience ─────────────────────────────────────────


class TestTriangulate:
    def test_returns_same_as_ear(self) -> None:
        assert triangulate(SQUARE) == ear_clipping(SQUARE)

    def test_concave_triangulation(self) -> None:
        tris = triangulate(CONCAVE)
        assert len(tris) == 3
        assert math.isclose(_total_tri_area(CONCAVE, tris), area(CONCAVE))
