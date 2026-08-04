"""Convex hull algorithms: Graham scan, Jarvis march (gift wrapping),
QuickHull, and Chan's algorithm.

Pure-Python, stdlib only. All functions accept a list of (x, y) points
and return the convex hull vertices in counterclockwise order.
"""

from __future__ import annotations

from functools import cmp_to_key
from typing import NamedTuple


class Point(NamedTuple):
    x: float
    y: float


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def _dist_sq(a: Point, b: Point) -> float:
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2


def _is_ccw(hull: list[Point]) -> bool:
    if len(hull) < 3:
        return True
    s = 0.0
    n = len(hull)
    for i in range(n):
        a, b = hull[i], hull[(i + 1) % n]
        s += (b.x - a.x) * (b.y + a.y)
    return s < 0


def _ensure_ccw(hull: list[Point]) -> list[Point]:
    if not _is_ccw(hull) and len(hull) >= 3:
        hull = list(reversed(hull))
    return hull


# ═══════════════════════════════════════════════════════════════════════
# Graham scan
# ═══════════════════════════════════════════════════════════════════════


def graham_scan(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Graham scan O(n log n). Returns convex hull vertices in CCW order."""
    if len(pts) <= 2:
        return pts[:]
    points = [Point(x, y) for x, y in pts]
    points.sort(key=lambda p: (p.y, p.x))
    p0 = points[0]
    rest = points[1:]
    rest.sort(key=cmp_to_key(_PolarCmp(p0)))
    hull = [p0, rest[0]]
    for p in rest[1:]:
        while len(hull) >= 2 and _cross(hull[-2], hull[-1], p) <= 0:
            hull.pop()
        hull.append(p)
    return [(p.x, p.y) for p in hull]


class _PolarCmp:
    __slots__ = ("p0",)

    def __init__(self, p0: Point) -> None:
        self.p0 = p0

    def __call__(self, a: Point, b: Point) -> int:
        return _polar_cmp(self.p0, a, b)


def _polar_cmp(p0: Point, a: Point, b: Point) -> int:
    c = _cross(p0, a, b)
    if c > 0:
        return -1
    if c < 0:
        return 1
    return -1 if _dist_sq(p0, a) < _dist_sq(p0, b) else 1


# ═══════════════════════════════════════════════════════════════════════
# Jarvis march (gift wrapping)
# ═══════════════════════════════════════════════════════════════════════


def jarvis_march(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Jarvis march O(n h) where h is hull size. Returns CCW hull."""
    if len(pts) <= 2:
        return pts[:]
    n = len(pts)
    points = [Point(x, y) for x, y in pts]
    leftmost = min(range(n), key=lambda i: (points[i].x, points[i].y))
    hull: list[int] = []
    p = leftmost
    while True:
        hull.append(p)
        q = (p + 1) % n
        for r in range(n):
            cr = _cross(points[p], points[r], points[q])
            if cr > 0 or (cr == 0 and _dist_sq(points[p], points[r]) > _dist_sq(points[p], points[q])):
                q = r
        p = q
        if p == hull[0]:
            break
    return [(points[i].x, points[i].y) for i in hull]


# ═══════════════════════════════════════════════════════════════════════
# QuickHull
# ═══════════════════════════════════════════════════════════════════════


def quickhull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """QuickHull O(n log n) average, O(n²) worst case. Returns CCW hull."""
    if len(pts) <= 2:
        return pts[:]
    points = [Point(x, y) for x, y in pts]
    min_x = min(points, key=lambda p: (p.x, p.y))
    max_x = max(points, key=lambda p: (p.x, p.y))
    above: list[Point] = []
    below: list[Point] = []
    for p in points:
        c = _cross(min_x, max_x, p)
        if c > 0:
            above.append(p)
        elif c < 0:
            below.append(p)

    upper: list[Point] = []
    _quickhull_rec(min_x, max_x, above, upper)
    lower: list[Point] = []
    _quickhull_rec(max_x, min_x, below, lower)

    hull = _ensure_ccw([min_x, *upper, max_x, *lower])
    return [(p.x, p.y) for p in hull]


def _quickhull_rec(a: Point, b: Point, pts: list[Point], out: list[Point]) -> None:
    if not pts:
        return
    farthest = max(pts, key=lambda p: abs(_cross(a, b, p)))
    left_of_far: list[Point] = []
    right_of_far: list[Point] = []
    for p in pts:
        if p is farthest:
            continue
        if _cross(a, farthest, p) > 0:
            left_of_far.append(p)
        elif _cross(farthest, b, p) > 0:
            right_of_far.append(p)
    _quickhull_rec(a, farthest, left_of_far, out)
    out.append(farthest)
    _quickhull_rec(farthest, b, right_of_far, out)


# ═══════════════════════════════════════════════════════════════════════
# Chan's algorithm
# ═══════════════════════════════════════════════════════════════════════


def chans_algorithm(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Chan's algorithm O(n log h) output-sensitive. Returns CCW hull."""
    if len(pts) <= 2:
        return pts[:]
    n = len(pts)
    points = [Point(x, y) for x, y in pts]
    t = 4
    while t <= n * 2:
        m = max(2, min(n, t))
        raw = _chan_try(points, m)
        if raw is not None and len(raw) <= t:
            hull = [Point(x, y) for x, y in raw]
            hull = _ensure_ccw(hull)
            return [(p.x, p.y) for p in hull]
        next_t = min(n, t * 2)
        if next_t == t:
            break
        t = next_t
    return graham_scan(pts)


def _chan_try(points: list[Point], m: int) -> list[tuple[float, float]] | None:
    n = len(points)
    sub_hulls: list[list[Point]] = []
    for i in range(0, n, m):
        chunk = points[i : i + m]
        chunk_pts = [(p.x, p.y) for p in chunk]
        hull_pts = graham_scan(chunk_pts)
        sub_hulls.append([Point(x, y) for x, y in hull_pts])

    p0 = min(sub_hulls, key=lambda h: h[0])[0]
    result: list[Point] = [p0]

    for _ in range(m):
        best: Point | None = None
        for h in sub_hulls:
            q = _find_tangent_linear(result[-1], h)
            if q is None or q == result[-1]:
                continue
            if best is None:
                best = q
            else:
                cr = _cross(result[-1], q, best)
                if cr > 0 or (cr == 0 and _dist_sq(result[-1], q) > _dist_sq(result[-1], best)):
                    best = q
        if best is None:
            break
        if best == p0:
            return [(p.x, p.y) for p in result]
        result.append(best)
    return None


def _find_tangent_linear(cur: Point, hull: list[Point]) -> Point | None:
    if not hull:
        return None
    if len(hull) == 1:
        return hull[0]
    best = hull[0]
    for q in hull[1:]:
        if q == cur:
            continue
        cr = _cross(cur, best, q)
        if cr > 0 or (cr == 0 and _dist_sq(cur, q) > _dist_sq(cur, best)):
            best = q
    return best if best != cur else None
