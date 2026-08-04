"""K-means clustering: Lloyd's algorithm, k-means++ initialization,
elbow method, and silhouette score.

Pure-Python, stdlib only. All functions accept a list of (float, ...)
points and return cluster assignments as list[int].
"""

from __future__ import annotations

import math
import random


def _centroid(points: list[list[float]]) -> list[float]:
    if not points:
        return []
    d = len(points[0])
    n = len(points)
    return [sum(p[j] for p in points) / n for j in range(d)]


def _squared_dist(a: list[float], b: list[float]) -> float:
    return sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False))


def _assign_clusters(points: list[list[float]], centroids: list[list[float]]) -> list[int]:
    labels: list[int] = []
    for p in points:
        best = 0
        best_dist = _squared_dist(p, centroids[0])
        for c in range(1, len(centroids)):
            d = _squared_dist(p, centroids[c])
            if d < best_dist:
                best_dist = d
                best = c
        labels.append(best)
    return labels


def _kmeans_pp_init(points: list[list[float]], k: int, rng: random.Random | None = None) -> list[list[float]]:
    if rng is None:
        rng = random.Random()
    centroids: list[list[float]] = [list(rng.choice(points))]
    for _ in range(1, k):
        dists = [min(_squared_dist(p, c) for c in centroids) for p in points]
        total = sum(dists)
        if total == 0:
            remaining = [list(p) for p in points if p not in centroids]
            if remaining:
                centroids.append(rng.choice(remaining))
            else:
                centroids.append(list(points[0]))
            continue
        r = rng.random() * total
        acc = 0.0
        chosen = points[0]
        for p, d in zip(points, dists, strict=False):
            acc += d
            if acc >= r:
                chosen = p
                break
        centroids.append(list(chosen))
    return centroids


# ── Lloyd's algorithm ─────────────────────────────────────────────────


def lloyd(
    points: list[list[float]],
    k: int,
    max_iters: int = 100,
    tol: float = 1e-6,
    init: str = "kmeans++",
    seed: int | None = None,
) -> tuple[list[int], list[list[float]], int]:
    """Lloyd's algorithm for k-means clustering.

    Returns (labels, centroids, iterations_run).
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if k > len(points):
        raise ValueError("k cannot exceed number of points")
    rng = random.Random(seed)

    if init == "kmeans++":
        centroids = _kmeans_pp_init(points, k, rng)
    elif init == "random":
        centroids = [list(c) for c in rng.sample(points, k)]
    else:
        raise ValueError(f"Unknown init method: {init}")

    labels = _assign_clusters(points, centroids)
    _it = 0
    for _it in range(1, max_iters + 1):
        new_centroids: list[list[float]] = []
        for c in range(k):
            cluster_pts = [points[i] for i, lbl in enumerate(labels) if lbl == c]
            new_centroids.append(_centroid(cluster_pts) if cluster_pts else centroids[c])
        max_shift = max(math.sqrt(_squared_dist(nc, oc)) for nc, oc in zip(new_centroids, centroids, strict=False))
        centroids = new_centroids
        new_labels = _assign_clusters(points, centroids)
        if new_labels == labels and max_shift < tol:
            break
        labels = new_labels

    return labels, centroids, _it


# ── k-means++ initialization (standalone) ─────────────────────────────


def kmeans_plusplus(points: list[list[float]], k: int, seed: int | None = None) -> list[list[float]]:
    """k-means++ initialization — returns centroids."""
    if k <= 0:
        raise ValueError("k must be positive")
    if k > len(points):
        raise ValueError("k cannot exceed number of points")
    rng = random.Random(seed)
    return _kmeans_pp_init(points, k, rng)


# ── elbow method ──────────────────────────────────────────────────────


def _inertia(points: list[list[float]], labels: list[int], centroids: list[list[float]]) -> float:
    return sum(_squared_dist(p, centroids[labels[i]]) for i, p in enumerate(points))


def elbow(
    points: list[list[float]],
    k_min: int = 1,
    k_max: int = 10,
    seed: int | None = None,
) -> list[float]:
    """Compute inertia (WCSS) for k = k_min .. k_max. Returns list of inertia values."""
    if k_min < 1:
        raise ValueError("k_min must be >= 1")
    if k_max < k_min:
        raise ValueError("k_max must be >= k_min")

    inertias: list[float] = []
    for k in range(k_min, k_max + 1):
        if k > len(points):
            break
        labels, centroids, _ = lloyd(points, k, seed=seed)
        inertias.append(_inertia(points, labels, centroids))
    return inertias


# ── silhouette score ──────────────────────────────────────────────────


def silhouette_score(points: list[list[float]], labels: list[int], centroids: list[list[float]]) -> float:
    """Compute mean silhouette score over all points.

    Returns a value in [-1, 1]. Higher is better.
    """
    n = len(points)
    if n <= 1:
        return 0.0
    k = len(centroids)
    if k <= 1:
        return 0.0

    total = 0.0
    for i, p in enumerate(points):
        own = labels[i]

        own_pts = [points[j] for j in range(n) if labels[j] == own and j != i]
        a = sum(_squared_dist(p, q) for q in own_pts) / len(own_pts) if own_pts else 0.0

        b = float("inf")
        for other in range(k):
            if other == own:
                continue
            other_pts = [points[j] for j in range(n) if labels[j] == other]
            if not other_pts:
                continue
            d = sum(_squared_dist(p, q) for q in other_pts) / len(other_pts)
            b = min(b, d)

        s = 0.0 if (a == 0 and b == float("inf")) or a == 0 or b == 0 else (b - a) / max(a, b)
        total += s

    return total / n


# ── convenience ───────────────────────────────────────────────────────


def fit(
    points: list[list[float]],
    k: int,
    max_iters: int = 100,
    seed: int | None = None,
) -> tuple[list[int], list[list[float]], int, float]:
    """Fit k-means to points. Returns (labels, centroids, iters, inertia)."""
    labels, centroids, iters = lloyd(points, k, max_iters=max_iters, seed=seed)
    return labels, centroids, iters, _inertia(points, labels, centroids)
