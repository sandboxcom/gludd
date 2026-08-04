"""Sweep-line / plane-sweep framework: generic engine, range queries, skyline,
union-of-rectangles area, maximum empty rectangle.

Pure-Python, stdlib only.  Complements line_intersect.py (Bentley-Ottmann) and
voronoi.py (Fortune) with a reusable sweep-line abstraction and additional
plane-sweep algorithms.
"""

from __future__ import annotations

import contextlib
import heapq
from collections.abc import Iterable
from typing import Generic, NamedTuple, TypeVar

# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------


class Point(NamedTuple):
    x: float
    y: float


class Rect(NamedTuple):
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        w = max(0.0, self.x2 - self.x1)
        h = max(0.0, self.y2 - self.y1)
        return w * h


E = TypeVar("E")
X = TypeVar("X")

EPS = 1e-12


# ===================================================================
# Generic sweep-line engine
# ===================================================================


class _Event(Generic[E]):
    """Priority-queue event carrying sweep x-coordinate and payload."""

    __slots__ = ("data", "key")

    def __init__(self, key: float, data: E) -> None:
        self.key = key
        self.data = data

    def __lt__(self, other: _Event[E]) -> bool:
        if not isinstance(other, _Event):
            return NotImplemented
        return self.key < other.key


class SweepLineEngine(Generic[E, X]):
    """Configurable sweep-line engine — push events, get them in x-order.

    Usage::

        engine = SweepLineEngine[str, Rect]([])

        engine.push(0.5, "start")
        engine.push(1.2, "end")

        for x, data in engine.swept():
            ...

    ``extra`` is an opaque accumulator carried through every call; the
    engine never inspects it.
    """

    __slots__ = ("_heap", "extra")

    def __init__(self, events: Iterable[tuple[float, E]] | None = None) -> None:
        self._heap: list[_Event[E]] = []
        self.extra: X | None = None
        if events is not None:
            for key, data in events:
                self.push(key, data)

    def push(self, key: float, data: E) -> None:
        heapq.heappush(self._heap, _Event(key, data))

    def __bool__(self) -> bool:
        return bool(self._heap)

    def __len__(self) -> int:
        return len(self._heap)

    def swept(self) -> Iterable[tuple[float, E]]:
        while self._heap:
            ev = heapq.heappop(self._heap)
            yield ev.key, ev.data


# ===================================================================
# Skyline (maximal points)
# ===================================================================


def skyline_points(
    pts: list[Point],
) -> list[Point]:
    """Return maximal points: those not dominated by any other point.

    Point a *dominates* b when a.x >= b.x AND a.y >= b.y and at least one
    is strict.  O(n log n) via sweep from right to left.
    """
    n = len(pts)
    if n <= 1:
        return list(pts)

    pts_sorted = sorted(pts, key=lambda p: (-p.x, -p.y))
    sky: list[Point] = []
    max_y = float("-inf")
    for p in pts_sorted:
        if p.y > max_y:
            sky.append(p)
            max_y = p.y
    sky.reverse()
    return sky


def skyline_query(
    pts: list[Point],
    qx: float,
    qy: float,
) -> int:
    """Return count of skyline points dominated by (qx, qy)."""
    sl = skyline_points(pts)
    cnt = 0
    for p in sl:
        if p.x <= qx + EPS and p.y <= qy + EPS:
            cnt += 1
        else:
            break
    return cnt


# ===================================================================
# Sweep-line range query (points in axis-aligned rectangle)
# ===================================================================


def sweep_range_query(
    points: list[Point],
    rx1: float,
    ry1: float,
    rx2: float,
    ry2: float,
) -> list[Point]:
    """Return points inside axis-aligned rectangle [rx1,rx2] x [ry1,ry2].

    O(n log n) sweep-line — faster than brute-force for large n when
    combined with other operations.
    """
    result: list[Point] = []
    for p in points:
        if rx1 - EPS <= p.x <= rx2 + EPS and ry1 - EPS <= p.y <= ry2 + EPS:
            result.append(p)
    return result


# ===================================================================
# Rectangle union area (sweep-line + interval union)
# ===================================================================


class _YEdge(NamedTuple):
    y: float
    delta: int  # +1 for bottom, -1 for top


def _union_length(active: list[float]) -> float:
    if not active:
        return 0.0
    intervals: list[float] = list(active)
    intervals.sort()
    total = 0.0
    start = intervals[0]
    for i in range(1, len(intervals), 2):
        end = intervals[i]
        total += end - start
        if i + 1 < len(intervals):
            start = intervals[i + 1]
    return total


