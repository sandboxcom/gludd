"""Closest-pair algorithms: brute force, divide-and-conquer, line sweep.

Returns (min_distance, (p1, p2)) for a list of points.
Pure-Python, stdlib only.
"""

from __future__ import annotations

import math


def _dist(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def closest_pair_brute(
    points: list[tuple[float, float]],
) -> tuple[float, tuple[int, int] | None]:
    """Brute-force O(n^2).  Returns (min_dist, (idx_a, idx_b)) or (inf, None)."""
    n = len(points)
    if n < 2:
        return math.inf, None
    best = math.inf
    best_pair: tuple[int, int] | None = None
    for i in range(n):
        xi, yi = points[i]
        for j in range(i + 1, n):
            d = math.hypot(xi - points[j][0], yi - points[j][1])
            if d < best:
                best = d
                best_pair = (i, j)
    return best, best_pair


def closest_pair_dc(
    points: list[tuple[float, float]],
) -> tuple[float, tuple[int, int] | None]:
    """Divide-and-conquer O(n log n) closest pair.

    Sorts by x, recursively halves, then checks a centre strip of width 2*delta.
    Returns (min_dist, (idx_a, idx_b)) or (inf, None).
    """
    n = len(points)
    if n < 2:
        return math.inf, None

    indexed = sorted(enumerate(points), key=lambda t: t[1][0])
    idx_map = [t[0] for t in indexed]
    pts_sorted = [t[1] for t in indexed]

    def _recurse(lo: int, hi: int) -> tuple[float, tuple[int, int] | None]:
        if hi - lo <= 3:
            best = math.inf
            best_pair: tuple[int, int] | None = None
            for i in range(lo, hi):
                xi, yi = pts_sorted[i]
                for j in range(i + 1, hi):
                    d = math.hypot(xi - pts_sorted[j][0], yi - pts_sorted[j][1])
                    if d < best:
                        best = d
                        best_pair = (idx_map[i], idx_map[j])
            return best, best_pair

        mid = (lo + hi) // 2
        mid_x = pts_sorted[mid][0]

        d_left, pair_left = _recurse(lo, mid)
        d_right, pair_right = _recurse(mid, hi)

        delta = d_left
        best_pair = pair_left
        if d_right < delta:
            delta = d_right
            best_pair = pair_right

        strip: list[int] = []
        for i in range(lo, hi):
            if abs(pts_sorted[i][0] - mid_x) < delta:
                strip.append(i)

        strip.sort(key=lambda i: pts_sorted[i][1])
        m = len(strip)
        for i in range(m):
            for j in range(i + 1, min(i + 8, m)):
                a, b = strip[i], strip[j]
                d = math.hypot(
                    pts_sorted[a][0] - pts_sorted[b][0],
                    pts_sorted[a][1] - pts_sorted[b][1],
                )
                if d < delta:
                    delta = d
                    best_pair = (idx_map[a], idx_map[b])

        return delta, best_pair

    return _recurse(0, n)


def closest_pair_sweep(
    points: list[tuple[float, float]],
) -> tuple[float, tuple[int, int] | None]:
    """Line-sweep O(n log n) closest pair.

    Sweeps by x, maintains an active set ordered by y within a window of
    width delta. Returns (min_dist, (idx_a, idx_b)) or (inf, None).
    """
    n = len(points)
    if n < 2:
        return math.inf, None

    indexed = sorted(enumerate(points), key=lambda t: t[1][0])

    delta = math.inf
    best_pair: tuple[int, int] | None = None

    active: list[tuple[float, float, int]] = []

    def _bisect_y(seq: list[tuple[float, float, int]], y: float) -> int:
        lo, hi = 0, len(seq)
        while lo < hi:
            mid = (lo + hi) // 2
            if seq[mid][1] < y:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _insort_y(seq: list[tuple[float, float, int]], item: tuple[float, float, int]) -> None:
        idx = _bisect_y(seq, item[1])
        seq.insert(idx, item)

    left = 0
    for idx_p, (px, py) in indexed:
        while left < len(active) and px - active[left][0] > delta:
            left += 1
        if left > 0:
            del active[:left]
            left = 0

        lo_y = py - delta
        start = _bisect_y(active, lo_y)
        for i in range(start, len(active)):
            qx, qy, qi = active[i]
            if qy - py > delta:
                break
            d = math.hypot(px - qx, py - qy)
            if d < delta:
                delta = d
                best_pair = (qi, idx_p)

        _insort_y(active, (px, py, idx_p))

    return delta, best_pair
