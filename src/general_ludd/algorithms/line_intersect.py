"""Provide line-segment intersection algorithms.

The module implements orientation, collinear ordering, Shamos-Hoey boolean
checks, and Bentley-Ottmann sweep-line intersection reporting.
"""

from __future__ import annotations

import bisect
from typing import NamedTuple


class Point(NamedTuple):
    """Represent a two-dimensional point."""

    x: float
    y: float


class Segment(NamedTuple):
    """Represent a closed line segment between two points."""

    p: Point
    q: Point


EPS = 1e-12


def orientation(a: Point, b: Point, c: Point) -> int:
    """Return zero for collinear, one for clockwise, and minus one otherwise."""
    val = (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y)
    if abs(val) < EPS:
        return 0
    return 1 if val > 0 else -1


def on_segment(a: Point, b: Point, c: Point) -> bool:
    """Return whether point ``b`` lies inside the bounding box from ``a`` to ``c``."""
    return min(a.x, c.x) - EPS <= b.x <= max(a.x, c.x) + EPS and min(a.y, c.y) - EPS <= b.y <= max(a.y, c.y) + EPS


def segments_intersect(s1: Segment, s2: Segment) -> bool:
    """Return whether two closed line segments intersect."""
    p1, q1 = s1.p, s1.q
    p2, q2 = s2.p, s2.q
    o1 = orientation(p1, q1, p2)
    o2 = orientation(p1, q1, q2)
    o3 = orientation(p2, q2, p1)
    o4 = orientation(p2, q2, q1)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(p1, p2, q1):
        return True
    if o2 == 0 and on_segment(p1, q2, q1):
        return True
    if o3 == 0 and on_segment(p2, p1, q2):
        return True
    return bool(o4 == 0 and on_segment(p2, q1, q2))


def shamos_hoey(segments: list[Segment]) -> bool:
    """Return whether any segment pair intersects using a sweep-line check."""
    events: list[tuple[float, int, int, Segment]] = []
    for i, seg in enumerate(segments):
        x1, x2 = seg.p.x, seg.q.x
        if x1 > x2:
            x1, x2 = x2, x1
        events.append((x1, 0, i, seg))
        events.append((x2, 1, i, seg))
    events.sort(key=lambda e: (e[0], e[1]))
    active: list[Segment] = []
    for x, typ, _idx, seg in events:
        if typ == 0:
            y = seg.p.y if seg.p.x == x else seg.q.y
            pos = bisect.bisect_left([_sweep_y(s, x) for s in active], y)
            active.insert(pos, seg)
            if pos > 0 and segments_intersect(active[pos - 1], seg):
                return True
            if pos + 1 < len(active) and segments_intersect(seg, active[pos + 1]):
                return True
        else:
            y = seg.p.y if seg.p.x == x else seg.q.y
            try:
                pos = active.index(seg)
            except ValueError:
                pos = bisect.bisect_left([_sweep_y(s, x) for s in active], y) - 1
                if pos < 0:
                    pos = 0
            if pos > 0 and pos + 1 < len(active) and segments_intersect(active[pos - 1], active[pos + 1]):
                return True
            if seg in active:
                active.remove(seg)
    return False


def _sweep_y(seg: Segment, x: float) -> float:
    if abs(seg.q.x - seg.p.x) < EPS:
        return min(seg.p.y, seg.q.y)
    t = (x - seg.p.x) / (seg.q.x - seg.p.x)
    return seg.p.y + t * (seg.q.y - seg.p.y)


