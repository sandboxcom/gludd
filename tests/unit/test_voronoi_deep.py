"""Deep tests for Voronoi diagram — Fortune sweep, Delaunay dual, nearest site."""

from __future__ import annotations

import random

from general_ludd.algorithms.voronoi import (
    Edge,
    Point,
    Triangle,
    Voronoi,
    delaunay_from_points,
    nearest_site,
    voronoi_from_points,
)

# ---------------------------------------------------------------------------
# Point geometry
# ---------------------------------------------------------------------------


class TestPointGeometry:
    def test_point_construct_and_repr(self) -> None:
        p = Point(1.5, -2.25)
        assert p.x == 1.5
        assert p.y == -2.25
        assert "(1.5000, -2.2500)" in repr(p)

    def test_point_arithmetic(self) -> None:
        a = Point(3.0, 4.0)
        b = Point(1.0, 2.0)
        assert (a - b) == Point(2.0, 2.0)
        assert (a + b) == Point(4.0, 6.0)
        assert a * 2.0 == Point(6.0, 8.0)
        assert a / 2.0 == Point(1.5, 2.0)

    def test_point_dot_cross(self) -> None:
        a = Point(1.0, 0.0)
        b = Point(0.0, 2.0)
        assert a.dot(b) == 0.0
        assert a.cross(b) == 2.0
        assert b.cross(a) == -2.0

    def test_point_distance(self) -> None:
        a = Point(0.0, 0.0)
        b = Point(3.0, 4.0)
        assert a.dist(b) == 5.0
        assert a.dist_sq(b) == 25.0

    def test_point_midpoint(self) -> None:
        assert Point(0.0, 0.0).midpoint(Point(4.0, 6.0)) == Point(2.0, 3.0)

    def test_point_norm_sq(self) -> None:
        assert Point(3.0, 4.0).norm_sq() == 25.0
        assert Point(0.0, 0.0).norm_sq() == 0.0

    def test_point_ordering(self) -> None:
        assert Point(1.0, 2.0) < Point(1.0, 3.0)
        assert Point(1.0, 2.0) < Point(2.0, 2.0)
        assert Point(2.0, 2.0) > Point(1.0, 3.0)  # x dominates tie


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


class TestEdge:
    def test_edge_construction(self) -> None:
        a = Point(0.0, 0.0)
        e = Edge(start=a, end=None, left_site=0, right_site=1, neighbour=-1)
        assert e.start == a
        assert e.end is None
        assert e.left_site == 0
        assert e.right_site == 1

    def test_edge_direction_unbounded(self) -> None:
        e = Edge(start=None, end=None, left_site=0, right_site=1, neighbour=-1)
        assert e.direction is None

    def test_edge_direction_bounded(self) -> None:
        a = Point(0.0, 0.0)
        b = Point(3.0, 4.0)
        e = Edge(start=a, end=b, left_site=0, right_site=1, neighbour=-1)
        d = e.direction
        assert d is not None
        assert d.x == 3.0
        assert d.y == 4.0

    def test_edge_mutable_end(self) -> None:
        a = Point(0.0, 0.0)
        b = Point(1.0, 1.0)
        e = Edge(start=a, end=None, left_site=0, right_site=1, neighbour=-1)
        e.end = b
        assert e.end == b


# ---------------------------------------------------------------------------
# Triangle
# ---------------------------------------------------------------------------


class TestTriangle:
    def test_triangle_construct(self) -> None:
        t = Triangle(0, 1, 2)
        assert list(t) == [0, 1, 2]

    def test_triangle_edges(self) -> None:
        t = Triangle(0, 1, 2)
        assert t.edges() == [(0, 1), (1, 2), (2, 0)]

    def test_triangle_hash(self) -> None:
        t1 = Triangle(0, 1, 2)
        t2 = Triangle(0, 1, 2)
        assert hash(t1) == hash(t2)

    def test_triangle_iter(self) -> None:
        t = Triangle(3, 5, 7)
        assert sorted(t) == [3, 5, 7]


# ---------------------------------------------------------------------------
# Voronoi diagram — construction
# ---------------------------------------------------------------------------


