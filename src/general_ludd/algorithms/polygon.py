"""Provide polygon triangulation and geometric queries.

The pure-Python implementation includes ear clipping, monotonicity checks,
area, centroid, and point-in-polygon queries. Polygon vertices are assumed to
be counterclockwise for triangulation; clockwise inputs are reversed.
"""

from __future__ import annotations

from collections import deque

_EPS = 1e-12


def _cross_2d(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return _cross_2d(bx - ax, by - ay, cx - ax, cy - ay)


def _is_ccw(pts: list[tuple[float, float]]) -> bool:
    if len(pts) < 3:
        return True
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += (x2 - x1) * (y2 + y1)
    return s < 0.0


def _ensure_ccw(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if _is_ccw(pts):
        return pts
    return list(reversed(pts))


def _is_convex(pts: list[tuple[float, float]], i: int) -> bool:
    n = len(pts)
    a = pts[(i - 1) % n]
    b = pts[i]
    c = pts[(i + 1) % n]
    return _orient(a[0], a[1], b[0], b[1], c[0], c[1]) > _EPS


def _point_in_triangle(
    px: float,
    py: float,
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    o1 = _orient(a[0], a[1], b[0], b[1], px, py)
    o2 = _orient(b[0], b[1], c[0], c[1], px, py)
    o3 = _orient(c[0], c[1], a[0], a[1], px, py)
    if abs(o1) < _EPS or abs(o2) < _EPS or abs(o3) < _EPS:
        return False
    return (o1 > 0) == (o2 > 0) == (o3 > 0)


def _triangle_contains_vertex(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    verts: list[tuple[float, float]],
    exclude: tuple[int, int, int],
) -> bool:
    for k, v in enumerate(verts):
        if k in exclude:
            continue
        if _point_in_triangle(v[0], v[1], a, b, c):
            return True
    return False


# ---------------------------------------------------------------------------
# ear clipping triangulation  O(n2) worst case
# ---------------------------------------------------------------------------


def ear_clipping(polygon: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon via ear clipping.

    Returns list of (i, j, k) index triples into the (CCW-adjusted) vertex list.
    """
    if len(polygon) < 3:
        return []

    verts = _ensure_ccw(list(polygon))
    n = len(verts)
    if n == 3:
        return [(0, 1, 2)]

    prev = [(i - 1) % n for i in range(n)]
    nxt = [(i + 1) % n for i in range(n)]
    remaining = set(range(n))

    ears: list[tuple[int, int, int]] = []

    candidates = deque(i for i in range(n) if _is_convex(verts, i))

    while candidates and len(remaining) > 3:
        i = candidates.popleft()
        if i not in remaining:
            continue
        if not _is_convex(verts, i):
            continue

        pi, ni = prev[i], nxt[i]
        a, b, c = verts[pi], verts[i], verts[ni]

        if not _triangle_contains_vertex(a, b, c, verts, (pi, i, ni)):
            ears.append((pi, i, ni))
            remaining.discard(i)
            nxt[pi] = ni
            prev[ni] = pi

            for idx in (pi, ni):
                if idx in remaining and _is_convex(verts, idx):
                    candidates.append(idx)

    if len(remaining) == 3:
        r = sorted(remaining)
        ears.append((r[0], r[1], r[2]))

    return ears


# ---------------------------------------------------------------------------
# monotone polygon partitioning
# ---------------------------------------------------------------------------


def is_monotone(polygon: list[tuple[float, float]]) -> bool:
    """True if the CCW polygon is y-monotone."""
    verts = _ensure_ccw(list(polygon))
    n = len(verts)
    if n < 3:
        return True

    topmost = max(range(n), key=lambda i: (verts[i][1], verts[i][0]))
    botmost = min(range(n), key=lambda i: (verts[i][1], verts[i][0]))

    def _non_increasing_chain(frm: int, to: int) -> bool:
        prev_y = verts[frm][1]
        i = frm
        while i != to:
            nx = (i + 1) % n
            if verts[nx][1] > prev_y + _EPS:
                return False
            prev_y = verts[nx][1]
            i = nx
        return True

    def _non_decreasing_chain(frm: int, to: int) -> bool:
        prev_y = verts[frm][1]
        i = frm
        while i != to:
            nx = (i + 1) % n
            if verts[nx][1] < prev_y - _EPS:
                return False
            prev_y = verts[nx][1]
            i = nx
        return True

    return _non_increasing_chain(topmost, botmost) and _non_decreasing_chain(botmost, topmost)


def monotone_triangulate(polygon: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon.

    Ear clipping handles both y-monotone and non-monotone simple polygons.
    """
    return ear_clipping(polygon)


# ---------------------------------------------------------------------------
# area  shoelace formula
# ---------------------------------------------------------------------------


def area(polygon: list[tuple[float, float]]) -> float:
    """Signed area via the shoelace formula.  Positive for CCW, negative for CW."""
    a = 0.0
    n = len(polygon)
    if n < 3:
        return 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a * 0.5


# ---------------------------------------------------------------------------
# centroid  center of mass of a simple polygon
# ---------------------------------------------------------------------------


def centroid(polygon: list[tuple[float, float]]) -> tuple[float, float]:
    """Geometric centroid of a simple polygon."""
    n = len(polygon)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return polygon[0]
    if n == 2:
        return (
            (polygon[0][0] + polygon[1][0]) / 2.0,
            (polygon[0][1] + polygon[1][1]) / 2.0,
        )

    a = area(polygon)
    if abs(a) < _EPS:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return (sum(xs) / n, sum(ys) / n)

    cx = 0.0
    cy = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    denom = 1.0 / (6.0 * a)
    return (cx * denom, cy * denom)


# ---------------------------------------------------------------------------
# point-in-polygon  ray casting
# ---------------------------------------------------------------------------


def point_in_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    """Ray-casting: horizontal ray to the right. Boundary points count as inside."""
    px, py = point
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]

        if (
            min(x1, x2) - _EPS <= px <= max(x1, x2) + _EPS
            and min(y1, y2) - _EPS <= py <= max(y1, y2) + _EPS
            and abs((x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)) < _EPS
        ):
            return True

        if (y1 > py) != (y2 > py):
            x_intersect = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < x_intersect:
                inside = not inside

    return inside


# ---------------------------------------------------------------------------
# is_simple  brute-force self-intersection check
# ---------------------------------------------------------------------------


def is_simple(polygon: list[tuple[float, float]]) -> bool:
    """Brute-force check for self-intersections. O(n2)."""
    n = len(polygon)
    if n < 3:
        return True

    def _segments_intersect(
        a: tuple[float, float],
        b: tuple[float, float],
        c: tuple[float, float],
        d: tuple[float, float],
    ) -> bool:
        o1 = _orient(a[0], a[1], b[0], b[1], c[0], c[1])
        o2 = _orient(a[0], a[1], b[0], b[1], d[0], d[1])
        o3 = _orient(c[0], c[1], d[0], d[1], a[0], a[1])
        o4 = _orient(c[0], c[1], d[0], d[1], b[0], b[1])

        return (o1 > _EPS) != (o2 > _EPS) and (o3 > _EPS) != (o4 > _EPS)

    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        for j in range(i + 2, n):
            if (j + 1) % n == i:
                continue
            if _segments_intersect(a, b, polygon[j], polygon[(j + 1) % n]):
                return False
    return True


# ---------------------------------------------------------------------------
# convenience
# ---------------------------------------------------------------------------


def triangulate(polygon: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Convenience: triangulate using ear clipping."""
    return ear_clipping(polygon)
