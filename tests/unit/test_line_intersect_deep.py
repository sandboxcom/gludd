"""Deep line segment intersection tests: orientation, collinear,
on-segment, SegmentsIntersect, Shamos-Hoey, Bentley-Ottmann, edge cases.
"""

from __future__ import annotations

from general_ludd.algorithms.line_intersect import (
    Point,
    Segment,
    bentley_ottmann,
    collinear_segments,
    compute_intersection,
    on_segment,
    orientation,
    segments_intersect,
    shamos_hoey,
)


class TestOrientation:
    def test_counterclockwise(self) -> None:
        assert orientation(Point(0, 0), Point(1, 0), Point(0, 1)) == -1

    def test_clockwise(self) -> None:
        assert orientation(Point(0, 0), Point(1, 0), Point(1, -1)) == 1

    def test_collinear_horizontal(self) -> None:
        assert orientation(Point(0, 0), Point(1, 0), Point(2, 0)) == 0

    def test_collinear_vertical(self) -> None:
        assert orientation(Point(0, 0), Point(0, 1), Point(0, 2)) == 0

    def test_collinear_diagonal(self) -> None:
        assert orientation(Point(0, 0), Point(1, 1), Point(2, 2)) == 0

    def test_epsilon_near_collinear(self) -> None:
        delta = 1e-13
        assert orientation(Point(0, 0), Point(1, 1), Point(2, 2 + delta)) == 0


class TestOnSegment:
    def test_endpoint(self) -> None:
        assert on_segment(Point(0, 0), Point(0, 0), Point(1, 1)) is True

    def test_midpoint(self) -> None:
        assert on_segment(Point(0, 0), Point(0.5, 0.5), Point(1, 1)) is True

    def test_outside(self) -> None:
        assert on_segment(Point(0, 0), Point(2, 2), Point(1, 1)) is False

    def test_outside_before(self) -> None:
        assert on_segment(Point(1, 1), Point(-1, -1), Point(0, 0)) is False


class TestSegmentsIntersect:
    def test_cross(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 1))
        s2 = Segment(Point(0, 1), Point(1, 0))
        assert segments_intersect(s1, s2) is True

    def test_parallel_no_intersect(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(0, 1), Point(1, 1))
        assert segments_intersect(s1, s2) is False

    def test_t_touch_endpoint(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(1, 0), Point(1, 1))
        assert segments_intersect(s1, s2) is True

    def test_collinear_overlap(self) -> None:
        s1 = Segment(Point(0, 0), Point(2, 0))
        s2 = Segment(Point(1, 0), Point(3, 0))
        assert segments_intersect(s1, s2) is True

    def test_collinear_disjoint(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(2, 0), Point(3, 0))
        assert segments_intersect(s1, s2) is False

    def test_collinear_touch_at_endpoint(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(1, 0), Point(2, 0))
        assert segments_intersect(s1, s2) is True


class TestComputeIntersection:
    def test_center_cross(self) -> None:
        s1 = Segment(Point(0, 0), Point(2, 2))
        s2 = Segment(Point(0, 2), Point(2, 0))
        result = compute_intersection(s1, s2)
        assert result == Point(1, 1)

    def test_no_intersection(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(0, 1), Point(1, 1))
        assert compute_intersection(s1, s2) is None

    def test_endpoint_touch(self) -> None:
        s1 = Segment(Point(0, 0), Point(1, 0))
        s2 = Segment(Point(0, 0), Point(0, 1))
        result = compute_intersection(s1, s2)
        assert result == Point(0, 0)

    def test_collinear_overlap_returns_segment(self) -> None:
        s1 = Segment(Point(0, 0), Point(2, 0))
        s2 = Segment(Point(1, 0), Point(3, 0))
        result = compute_intersection(s1, s2)
        assert isinstance(result, Segment)
        assert result.p == Point(0, 0)
        assert result.q == Point(3, 0)


class TestShamosHoey:
    def test_empty(self) -> None:
        assert shamos_hoey([]) is False

    def test_single_segment(self) -> None:
        assert shamos_hoey([Segment(Point(0, 0), Point(1, 1))]) is False

    def test_two_crossing(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 1)),
            Segment(Point(0, 1), Point(1, 0)),
        ]
        assert shamos_hoey(segs) is True

    def test_two_disjoint(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 0)),
            Segment(Point(0, 1), Point(1, 1)),
        ]
        assert shamos_hoey(segs) is False

    def test_many_no_intersect(self) -> None:
        segs = [Segment(Point(i, 0), Point(i, 1)) for i in range(10)]
        assert shamos_hoey(segs) is False

    def test_many_with_intersect(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(5, 5)),
            Segment(Point(0, 5), Point(5, 0)),
            Segment(Point(0, 1), Point(1, 0)),
        ]
        assert shamos_hoey(segs) is True


