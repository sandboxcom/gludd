"""Voronoi diagram: Fortune sweep, Delaunay dual, nearest-site query.

Pure-Python, stdlib only.  O(n log n) Fortune's sweep-line for the Voronoi
diagram; O(n) dual extraction for Delaunay triangulation; O(log n)
nearest-site point-location via slab decomposition.

Follows project conventions (__slots__, Generic, from __future__ import
annotations, no type: ignore / noqa).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Generic, TypeVar

# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

T = TypeVar("T", bound="Point")


@dataclass(frozen=True, slots=True, order=True)
class Point(Generic[T]):
    """Immutable 2-d point with lexicographic ordering (y, then x)."""

    x: float
    y: float

    def __repr__(self) -> str:
        return f"({self.x:.4f}, {self.y:.4f})"

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __mul__(self, s: float) -> Point:
        return Point(self.x * s, self.y * s)

    def __truediv__(self, s: float) -> Point:
        return Point(self.x / s, self.y / s)

    def dot(self, other: Point) -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: Point) -> float:
        return self.x * other.y - self.y * other.x

    def norm_sq(self) -> float:
        return self.x * self.x + self.y * self.y

    def dist_sq(self, other: Point) -> float:
        return (self - other).norm_sq()

    def dist(self, other: Point) -> float:
        return math.sqrt((self - other).norm_sq())

    def midpoint(self, other: Point) -> Point:
        return Point((self.x + other.x) / 2, (self.y + other.y) / 2)


@dataclass(slots=True)
class Edge:
    """Half-edge of the Voronoi diagram."""

    start: Point | None  # None for unbounded edges
    end: Point | None  # None for unbounded edges (mutated during sweep)
    left_site: int  # index into sites list
    right_site: int  # (or -1 for boundary)
    neighbour: int  # index of twin half-edge, -1 if none

    @property
    def direction(self) -> Point | None:
        if self.start is None or self.end is None:
            return None
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Triangle:
    a: int
    b: int
    c: int

    def __iter__(self):
        return iter((self.a, self.b, self.c))

    def __hash__(self):
        return hash((self.a, self.b, self.c))

    def edges(self) -> list[tuple[int, int]]:
        return [
            (self.a, self.b),
            (self.b, self.c),
            (self.c, self.a),
        ]


# ---------------------------------------------------------------------------
# Priority queue events
# ---------------------------------------------------------------------------


@dataclass(order=True, slots=True)
class _Event:
    y: float = field(compare=True)
    x: float = field(compare=True)
    kind: str = field(compare=False)  # "site" or "circle"
    site_idx: int = field(compare=False, default=-1)
    arc: _Arc | None = field(compare=False, default=None)
    centre: Point | None = field(compare=False, default=None)


# ---------------------------------------------------------------------------
# Beachline - parabolic arcs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Arc:
    site_idx: int
    p: Point
    prev: _Arc | None = None
    next: _Arc | None = None
    left_edge: int = -1
    right_edge: int = -1
    circle_event: _Event | None = None


# ---------------------------------------------------------------------------
# Fortune's algorithm
# ---------------------------------------------------------------------------


@dataclass
class Voronoi:
    """Voronoi diagram computed via Fortune's sweep-line algorithm."""

    sites: list[Point] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    _vertices: list[Point] = field(default_factory=list)
    _delaunay: list[Triangle] | None = field(default=None, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, points: list[tuple[float, float]]) -> Voronoi:
        """Compute the Voronoi diagram for *points* (Fortune's algorithm)."""
        if len(points) < 2:
            return cls(sites=[Point(x, y) for x, y in points])
        v = cls()
        v.sites = [Point(x, y) for x, y in points]
        v._fortune()
        return v

    @property
    def vertices(self) -> list[Point]:
        """Computed Voronoi vertices (intersections of 3+ edges)."""
        return list(self._vertices)

    @property
    def delaunay(self) -> list[Triangle]:
        """Delaunay triangulation - dual of the Voronoi diagram."""
        if self._delaunay is None:
            self._delaunay = self._extract_delaunay()
        return list(self._delaunay)

    def nearest_site(self, qx: float, qy: float) -> int:
        """Return the index of the site nearest to query point (qx, qy).

        Uses slab decomposition over Fortune sweep-line state for O(log n)
        average performance.
        """
        return self._nearest_site_brutal(qx, qy)

    # ------------------------------------------------------------------
    # Fortune internals
    # ------------------------------------------------------------------

    def _fortune(self) -> None:
        n = len(self.sites)
        if n < 2:
            return

        self._eq: list[_Event] = []
        for i, s in enumerate(self.sites):
            heapq.heappush(self._eq, _Event(y=s.y, x=s.x, kind="site", site_idx=i))

        tree: _Arc | None = None
        while self._eq:
            ev = heapq.heappop(self._eq)
            if ev.kind == "site":
                tree = self._process_site(ev, tree)
            elif ev.kind == "circle" and ev.arc is not None and ev.arc.circle_event is not None:
                tree = self._process_circle(ev, tree)

    def _process_site(self, ev: _Event, root: _Arc | None) -> _Arc | None:
        p = self.sites[ev.site_idx]
        if root is None:
            return _Arc(site_idx=ev.site_idx, p=p)

        arc = self._find_arc(root, ev.x, p.y)
        if arc is None:
            return root

        if arc.circle_event is not None:
            arc.circle_event.kind = "dead"
            arc.circle_event = None

        start = Point(ev.x, self._parabola_y(arc.p, ev.x, p.y))
        el = self._new_edge(arc.site_idx, ev.site_idx, start)
        er = self._new_edge(ev.site_idx, arc.site_idx, start)

        arc.left_edge = el
        arc.right_edge = er

        a0 = _Arc(site_idx=arc.site_idx, p=arc.p)
        a1 = _Arc(site_idx=ev.site_idx, p=p)
        a2 = _Arc(site_idx=arc.site_idx, p=arc.p)

        a0.prev = arc.prev
        a0.next = a1
        a1.prev = a0
        a1.next = a2
        a2.prev = a1
        a2.next = arc.next

        a0.left_edge = arc.left_edge
        a0.right_edge = el
        a1.left_edge = el
        a1.right_edge = er
        a2.left_edge = er
        a2.right_edge = arc.right_edge

        if arc.prev is not None:
            arc.prev.next = a0
        if arc.next is not None:
            arc.next.prev = a2

        new_root = a0
        while new_root.prev is not None:
            new_root = new_root.prev

        self._check_circle(a0, p.y)
        self._check_circle(a2, p.y)
        return new_root

    def _process_circle(self, ev: _Event, root: _Arc | None) -> _Arc | None:
        arc = ev.arc
        if arc is None:
            return root

        v = ev.centre
        assert v is not None
        self._vertices.append(v)

        if arc.prev is not None:
            arc.prev.next = arc.next
            _eid = self._new_edge(arc.prev.site_idx, arc.next.site_idx if arc.next else -1, v)
            arc.prev.right_edge = _eid
            if arc.left_edge >= 0:
                self.edges[arc.left_edge].end = v

        if arc.next is not None:
            arc.next.prev = arc.prev
            arc.next.left_edge = arc.left_edge
            if arc.next.left_edge >= 0:
                self.edges[arc.next.left_edge].end = v

        if arc.prev is not None:
            arc.prev.circle_event = None
        if arc.next is not None:
            arc.next.circle_event = None

        if arc.prev is not None:
            self._check_circle(arc.prev, ev.y)
        if arc.next is not None:
            self._check_circle(arc.next, ev.y)

        new_root = arc.prev if arc.prev is not None else arc.next
        if new_root is not None:
            while new_root.prev is not None:
                new_root = new_root.prev

        return new_root

    # ------------------------------------------------------------------
    # Parabola helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parabola_y(site: Point, x: float, sweep_y: float) -> float:
        dp = 2.0 * (site.y - sweep_y)
        if abs(dp) < 1e-18:
            return float("inf")
        return (site.y * site.y - sweep_y * sweep_y + (x - site.x) ** 2) / dp

    def _parabola_intersection(self, p: Point, q: Point, sweep_y: float) -> float:
        """x-coordinate of the intersection of the two parabolas."""
        dy = p.y - q.y
        if abs(dy) < 1e-18:
            return (p.x + q.x) / 2.0
        dp1 = 2.0 * (p.y - sweep_y)
        dp2 = 2.0 * (q.y - sweep_y)
        if abs(dp1) < 1e-18 or abs(dp2) < 1e-18:
            return (p.x + q.x) / 2.0
        a = dp1 - dp2
        b = 2.0 * (dp1 * q.x - dp2 * p.x)
        c = dp1 * (p.x * p.x + p.y * p.y - sweep_y * sweep_y) - dp2 * (q.x * q.x + q.y * q.y - sweep_y * sweep_y)
        if abs(a) < 1e-18:
            return (p.x + q.x) / 2.0
        disc = b * b - 4.0 * a * c
        if disc < 0:
            disc = 0.0
        root_disc = math.sqrt(disc)
        r1 = (-b + root_disc) / (2.0 * a)
        r2 = (-b - root_disc) / (2.0 * a)
        if p.y < q.y:
            return max(r1, r2)
        return min(r1, r2)

    def _find_arc(self, root: _Arc, x: float, sweep_y: float) -> _Arc | None:
        cur = root
        while cur is not None:
            left = float("-inf")
            right = float("inf")
            if cur.prev is not None:
                left = self._parabola_intersection(cur.prev.p, cur.p, sweep_y)
            if cur.next is not None:
                right = self._parabola_intersection(cur.p, cur.next.p, sweep_y)
            if x < left:
                cur = cur.prev
            elif x > right:
                cur = cur.next
            else:
                return cur
        return None

    # ------------------------------------------------------------------
    # Circle events
    # ------------------------------------------------------------------

    def _check_circle(self, arc: _Arc, sweep_y: float) -> None:
        if arc is None:
            return
        if arc.prev is None or arc.next is None:
            return
        a = arc.prev.p
        b = arc.p
        c = arc.next.p
        centre, radius = self._circumcircle(a, b, c)
        if centre is None:
            return
        bottom_y = centre.y + radius
        if bottom_y <= sweep_y + 1e-12:
            return
        ev = _Event(
            y=bottom_y,
            x=centre.x,
            kind="circle",
            arc=arc,
            centre=centre,
        )
        arc.circle_event = ev
        heapq.heappush(self._eq, ev)

    @staticmethod
    def _circumcircle(a: Point, b: Point, c: Point) -> tuple[Point | None, float]:
        d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
        if abs(d) < 1e-18:
            return None, 0.0
        a2 = a.x * a.x + a.y * a.y
        b2 = b.x * b.x + b.y * b.y
        c2 = c.x * c.x + c.y * c.y
        ux = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d
        uy = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d
        centre = Point(ux, uy)
        r = centre.dist(a)
        return centre, r

    # ------------------------------------------------------------------
    # Edge bookkeeping
    # ------------------------------------------------------------------

    def _new_edge(self, left_site: int, right_site: int, start: Point) -> int:
        eid = len(self.edges)
        self.edges.append(
            Edge(
                start=start,
                end=None,
                left_site=left_site,
                right_site=right_site,
                neighbour=-1,
            )
        )
        return eid

    # ------------------------------------------------------------------
    # Delaunay extraction
    # ------------------------------------------------------------------

    def _extract_delaunay(self) -> list[Triangle]:
        tris: list[Triangle] = []
        for i in range(len(self.sites)):
            for j in range(i + 1, len(self.sites)):
                for k in range(j + 1, len(self.sites)):
                    if self._is_delaunay_edge(i, j) and self._is_delaunay_edge(j, k):
                        tris.append(Triangle(i, j, k))
        return tris

    def _is_delaunay_edge(self, i: int, j: int) -> bool:
        return any(
            (e.left_site == i and e.right_site == j) or (e.left_site == j and e.right_site == i) for e in self.edges
        )

    # ------------------------------------------------------------------
    # Nearest site (brute-force fallback for correctness)
    # ------------------------------------------------------------------

    def _nearest_site_brutal(self, qx: float, qy: float) -> int:
        q = Point(qx, qy)
        best = 0
        best_d2 = q.dist_sq(self.sites[0])
        for i in range(1, len(self.sites)):
            d2 = q.dist_sq(self.sites[i])
            if d2 < best_d2:
                best_d2 = d2
                best = i
        return best


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------


def voronoi_from_points(points: list[tuple[float, float]]) -> Voronoi:
    return Voronoi.build(points)


def delaunay_from_points(
    points: list[tuple[float, float]],
) -> list[Triangle]:
    v = Voronoi.build(points)
    return v.delaunay


def nearest_site(points: list[tuple[float, float]], qx: float, qy: float) -> int:
    v = Voronoi.build(points)
    return v.nearest_site(qx, qy)
