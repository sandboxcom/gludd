"""Physics-collection convex hull adapter via SciPy.

Delegates to scipy's Qhull-based implementation. All functions accept
a list of (x, y) points and return convex hull vertices in CCW order.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import ConvexHull, QhullError


def _compute_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(pts) <= 2:
        return pts[:]
    arr = np.array(pts, dtype=np.float64)
    try:
        hull = ConvexHull(arr)
    except QhullError:
        if np.ptp(arr[:, 0]) > 0:
            i_min, i_max = int(np.argmin(arr[:, 0])), int(np.argmax(arr[:, 0]))
        else:
            i_min, i_max = int(np.argmin(arr[:, 1])), int(np.argmax(arr[:, 1]))
        if i_min == i_max:
            return [tuple(arr[0])]
        return [tuple(arr[i_min]), tuple(arr[i_max])]
    return [tuple(arr[i]) for i in hull.vertices]


def graham_scan(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Graham scan O(n log n). Delegates to scipy ConvexHull."""
    return _compute_hull(pts)


def jarvis_march(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Jarvis march O(n h). Delegates to scipy ConvexHull."""
    return _compute_hull(pts)


def quickhull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """QuickHull O(n log n) average. Delegates to scipy ConvexHull."""
    return _compute_hull(pts)


def chans_algorithm(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Chan's algorithm O(n log h). Delegates to scipy ConvexHull."""
    return _compute_hull(pts)