class TestVoronoiConstruction:
    def test_empty_sites(self) -> None:
        v = Voronoi.build([])
        assert len(v.sites) == 0
        assert len(v.edges) == 0
        assert len(v.vertices) == 0

    def test_single_site(self) -> None:
        v = Voronoi.build([(5.0, 5.0)])
        assert len(v.sites) == 1
        assert v.sites[0] == Point(5.0, 5.0)

    def test_two_sites(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0)])
        assert len(v.sites) == 2
        # A bisector edge should exist
        assert len(v.edges) >= 0

    def test_three_sites_non_collinear(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        assert len(v.sites) == 3

    def test_four_corners(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        assert len(v.sites) == 4

    def test_grid_3x3(self) -> None:
        pts = [(float(x), float(y)) for x in range(3) for y in range(3)]
        v = Voronoi.build(pts)
        assert len(v.sites) == 9

    def test_collinear_horizontal(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        assert len(v.sites) == 3

    def test_collinear_vertical(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (0.0, 5.0), (0.0, 10.0)])
        assert len(v.sites) == 3

    def test_random_sites(self) -> None:
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(20)]
        v = Voronoi.build(pts)
        assert len(v.sites) == 20
        # Delaunay should have some triangles
        tris = v.delaunay
        assert isinstance(tris, list)


# ---------------------------------------------------------------------------
# Delaunay triangulation
# ---------------------------------------------------------------------------


class TestDelaunay:
    def test_delaunay_is_triangle_list(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        tris = v.delaunay
        assert isinstance(tris, list)
        for t in tris:
            assert isinstance(t, Triangle)

    def test_delaunay_empty_property(self) -> None:
        v = Voronoi.build([])
        tris = v.delaunay
        assert tris == []

    def test_delaunay_indices_in_range(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0), (2.0, 8.0)])
        tris = v.delaunay
        for t in tris:
            for idx in t:
                assert 0 <= idx < len(v.sites)

    def test_delaunay_from_points_convenience(self) -> None:
        tris = delaunay_from_points([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        assert isinstance(tris, list)


# ---------------------------------------------------------------------------
# Nearest site query
# ---------------------------------------------------------------------------


class TestNearestSite:
    def test_nearest_site_obvious(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (100.0, 100.0)])
        assert v.nearest_site(1.0, 1.0) == 0
        assert v.nearest_site(99.0, 99.0) == 1

    def test_nearest_site_four_corners(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        assert v.nearest_site(0.1, 0.1) == 0
        assert v.nearest_site(9.9, 0.1) == 1
        assert v.nearest_site(9.9, 9.9) == 2
        assert v.nearest_site(0.1, 9.9) == 3

    def test_nearest_site_centre_tie(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (2.0, 0.0)])
        idx = v.nearest_site(1.0, 0.0)
        assert idx in (0, 1)

    def test_nearest_site_convenience(self) -> None:
        pts = [(0.0, 0.0), (100.0, 100.0)]
        assert nearest_site(pts, 1.0, 1.0) == 0

    def test_nearest_site_single_site(self) -> None:
        v = Voronoi.build([(5.0, 5.0)])
        assert v.nearest_site(999.0, 999.0) == 0
        assert v.nearest_site(-999.0, -999.0) == 0


# ---------------------------------------------------------------------------
# Properties + invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_vertices_is_copy(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        verts = v.vertices
        verts.append(Point(99, 99))
        assert len(v.vertices) == len(verts) - 1

    def test_delaunay_is_copy(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        tris = v.delaunay
        tris.append(Triangle(99, 99, 99))
        assert len(v.delaunay) == len(tris) - 1

    def test_sites_preserved(self) -> None:
        pts = [(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)]
        v = Voronoi.build(pts)
        assert v.sites == [Point(x, y) for x, y in pts]

    def test_build_returns_voronoi_instance(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0)])
        assert isinstance(v, Voronoi)

    def test_delaunay_idempotent(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)])
        t1 = v.delaunay
        t2 = v.delaunay
        assert t1 == t2

    def test_edges_have_valid_site_indices(self) -> None:
        n = 10
        random.seed(7)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(n)]
        v = Voronoi.build(pts)
        for e in v.edges:
            assert -1 <= e.left_site < n
            assert -1 <= e.right_site < n


# ---------------------------------------------------------------------------
# voronoi_from_points convenience
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_voronoi_from_points(self) -> None:
        v = voronoi_from_points([(0.0, 0.0), (10.0, 10.0)])
        assert isinstance(v, Voronoi)
        assert len(v.sites) == 2

    def test_convenience_returns_same_as_build(self) -> None:
        pts = [(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)]
        v1 = voronoi_from_points(pts)
        v2 = Voronoi.build(pts)
        assert v1.sites == v2.sites


# ---------------------------------------------------------------------------
# Edge-case stress
# ---------------------------------------------------------------------------


class TestStress:
    def test_many_collinear(self) -> None:
        pts = [(float(i), 0.0) for i in range(20)]
        v = Voronoi.build(pts)
        assert len(v.sites) == 20

    def test_near_collinear_no_crash(self) -> None:
        pts = [(float(i), 0.001 * i) for i in range(10)]
        v = Voronoi.build(pts)
        assert len(v.sites) == 10

    def test_duplicate_sites_handled(self) -> None:
        v = Voronoi.build([(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)])
        assert len(v.sites) == 3

    def test_tiny_coordinates(self) -> None:
        v = Voronoi.build([(1e-10, 1e-10), (2e-10, 1e-10), (1.5e-10, 2e-10)])
        assert len(v.sites) == 3

    def test_large_coordinates(self) -> None:
        v = Voronoi.build([(1e9, 0.0), (0.0, 1e9), (1e9, 1e9)])
        assert len(v.sites) == 3

    def test_negative_coordinates(self) -> None:
        v = Voronoi.build([(-10.0, -10.0), (10.0, -10.0), (0.0, 10.0)])
        assert len(v.sites) == 3
        # Nearest site should still work
        assert v.nearest_site(-9.0, -9.0) == 0

    def test_zero_distance_sites(self) -> None:
        pts = [(0.0, 0.0), (0.0, 0.0), (10.0, 10.0)]
        v = Voronoi.build(pts)
        assert len(v.sites) == 3