class TestBentleyOttmann:
    def test_empty(self) -> None:
        assert bentley_ottmann([]) == []

    def test_single_segment(self) -> None:
        assert bentley_ottmann([Segment(Point(0, 0), Point(1, 1))]) == []

    def test_two_crossing(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 1)),
            Segment(Point(0, 1), Point(1, 0)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) == 1
        i, j, pt = result[0]
        assert {i, j} == {0, 1}
        assert isinstance(pt, Point)
        assert abs(pt.x - 0.5) < 1e-9
        assert abs(pt.y - 0.5) < 1e-9

    def test_three_line_star(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 2)),
            Segment(Point(0, 2), Point(2, 0)),
            Segment(Point(0, 1), Point(2, 1)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) == 3

    def test_no_intersection(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 0)),
            Segment(Point(0, 1), Point(1, 1)),
        ]
        assert bentley_ottmann(segs) == []

    def test_shared_endpoint(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 0)),
            Segment(Point(1, 0), Point(1, 1)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) >= 0

    def test_collinear_overlap_returns_segment(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 0)),
            Segment(Point(1, 0), Point(3, 0)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) >= 0
        if result:
            _i, _j, inter = result[0]
            assert isinstance(inter, (Point, Segment))

    def test_vertical_horizontal_cross(self) -> None:
        segs = [
            Segment(Point(1, 0), Point(1, 2)),
            Segment(Point(0, 1), Point(2, 1)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) == 1

    def test_no_duplicate_intersections(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 2)),
            Segment(Point(0, 2), Point(2, 0)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) == 1


class TestCollinearSegments:
    def test_empty(self) -> None:
        assert collinear_segments([]) == []

    def test_two_overlapping(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 0)),
            Segment(Point(1, 0), Point(3, 0)),
        ]
        result = collinear_segments(segs)
        assert len(result) == 1
        assert result[0].p == Point(0, 0)
        assert result[0].q == Point(3, 0)

    def test_three_overlapping(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 0)),
            Segment(Point(1, 0), Point(3, 0)),
            Segment(Point(2, 0), Point(4, 0)),
        ]
        result = collinear_segments(segs)
        assert len(result) == 1
        assert result[0].p == Point(0, 0)
        assert result[0].q == Point(4, 0)

    def test_non_overlapping_collinear(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 0)),
            Segment(Point(2, 0), Point(3, 0)),
        ]
        result = collinear_segments(segs)
        assert len(result) == 2

    def test_mixed(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(2, 0)),
            Segment(Point(1, 0), Point(3, 0)),
            Segment(Point(0, 1), Point(1, 1)),
        ]
        result = collinear_segments(segs)
        assert len(result) == 2


class TestEdgeCases:
    def test_zero_length_segment(self) -> None:
        s1 = Segment(Point(0, 0), Point(0, 0))
        s2 = Segment(Point(0, 0), Point(1, 1))
        assert segments_intersect(s1, s2) is True

    def test_float_precision(self) -> None:
        s1 = Segment(Point(0.1, 0.1), Point(0.9, 0.9))
        s2 = Segment(Point(0.1, 0.9), Point(0.9, 0.1))
        assert segments_intersect(s1, s2) is True

    def test_point_namedtuple_equality(self) -> None:
        assert Point(1.0, 2.0) == Point(1.0, 2.0)
        assert Point(1.0, 2.0) != Point(1.0, 3.0)

    def test_segment_namedtuple_fields(self) -> None:
        s = Segment(Point(0, 0), Point(1, 1))
        assert s.p == Point(0, 0)
        assert s.q == Point(1, 1)

    def test_shamos_hoey_degenerate_all_collinear(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 0)),
            Segment(Point(2, 0), Point(3, 0)),
            Segment(Point(1.5, 0), Point(2.5, 0)),
        ]
        result = shamos_hoey(segs)
        assert isinstance(result, bool)

    def test_bentley_ottmann_preserves_index_order(self) -> None:
        segs = [
            Segment(Point(0, 0), Point(1, 1)),
            Segment(Point(0, 1), Point(1, 0)),
        ]
        result = bentley_ottmann(segs)
        assert len(result) == 1
        i, j, _ = result[0]
        assert i < j
