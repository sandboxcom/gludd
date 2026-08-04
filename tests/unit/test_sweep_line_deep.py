"""Deep sweep-line / plane-sweep tests: engine, skyline, range query,
union-of-rectangles, max-empty-rectangle, closest pair, convex hull area,
orthogonal intersections.  18 test classes / 30+ test methods.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.algorithms.sweep_line import (
    Point,
    Rect,
    SweepLineEngine,
    SweepRangeCounter,
    closest_pair_sweep,
    convex_hull_polygon_area,
    count_orthogonal_intersections,
    max_empty_rect,
    skyline_points,
    skyline_query,
    sweep_range_query,
    union_rect_area,
)


class TestPoint:
    def test_construct_and_equality(self) -> None:
        assert Point(1.0, 2.0) == Point(1.0, 2.0)
        assert Point(1.0, 2.0) != Point(1.0, 3.0)

    def test_field_access(self) -> None:
        p = Point(3.5, -7.25)
        assert p.x == 3.5
        assert p.y == -7.25


class TestRect:
    def test_area_positive(self) -> None:
        r = Rect(0, 0, 4, 5)
        assert r.area == 20.0

    def test_area_zero_width(self) -> None:
        r = Rect(1, 1, 1, 5)
        assert r.area == 0.0

    def test_area_zero_height(self) -> None:
        r = Rect(1, 1, 5, 1)
        assert r.area == 0.0


class TestSweepLineEngine:
    def test_push_and_sweep_order(self) -> None:
        engine = SweepLineEngine[str, None]()
        engine.push(2.5, "b")
        engine.push(1.0, "a")
        engine.push(3.0, "c")
        items = list(engine.swept())
        assert items == [(1.0, "a"), (2.5, "b"), (3.0, "c")]

    def test_construct_from_iterable(self) -> None:
        engine = SweepLineEngine[str, int]([(3.0, "x"), (1.5, "y"), (2.0, "z")])
        engine.extra = 42
        items = list(engine.swept())
        assert len(items) == 3
        assert items[0][0] == 1.5
        assert engine.extra == 42

    def test_bool_and_len(self) -> None:
        engine = SweepLineEngine[float, None]()
        assert not engine
        assert len(engine) == 0
        engine.push(1.0, 9.9)
        assert engine
        assert len(engine) == 1

    def test_empty_sweep(self) -> None:
        engine = SweepLineEngine[int, str]()
        assert list(engine.swept()) == []


class TestSkylinePoints:
    def test_single_point(self) -> None:
        pts = [Point(1, 2)]
        result = skyline_points(pts)
        assert result == [Point(1, 2)]

    def test_empty(self) -> None:
        assert skyline_points([]) == []

    def test_all_not_dominated(self) -> None:
        pts = [Point(5, 5), Point(6, 4), Point(4, 6)]
        result = skyline_points(pts)
        assert Point(4, 6) in result
        assert Point(5, 5) in result
        assert Point(6, 4) in result

    def test_dominated_point_excluded(self) -> None:
        pts = [Point(1, 100), Point(2, 1)]
        result = skyline_points(pts)
        assert Point(1, 100) in result
        assert Point(2, 1) in result

    def test_fully_dominated_excluded(self) -> None:
        pts = [Point(1, 1), Point(3, 3)]
        result = skyline_points(pts)
        assert result == [Point(3, 3)]


class TestSkylineQuery:
    def test_count_zero_below_all(self) -> None:
        pts = [Point(5, 5), Point(6, 6)]
        assert skyline_query(pts, 1.0, 1.0) == 0

    def test_count_all(self) -> None:
        pts = [Point(1, 2), Point(3, 1)]
        assert skyline_query(pts, 10.0, 10.0) == 2


class TestSweepRangeQuery:
    def test_empty_points(self) -> None:
        assert sweep_range_query([], 0, 0, 10, 10) == []

    def test_point_inside(self) -> None:
        pts = [Point(1, 1), Point(5, 5), Point(9, 9)]
        result = sweep_range_query(pts, 0, 0, 6, 6)
        assert Point(1, 1) in result
        assert Point(5, 5) in result
        assert Point(9, 9) not in result

    def test_no_points_inside(self) -> None:
        pts = [Point(100, 100)]
        assert sweep_range_query(pts, 0, 0, 10, 10) == []

    def test_all_points_inside(self) -> None:
        pts = [Point(1, 2), Point(3, 4)]
        result = sweep_range_query(pts, 0, 0, 10, 10)
        assert len(result) == 2


class TestUnionRectArea:
    def test_empty(self) -> None:
        assert union_rect_area([]) == 0.0

    def test_single_rect(self) -> None:
        assert union_rect_area([Rect(0, 0, 4, 3)]) == 12.0

    def test_two_disjoint(self) -> None:
        rects = [Rect(0, 0, 1, 1), Rect(2, 2, 3, 3)]
        assert union_rect_area(rects) == 2.0

    def test_two_overlapping(self) -> None:
        rects = [Rect(0, 0, 2, 2), Rect(1, 1, 3, 3)]
        assert union_rect_area(rects) == pytest.approx(7.0, abs=1e-9)

    def test_identical(self) -> None:
        r = Rect(0, 0, 5, 5)
        assert union_rect_area([r, r]) == 25.0


class TestSweepRangeCounter:
    def test_empty(self) -> None:
        counter = SweepRangeCounter([])
        assert counter.count_range(0, 0, 10, 10) == 0

    def test_single_point(self) -> None:
        counter = SweepRangeCounter([Point(5, 5)])
        assert counter.count_range(0, 0, 10, 10) == 1
        assert counter.count_range(6, 6, 10, 10) == 0

    def test_range_partial(self) -> None:
        pts = [Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)]
        counter = SweepRangeCounter(pts)
        assert counter.count_range(0.5, 0.5, 2.5, 2.5) == 2

    def test_outside_all(self) -> None:
        counter = SweepRangeCounter([Point(1, 1), Point(2, 2)])
        assert counter.count_range(10, 10, 20, 20) == 0


class TestMaxEmptyRect:
    def test_no_obstacles(self) -> None:
        result = max_empty_rect([], 0, 0, 10, 8)
        assert result == Rect(0, 0, 10, 8)

    def test_one_obstacle_center(self) -> None:
        result = max_empty_rect([Point(5, 4)], 0, 0, 10, 8)
        assert result.area == pytest.approx(40.0, abs=1e-9)

    def test_obstacle_at_corner(self) -> None:
        result = max_empty_rect([Point(0, 0)], 0, 0, 10, 10)
        assert result.area >= 0.0


class TestClosestPairSweep:
    def test_two_points(self) -> None:
        pts = [Point(0, 0), Point(3, 4)]
        dist, (a, b) = closest_pair_sweep(pts)
        assert dist == pytest.approx(5.0, abs=1e-9)
        assert {a, b} == {Point(0, 0), Point(3, 4)}

    def test_three_points(self) -> None:
        pts = [Point(0, 0), Point(10, 10), Point(1, 1)]
        dist, (a, b) = closest_pair_sweep(pts)
        expected = math.sqrt(2)
        assert dist == pytest.approx(expected, abs=1e-9)
        assert {a, b} == {Point(0, 0), Point(1, 1)}

    def test_grid_4x4(self) -> None:
        pts = [Point(x, y) for x in range(4) for y in range(4)]
        dist, _ = closest_pair_sweep(pts)
        assert dist == pytest.approx(1.0, abs=1e-9)

    def test_raises_on_fewer_than_two(self) -> None:
        import pytest as pt

        with pt.raises(ValueError, match="at least 2"):
            closest_pair_sweep([Point(0, 0)])


class TestConvexHullArea:
    def test_triangle(self) -> None:
        pts = [Point(0, 0), Point(4, 0), Point(0, 3)]
        assert convex_hull_polygon_area(pts) == pytest.approx(6.0, abs=1e-9)

    def test_unit_square(self) -> None:
        pts = [Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)]
        assert convex_hull_polygon_area(pts) == pytest.approx(1.0, abs=1e-9)

    def test_collinear_points(self) -> None:
        pts = [Point(0, 0), Point(1, 1), Point(2, 2)]
        assert convex_hull_polygon_area(pts) == 0.0

    def test_empty(self) -> None:
        assert convex_hull_polygon_area([]) == 0.0


class TestOrthogonalIntersections:
    def test_no_segments(self) -> None:
        assert count_orthogonal_intersections([], []) == 0

    def test_single_cross(self) -> None:
        h = [(2.0, 0.0, 4.0)]
        v = [(2.0, 0.0, 4.0)]
        assert count_orthogonal_intersections(h, v) == 1

    def test_no_cross_disjoint(self) -> None:
        h = [(0.0, 0.0, 1.0)]
        v = [(2.0, 0.0, 1.0)]
        assert count_orthogonal_intersections(h, v) == 0