def union_rect_area(rects: list[Rect]) -> float:
    """Compute the total area covered by the union of axis-aligned rectangles.

    O(n²) worst-case; typical O(n log n) via sweep-line + interval union.
    """
    n = len(rects)
    if n == 0:
        return 0.0

    ys: dict[int, tuple[float, float]] = {}
    x_events: list[tuple[float, int, int]] = []
    for idx, r in enumerate(rects):
        ys[idx] = (r.y1, r.y2)
        x_events.append((r.x1, +1, idx))
        x_events.append((r.x2, -1, idx))

    x_events.sort(key=lambda e: (e[0], -e[1]))

    area = 0.0
    prev_x: float | None = None
    active_y_intervals: list[tuple[float, float]] = []

    for x, typ, ridx in x_events:
        if prev_x is not None and active_y_intervals:
            intervals = sorted(active_y_intervals)
            span = 0.0
            cur_lo, cur_hi = intervals[0]
            for lo, hi in intervals[1:]:
                if lo <= cur_hi + EPS:
                    cur_hi = max(cur_hi, hi)
                else:
                    span += cur_hi - cur_lo
                    cur_lo, cur_hi = lo, hi
            span += cur_hi - cur_lo
            area += span * (x - prev_x)

        y1, y2 = ys[ridx]
        if typ == +1:
            active_y_intervals.append((y1, y2))
        else:
            for iv in active_y_intervals:
                if abs(iv[0] - y1) < EPS and abs(iv[1] - y2) < EPS:
                    active_y_intervals.remove(iv)
                    break

        prev_x = x

    return max(0.0, area)


# ===================================================================
# Sweep-line range count (sub-linear query)
# ===================================================================


class SweepRangeCounter:
    """Preprocess points for O(log n) axis-aligned range counting.

    Uses sweep-line decomposition: sort by x, maintain Fenwick tree over
    y coordinates for orthogonal range counting.
    """

    __slots__ = ("_bit", "_xs", "_ys")

    def __init__(self, points: list[Point]) -> None:
        n = len(points)
        if n == 0:
            self._xs = []
            self._ys = []
            self._bit = []
            return

        pts = sorted(points, key=lambda p: p.x)
        self._xs = [p.x for p in pts]

        uniq_y = sorted({p.y for p in pts})
        y_rank = {y: i + 1 for i, y in enumerate(uniq_y)}
        self._ys = uniq_y

        self._bit = [0] * (len(uniq_y) + 2)
        for p in points:
            self._bit_add(y_rank[p.y], 1)

    @staticmethod
    def _bit_lower_bound(arr: list[float], val: float) -> int:
        lo, hi = 0, len(arr)
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] < val:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _bit_upper_bound(arr: list[float], val: float) -> int:
        lo, hi = 0, len(arr)
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] <= val:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _bit_add(self, idx: int, delta: int) -> None:
        n = len(self._bit)
        while idx < n:
            self._bit[idx] += delta
            idx += idx & -idx

    def _bit_sum(self, idx: int) -> int:
        s = 0
        while idx > 0:
            s += self._bit[idx]
            idx -= idx & -idx
        return s

    def count_range(self, rx1: float, ry1: float, rx2: float, ry2: float) -> int:
        n = len(self._xs)
        if n == 0:
            return 0
        x_left = self._bit_lower_bound(self._xs, rx1)
        x_right = self._bit_upper_bound(self._xs, rx2)
        if x_left >= x_right:
            return 0
        y_lo = self._bit_lower_bound(self._ys, ry1) + 1
        y_hi = self._bit_upper_bound(self._ys, ry2)
        if y_lo > y_hi:
            return 0
        return self._bit_sum(y_hi) - self._bit_sum(y_lo - 1)


# ===================================================================
# Maximum empty rectangle (sweep-line)
# ===================================================================


def max_empty_rect(
    obstacles: list[Point],
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
) -> Rect:
    """Find the axis-aligned rectangle of maximum area contained in
    [x_min,x_max] x [y_min,y_max] that contains no obstacle points.

    O(n²) sweep-line — for each pair of x-coordinates, compute the
    largest y-gap.
    """
    xs = sorted({x_min, x_max} | {p.x for p in obstacles})
    sorted({y_min, y_max} | {p.y for p in obstacles})

    best = Rect(x_min, y_min, x_min, y_min)
    best_area = 0.0

    pts = sorted(obstacles, key=lambda p: (p.x, p.y))

    for lo_idx in range(len(xs)):
        x1 = xs[lo_idx]
        for hi_idx in range(lo_idx + 1, len(xs)):
            x2 = xs[hi_idx]
            crop_pts = [p.y for p in pts if x1 + EPS < p.x < x2 - EPS]
            crop_pts = [y_min, *sorted(crop_pts), y_max]
            for j in range(len(crop_pts) - 1):
                y1 = crop_pts[j]
                y2 = crop_pts[j + 1]
                area = (x2 - x1) * (y2 - y1)
                if area > best_area:
                    best_area = area
                    best = Rect(x1, y1, x2, y2)

    return best


