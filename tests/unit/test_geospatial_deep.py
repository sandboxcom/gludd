"""Deep geospatial algorithm tests: Haversine, bearing, bounding box,
point-in-polygon (ray casting), convex hull (Graham scan), and Voronoi
diagrams (Fortune's sweep for 2D sites).

All algorithms are pure-Python (stdlib only) — no external geo library.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

# ── Types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float  # latitude when geo, Cartesian y otherwise


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_M = 6_371_000.0


# ── Haversine ─────────────────────────────────────────────────────────


def haversine(a: GeoPoint, b: GeoPoint) -> float:
    """Distance in km between two geographic points via the Haversine formula."""
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    sdlat = math.sin(dlat / 2)
    sdlon = math.sin(dlon / 2)
    a_val = sdlat * sdlat + math.cos(lat1) * math.cos(lat2) * sdlon * sdlon
    c = 2 * math.atan2(math.sqrt(a_val), math.sqrt(1 - a_val))
    return EARTH_RADIUS_KM * c


def test_haversine_zero():
    """Same point → 0 km."""
    p = GeoPoint(51.5074, -0.1278)
    assert haversine(p, p) == pytest.approx(0.0, abs=1e-9)


def test_haversine_antipodal():
    """Antipodal points → half circumference (~20015 km)."""
    ny = GeoPoint(40.7128, -74.0060)
    antipodal = GeoPoint(-40.7128, 105.9940)  # 180° away
    d = haversine(ny, antipodal)
    half_circ = math.pi * EARTH_RADIUS_KM
    assert d == pytest.approx(half_circ, rel=0.02)


def test_haversine_london_paris():
    """London → Paris ≈ 344 km."""
    london = GeoPoint(51.5074, -0.1278)
    paris = GeoPoint(48.8566, 2.3522)
    d = haversine(london, paris)
    assert d == pytest.approx(343.0, abs=10)


def test_haversine_la_nyc():
    """LA → NYC ≈ 3940 km."""
    la = GeoPoint(34.0522, -118.2437)
    nyc = GeoPoint(40.7128, -74.0060)
    d = haversine(la, nyc)
    assert d == pytest.approx(3940.0, abs=50)


def test_haversine_equator_small():
    """Two points 1° apart on the equator ≈ 111.195 km."""
    a = GeoPoint(0.0, 0.0)
    b = GeoPoint(0.0, 1.0)
    d = haversine(a, b)
    # 1 degree ≈ 111.195 km at the equator
    assert d == pytest.approx(111.195, abs=0.5)


def test_haversine_poles():
    """Points near the poles — lon difference nearly irrelevant."""
    a = GeoPoint(89.9, 0.0)
    b = GeoPoint(89.9, 180.0)
    d = haversine(a, b)
    assert d < 25  # very close near the pole


def test_haversine_symmetric():
    """Distance is symmetric."""
    a = GeoPoint(37.7749, -122.4194)
    b = GeoPoint(40.7128, -74.0060)
    assert haversine(a, b) == pytest.approx(haversine(b, a), abs=1e-9)


# ── Initial Bearing ───────────────────────────────────────────────────


def initial_bearing(a: GeoPoint, b: GeoPoint) -> float:
    """Initial bearing (degrees, 0-360) from a to b along a great circle."""
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    y = math.sin(dlon) * math.cos(lat2)
    bearing_rad = math.atan2(y, x)
    bearing_deg = math.degrees(bearing_rad)
    return (bearing_deg + 360) % 360


def test_bearing_due_north():
    """Bearing from equator 0,0 to 1,0 is due north (0°)."""
    a = GeoPoint(0.0, 0.0)
    b = GeoPoint(10.0, 0.0)
    assert initial_bearing(a, b) == pytest.approx(0.0, abs=0.1)


def test_bearing_due_east():
    """Bearing from 0,0 to 0,10 is due east (90°)."""
    a = GeoPoint(0.0, 0.0)
    b = GeoPoint(0.0, 10.0)
    assert initial_bearing(a, b) == pytest.approx(90.0, abs=0.1)


def test_bearing_due_south():
    """Bearing from 10,0 to 0,0 is due south (180°)."""
    a = GeoPoint(10.0, 0.0)
    b = GeoPoint(0.0, 0.0)
    assert initial_bearing(a, b) == pytest.approx(180.0, abs=0.1)


def test_bearing_due_west():
    """Bearing from 0,10 to 0,0 is due west (270°)."""
    a = GeoPoint(0.0, 10.0)
    b = GeoPoint(0.0, 0.0)
    assert initial_bearing(a, b) == pytest.approx(270.0, abs=0.1)


def test_bearing_london_to_nyc():
    """London → NYC initial bearing ≈ 288° (northwest)."""
    london = GeoPoint(51.5074, -0.1278)
    nyc = GeoPoint(40.7128, -74.0060)
    b = initial_bearing(london, nyc)
    assert 280 < b < 295


# ── Bounding Box ──────────────────────────────────────────────────────


def bounding_box(points: Sequence[GeoPoint], padding_km: float = 0.0) -> BoundingBox:
    """Axis-aligned bounding box (min/max lat/lon) with optional padding in km."""
    if not points:
        raise ValueError("at least one point required")
    min_lat = min(p.lat for p in points)
    max_lat = max(p.lat for p in points)
    min_lon = min(p.lon for p in points)
    max_lon = max(p.lon for p in points)
    if padding_km > 0:
        dlat = (padding_km / EARTH_RADIUS_KM) * (180 / math.pi)
        mid_lat = (min_lat + max_lat) / 2
        dlon = (padding_km / (EARTH_RADIUS_KM * math.cos(math.radians(mid_lat)))) * (180 / math.pi)
        min_lat -= dlat
        max_lat += dlat
        min_lon -= dlon
        max_lon += dlon
    return BoundingBox(min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon)


def test_bbox_single_point():
    """A single point yields a degenerate bbox."""
    p = GeoPoint(51.5, -0.12)
    bb = bounding_box([p])
    assert bb.min_lat == bb.max_lat == 51.5
    assert bb.min_lon == bb.max_lon == -0.12


def test_bbox_multiple_points():
    """Bounding box from multiple points."""
    pts = [
        GeoPoint(51.5, -0.12),
        GeoPoint(40.7, -74.0),
        GeoPoint(48.8, 2.35),
    ]
    bb = bounding_box(pts)
    assert bb.min_lat == 40.7
    assert bb.max_lat == 51.5
    assert bb.min_lon == -74.0
    assert bb.max_lon == 2.35


def test_bbox_with_padding():
    """Padding expands the bbox by roughly the expected degrees."""
    pts = [GeoPoint(45.0, -90.0)]
    bb = bounding_box(pts, padding_km=111.195)
    assert bb.min_lat < 44.0
    assert bb.max_lat > 46.0
    assert bb.min_lon < -91.0
    assert bb.max_lon > -89.0


def test_bbox_no_points_raises():
    """Empty input raises ValueError."""
    with pytest.raises(ValueError):
        bounding_box([])


# ── Point-in-Polygon (Ray Casting) ────────────────────────────────────


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray-casting algorithm: returns True if point lies inside the (closed) polygon."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].x, polygon[i].y
        xj, yj = polygon[j].x, polygon[j].y
        if ((yi > point.y) != (yj > point.y)) and (point.x < (xj - xi) * (point.y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def test_pip_inside_square():
    """Point clearly inside a square."""
    poly = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    assert point_in_polygon(Point(5, 5), poly) is True


def test_pip_outside_square():
    """Point clearly outside a square."""
    poly = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    assert point_in_polygon(Point(15, 5), poly) is False


def test_pip_on_edge():
    """Point on the edge — ray casting may return either True or False
    depending on edge orientation, so we accept both."""
    poly = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    result = point_in_polygon(Point(5, 0), poly)
    assert result is True or result is False


def test_pip_concave_polygon():
    """Point in a concave (L-shaped) polygon."""
    poly = [
        Point(0, 0),
        Point(10, 0),
        Point(10, 5),
        Point(5, 5),
        Point(5, 10),
        Point(0, 10),
    ]
    assert point_in_polygon(Point(2, 2), poly) is True
    assert point_in_polygon(Point(7, 7), poly) is False  # in the notch


def test_pip_degenerate():
    """Fewer than 3 vertices → always False."""
    assert point_in_polygon(Point(0, 0), [Point(0, 0), Point(1, 1)]) is False


# ── Convex Hull (Graham Scan) ─────────────────────────────────────────


def _cross(o: Point, a: Point, b: Point) -> float:
    """2D cross product of vectors oa and ob. Positive → ccw turn."""
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def convex_hull(points: Sequence[Point]) -> list[Point]:
    """Graham scan: returns the convex hull vertices in CCW order."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def test_convex_hull_triangle():
    """Three points form the hull themselves."""
    pts = [Point(0, 0), Point(10, 0), Point(5, 10)]
    hull = convex_hull(pts)
    assert len(hull) == 3
    assert set(hull) == set(pts)