def compute_intersection(s1: Segment, s2: Segment) -> Point | Segment | None:
    """Return the point or overlap segment shared by two segments, if any."""
    p1, q1 = s1.p, s1.q
    p2, q2 = s2.p, s2.q
    x1, y1 = p1.x, p1.y
    x2, y2 = q1.x, q1.y
    x3, y3 = p2.x, p2.y
    x4, y4 = q2.x, q2.y
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < EPS:
        o1 = orientation(p1, q1, p2)
        o2 = orientation(p1, q1, q2)
        o3 = orientation(p2, q2, p1)
        o4 = orientation(p2, q2, q1)
        if o1 == 0 and o2 == 0 and o3 == 0 and o4 == 0:
            pts = sorted([p1, q1, p2, q2])
            if on_segment(p1, p2, q1) or on_segment(p1, q2, q1):
                return Segment(pts[0], pts[-1])
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    if -EPS <= t <= 1 + EPS and -EPS <= u <= 1 + EPS:
        return Point(x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def bentley_ottmann(
    segments: list[Segment],
) -> list[tuple[int, int, Point | Segment]]:
    """Return indexed intersections discovered by a Bentley-Ottmann sweep."""
    n = len(segments)
    if n < 2:
        return []
    events: list[tuple[float, int, int, Segment]] = []
    for i, seg in enumerate(segments):
        x1, x2 = seg.p.x, seg.q.x
        if x1 > x2:
            x1, x2 = x2, x1
        events.append((x1, 0, i, seg))
        events.append((x2, 1, i, seg))
    events.sort(key=lambda e: (e[0], e[1]))
    active: list[Segment] = []
    result: list[tuple[int, int, Point | Segment]] = []
    seen: set[tuple[int, int]] = set()
    for x, typ, idx, seg in events:
        if typ == 0:
            y_start = _sweep_y(seg, x)
            pos = bisect.bisect_left([_sweep_y(s, x) for s in active], y_start)
            active.insert(pos, seg)
            for nb in (pos - 1, pos + 1):
                if 0 <= nb < len(active) and active[nb] is not seg:
                    other_seg = active[nb]
                    other_idx = -1
                    for oi, s in enumerate(segments):
                        if s is other_seg:
                            other_idx = oi
                            break
                    if other_idx >= 0:
                        pair = (min(idx, other_idx), max(idx, other_idx))
                        if pair not in seen and segments_intersect(seg, other_seg):
                            inter = compute_intersection(seg, other_seg)
                            if inter is not None:
                                seen.add(pair)
                                result.append((pair[0], pair[1], inter))
        else:
            try:
                pos = active.index(seg)
            except ValueError:
                pos = -1
            if pos >= 0:
                active.pop(pos)
                if 0 <= pos < len(active):
                    above = active[pos]
                    below_idx = pos - 1
                    if below_idx >= 0:
                        below = active[below_idx]
                        ab_idx = -1
                        bl_idx_ = -1
                        for oi, s in enumerate(segments):
                            if s is above:
                                ab_idx = oi
                            if s is below:
                                bl_idx_ = oi
                        if ab_idx >= 0 and bl_idx_ >= 0:
                            pair = (
                                min(ab_idx, bl_idx_),
                                max(ab_idx, bl_idx_),
                            )
                            if pair not in seen and segments_intersect(above, below):
                                inter = compute_intersection(above, below)
                                if inter is not None:
                                    seen.add(pair)
                                    result.append((pair[0], pair[1], inter))
    return result


def collinear_segments(segments: list[Segment]) -> list[Segment]:
    """Merge overlapping collinear segments into maximal spans."""
    result: list[Segment] = []
    used: set[int] = set()
    for i, s in enumerate(segments):
        if i in used:
            continue
        col_pts: list[Point] = [s.p, s.q]
        col_idxs: set[int] = {i}
        for j, other in enumerate(segments):
            if j == i or j in used:
                continue
            if (
                orientation(s.p, s.q, other.p) == 0
                and orientation(s.p, s.q, other.q) == 0
                and (
                    on_segment(s.p, other.p, s.q) or on_segment(s.p, other.q, s.q) or on_segment(other.p, s.p, other.q)
                )
            ):
                col_pts.extend([other.p, other.q])
                col_idxs.add(j)
        col_pts.sort()
        if len(col_pts) >= 2:
            result.append(Segment(col_pts[0], col_pts[-1]))
        used.update(col_idxs)
    return result