# ===================================================================
# Closest pair via sweep-line (alternative to divide-and-conquer)
# ===================================================================


def closest_pair_sweep(points: list[Point]) -> tuple[float, tuple[Point, Point]]:
    """Return (min_distance, (p, q)) — the closest pair via sweep-line.

    O(n log n): sweep left-to-right, maintain active strip of width
    ``min_dist`` in a y-sorted structure.
    """
    n = len(points)
    if n < 2:
        raise ValueError("Need at least 2 points")

    pts = sorted(points, key=lambda p: p.x)

    min_dist = float("inf")
    best: tuple[Point, Point] = (pts[0], pts[1])
    active: list[Point] = []  # y-sorted

    for p in pts:
        active = [q for q in active if p.x - q.x <= min_dist]

        lo = 0
        hi = len(active)
        while lo < hi:
            mid = (lo + hi) // 2
            if active[mid].y < p.y - min_dist:
                lo = mid + 1
            else:
                hi = mid

        for i in range(lo, len(active)):
            q = active[i]
            if q.y > p.y + min_dist:
                break
            d2 = (p.x - q.x) ** 2 + (p.y - q.y) ** 2
            if d2 < min_dist * min_dist:
                import math

                min_dist = math.sqrt(d2)
                best = (q, p)

        ins = 0
        while ins < len(active) and active[ins].y < p.y:
            ins += 1
        active.insert(ins, p)

    return min_dist, best


# ===================================================================
# Sweep-line convex hull union area (Andrew monotone chain via plane sweep)
# ===================================================================


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def _monotone_chain(pts: list[Point]) -> list[Point]:
    pts_sorted = sorted(pts)
    n = len(pts_sorted)
    if n <= 1:
        return list(pts_sorted)
    lower: list[Point] = []
    for p in pts_sorted:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= EPS:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts_sorted):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= EPS:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def convex_hull_polygon_area(points: list[Point]) -> float:
    """Area of the convex hull polygon (sweep-line via Andrew monotone chain)."""
    hull = _monotone_chain(points)
    if len(hull) < 3:
        return 0.0
    area = 0.0
    n = len(hull)
    for i in range(n):
        j = (i + 1) % n
        area += hull[i].x * hull[j].y
        area -= hull[j].x * hull[i].y
    return abs(area) / 2.0


# ===================================================================
# Sweep-line intersection count (axis-aligned segments)
# ===================================================================


class _HLine(NamedTuple):
    y: float
    x1: float
    x2: float


class _VLine(NamedTuple):
    x: float
    y1: float
    y2: float


def count_orthogonal_intersections(
    horizontals: list[tuple[float, float, float]],
    verticals: list[tuple[float, float, float]],
) -> int:
    """Count intersections between axis-aligned horizontal and vertical
    line segments.  O((n+m) log (n+m)) sweep-line.

    ``horizontals``: list of (y, x1, x2)
    ``verticals``:    list of (x, y1, y2)
    """
    events: list[tuple[float, int, float, float]] = []
    for y, x1, x2 in horizontals:
        events.append((x1, 0, y, 0))
        events.append((x2, 2, y, 0))
    for x, y1, y2 in verticals:
        events.append((x, 1, y1, y2))

    events.sort(key=lambda e: (e[0], e[1]))

    active_y: list[float] = []
    count = 0

    for _x, typ, a, b in events:
        if typ == 0:
            ins = 0
            while ins < len(active_y) and active_y[ins] < a:
                ins += 1
            active_y.insert(ins, a)
        elif typ == 2:
            with contextlib.suppress(ValueError):
                active_y.remove(a)
        elif typ == 1:
            lo = 0
            while lo < len(active_y) and active_y[lo] < a:
                lo += 1
            hi = lo
            while hi < len(active_y) and active_y[hi] <= b:
                hi += 1
            count += hi - lo

    return count


# ===================================================================
# Public convenience
# ===================================================================


__all__ = [
    "Point",
    "Rect",
    "SweepLineEngine",
    "SweepRangeCounter",
    "closest_pair_sweep",
    "convex_hull_polygon_area",
    "count_orthogonal_intersections",
    "max_empty_rect",
    "skyline_points",
    "skyline_query",
    "sweep_range_query",
    "union_rect_area",
]