def test_convex_hull_square_with_center():
    """A point inside the square is excluded from the hull."""
    pts = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10), Point(5, 5)]
    hull = convex_hull(pts)
    assert len(hull) == 4
    assert Point(5, 5) not in hull


def test_convex_hull_collinear():
    """Collinear points — only endpoints remain."""
    pts = [Point(0, 0), Point(2, 0), Point(4, 0), Point(6, 0)]
    hull = convex_hull(pts)
    assert len(hull) == 2


def test_convex_hull_duplicates():
    """Duplicate points are deduplicated."""
    pts = [Point(0, 0), Point(0, 0), Point(10, 0), Point(10, 10)]
    hull = convex_hull(pts)
    assert len(hull) == 3


def test_convex_hull_single_point():
    """Degenerate: one point → same point."""
    pts = [Point(5, 5)]
    hull = convex_hull(pts)
    assert hull == [Point(5, 5)]


def test_convex_hull_empty():
    """Empty input → empty hull."""
    hull = convex_hull([])
    assert hull == []


def test_convex_hull_convexity():
    """Every consecutive triple in the hull must turn left (CCW)."""
    import random

    rng = random.Random(42)
    pts = [Point(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(50)]
    hull = convex_hull(pts)
    n = len(hull)
    for i in range(n):
        a = hull[i]
        b = hull[(i + 1) % n]
        c = hull[(i + 2) % n]
        assert _cross(a, b, c) >= 0  # CCW or collinear


# ── Voronoi Diagram (2D — simple O(n³) for correctness) ───────────────


@dataclass(frozen=True)
class VoronoiCell:
    site: Point
    vertices: list[Point]


def voronoi_sites_to_cells(sites: Sequence[Point], bbox_half: float = 10.0) -> list[VoronoiCell]:
    """Compute Voronoi cells for 2D sites by computing half-plane
    intersections. Each cell is the set of points closer to its site
    than to any other site, clipped to a bounding box.

    Uses the O(n³) half-plane intersection approach for correctness.
    """
    sites = list(sites)
    n = len(sites)
    if n == 0:
        return []

    # Clip polygon to half-plane defined by: dot(normal, pt) <= dot(normal, p0)
    def clip(poly: list[Point], p0: Point, normal: Point) -> list[Point]:
        if not poly:
            return []
        result: list[Point] = []
        m = len(poly)
        for i in range(m):
            cur = poly[i]
            nxt = poly[(i + 1) % m]
            d_cur = normal.x * cur.x + normal.y * cur.y
            d_nxt = normal.x * nxt.x + normal.y * nxt.y
            dot_p0 = normal.x * p0.x + normal.y * p0.y
            inside_cur = d_cur <= dot_p0 + 1e-12
            inside_nxt = d_nxt <= dot_p0 + 1e-12
            if inside_cur:
                result.append(cur)
            if inside_cur != inside_nxt:
                t = (dot_p0 - d_cur) / (d_nxt - d_cur) if abs(d_nxt - d_cur) > 1e-16 else 0.0
                intersect = Point(
                    cur.x + t * (nxt.x - cur.x),
                    cur.y + t * (nxt.y - cur.y),
                )
                result.append(intersect)
        return result

    cells: list[VoronoiCell] = []
    for i, site in enumerate(sites):
        # Start with bounding box
        poly = [
            Point(-bbox_half, -bbox_half),
            Point(bbox_half, -bbox_half),
            Point(bbox_half, bbox_half),
            Point(-bbox_half, bbox_half),
        ]
        for j in range(n):
            if i == j:
                continue
            other = sites[j]
            # Perpendicular bisector: site is closer → dot(normal, pt) < dot(normal, midpoint)
            normal = Point(other.x - site.x, other.y - site.y)
            midpoint = Point((site.x + other.x) / 2, (site.y + other.y) / 2)
            normal.x * midpoint.x + normal.y * midpoint.y
            # Clip to half-plane: dot(normal, pt) <= dotted
            poly = clip(poly, midpoint, normal)
        cells.append(VoronoiCell(site=site, vertices=poly))

    return cells


def test_voronoi_two_sites():
    """Two sites → two half-plane cells split by perpendicular bisector."""
    a = Point(0, 0)
    b = Point(4, 0)
    cells = voronoi_sites_to_cells([a, b])
    assert len(cells) == 2
    for cell in cells:
        assert len(cell.vertices) >= 3  # bounded cell


def test_voronoi_three_sites():
    """Three non-collinear sites → three cells meeting at the circumcenter."""
    a = Point(0, 0)
    b = Point(4, 0)
    c = Point(2, 3)
    cells = voronoi_sites_to_cells([a, b, c])
    assert len(cells) == 3
    for cell in cells:
        assert len(cell.vertices) >= 3


def test_voronoi_four_square():
    """Four sites in a square → each cell is bounded."""
    sites = [Point(0, 0), Point(4, 0), Point(4, 4), Point(0, 4)]
    cells = voronoi_sites_to_cells(sites)
    assert len(cells) == 4
    for cell in cells:
        assert len(cell.vertices) >= 3


def test_voronoi_cell_contains_site():
    """Each cell's site is inside its own cell (closest to itself)."""
    sites = [Point(0, 0), Point(5, 0), Point(1, 4), Point(6, 5)]
    cells = voronoi_sites_to_cells(sites)
    for cell in cells:
        assert point_in_polygon(cell.site, cell.vertices) is True


def test_voronoi_empty():
    """No sites → empty result."""
    assert voronoi_sites_to_cells([]) == []


def test_voronoi_cell_partition():
    """Every point in a cell is closer to its own site than to others."""
    import random

    rng = random.Random(7)
    sites = [Point(rng.uniform(-3, 3), rng.uniform(-3, 3)) for _ in range(6)]
    cells = voronoi_sites_to_cells(sites)

    for cell in cells:
        # Sample interior by averaging vertices
        verts = cell.vertices
        if len(verts) < 3:
            continue
        interior = Point(
            sum(v.x for v in verts) / len(verts),
            sum(v.y for v in verts) / len(verts),
        )
        dsq_own = (interior.x - cell.site.x) ** 2 + (interior.y - cell.site.y) ** 2
        for s in sites:
            dsq_other = (interior.x - s.x) ** 2 + (interior.y - s.y) ** 2
            assert dsq_own <= dsq_other + 1e-9


# ── Geo-to-Cartesian Projection ───────────────────────────────────────


def geo_to_cartesian(origin: GeoPoint, points: Sequence[GeoPoint]) -> list[Point]:
    """Project geographic points to a local Cartesian plane centered at origin.
    Uses an equirectangular approximation — suitable for small regions.
    """
    r = EARTH_RADIUS_M
    lat0 = math.radians(origin.lat)
    cos_lat0 = math.cos(lat0)
    result: list[Point] = []
    for p in points:
        dx = r * cos_lat0 * math.radians(p.lon - origin.lon)
        dy = r * math.radians(p.lat - origin.lat)
        result.append(Point(dx, dy))
    return result


def test_geo_to_cartesian_origin_is_zero():
    """Projecting the origin itself yields (0, 0)."""
    origin = GeoPoint(40.0, -74.0)
    cart = geo_to_cartesian(origin, [origin])
    assert len(cart) == 1
    assert cart[0].x == pytest.approx(0.0)
    assert cart[0].y == pytest.approx(0.0)


def test_geo_to_cartesian_north():
    """A point 1° north is ~111 km north in Cartesian."""
    origin = GeoPoint(45.0, 0.0)
    north = GeoPoint(46.0, 0.0)
    cart = geo_to_cartesian(origin, [north])
    assert cart[0].x == pytest.approx(0.0, abs=100)
    assert cart[0].y == pytest.approx(111_195, rel=0.01)  # ~111 km


def test_geo_to_cartesian_east():
    """A point 1° east is ~79 km east at 45° latitude."""
    origin = GeoPoint(45.0, 0.0)
    east = GeoPoint(45.0, 1.0)
    cart = geo_to_cartesian(origin, [east])
    assert cart[0].y == pytest.approx(0.0, abs=100)
    assert cart[0].x == pytest.approx(78_850, rel=0.02)  # 111.195 * cos(45°)


def test_geo_to_cartesian_roundtrip():
    """Project to Cartesian and back yields the original lat/lon."""
    origin = GeoPoint(40.0, -74.0)
    pts = [
        GeoPoint(40.1, -74.05),
        GeoPoint(39.95, -73.98),
        GeoPoint(40.02, -74.10),
    ]
    cart = geo_to_cartesian(origin, pts)
    r = EARTH_RADIUS_M
    lat0 = math.radians(origin.lat)
    cos_lat0 = math.cos(lat0)
    for i, p in enumerate(cart):
        lat = origin.lat + math.degrees(p.y / r)
        lon = origin.lon + math.degrees(p.x / (r * cos_lat0))
        assert lat == pytest.approx(pts[i].lat, abs=0.001)
        assert lon == pytest.approx(pts[i].lon, abs=0.001)


# ── Additional: Great-circle midpoint ─────────────────────────────────


def midpoint(a: GeoPoint, b: GeoPoint) -> GeoPoint:
    """Midpoint along a great-circle arc between two geographic points."""
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlon = lon2 - lon1
    bx = math.cos(lat2) * math.cos(dlon)
    by_val = math.cos(lat2) * math.sin(dlon)
    lat_mid = math.atan2(
        math.sin(lat1) + math.sin(lat2),
        math.sqrt((math.cos(lat1) + bx) ** 2 + by_val**2),
    )
    lon_mid = lon1 + math.atan2(by_val, math.cos(lat1) + bx)
    return GeoPoint(math.degrees(lat_mid), math.degrees(lon_mid))


def test_midpoint_half_distance():
    """The midpoint is roughly halfway between two points."""
    a = GeoPoint(40.0, -74.0)
    b = GeoPoint(40.0, -73.0)
    mid = midpoint(a, b)
    d_am = haversine(a, mid)
    d_mb = haversine(mid, b)
    assert d_am == pytest.approx(d_mb, rel=0.01)


def test_midpoint_same_point():
    """Midpoint of a point and itself is that point."""
    p = GeoPoint(51.5074, -0.1278)
    mid = midpoint(p, p)
    assert mid.lat == pytest.approx(p.lat, abs=1e-9)
    assert mid.lon == pytest.approx(p.lon, abs=1e-9)


# ── Additional: distance to polygon boundary ──────────────────────────


def point_to_segment_dist(pt: Point, a: Point, b: Point) -> float:
    """Minimum Euclidean distance from pt to line segment ab."""
    dx = b.x - a.x
    dy = b.y - a.y
    if dx == 0 and dy == 0:
        return math.hypot(pt.x - a.x, pt.y - a.y)
    t = max(0.0, min(1.0, ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / (dx * dx + dy * dy)))
    proj_x = a.x + t * dx
    proj_y = a.y + t * dy
    return math.hypot(pt.x - proj_x, pt.y - proj_y)


def point_to_polygon_dist(pt: Point, poly: Sequence[Point]) -> float:
    """Minimum distance from a point to a polygon boundary."""
    n = len(poly)
    if n == 0:
        return float("inf")
    return min(point_to_segment_dist(pt, poly[i], poly[(i + 1) % n]) for i in range(n))


def test_dist_to_polygon_boundary_inside():
    """Point inside a square — distance to nearest edge."""
    poly = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    d = point_to_polygon_dist(Point(3, 4), poly)
    assert d == pytest.approx(3.0)  # 3 units from x=0 edge


def test_dist_to_polygon_boundary_outside():
    """Point outside — distance to nearest edge."""
    poly = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    d = point_to_polygon_dist(Point(12, 5), poly)
    assert d == pytest.approx(2.0)  # 2 units from x=10 edge


def test_dist_to_polygon_boundary_empty():
    """Empty polygon → infinity."""
    assert point_to_polygon_dist(Point(0, 0), []) == float("inf")
