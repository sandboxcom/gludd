"""K-means clustering backed by numpy: Lloyd's algorithm, k-means++ initialization,
elbow method, and silhouette score.
"""

from __future__ import annotations

import math
import random

import numpy as np


def _centroid(points: np.ndarray) -> np.ndarray:
    return points.mean(axis=0)


def _squared_dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(((a - b) ** 2).sum())


def _assign_clusters(points: np.ndarray, centroids: np.ndarray) -> list[int]:
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


def _kmeans_pp_init(points: np.ndarray, k: int, rng: random.Random | None = None) -> np.ndarray:
    if rng is None:
        rng = random.Random()
    centroids = [points[rng.randint(0, len(points) - 1)].copy()]
    for _ in range(1, k):
        dists = np.array([min(_squared_dist(p, c) for c in centroids) for p in points])
        total = float(dists.sum())
        if total == 0:
            remaining = [p for p in points if not any(np.array_equal(p, c) for c in centroids)]
            if remaining:
                centroids.append(remaining[rng.randint(0, len(remaining) - 1)].copy())
            else:
                centroids.append(points[0].copy())
            continue
        r = rng.random() * total
        acc = 0.0
        chosen = points[0]
        for p, d in zip(points, dists, strict=False):
            acc += float(d)
            if acc >= r:
                chosen = p
                break
        centroids.append(chosen.copy())
    return np.array(centroids)


def lloyd(
    points: list[list[float]],
    k: int,
    max_iters: int = 100,
    tol: float = 1e-6,
    init: str = "kmeans++",
    seed: int | None = None,
) -> tuple[list[int], list[list[float]], int]:
    if k <= 0:
        raise ValueError("k must be positive")
    pts = np.array(points, dtype=float)
    if k > len(pts):
        raise ValueError("k cannot exceed number of points")
    rng = random.Random(seed)

    if init == "kmeans++":
        centroids = _kmeans_pp_init(pts, k, rng)
    elif init == "random":
        indices = rng.sample(range(len(pts)), k)
        centroids = pts[indices].copy()
    else:
        raise ValueError(f"Unknown init method: {init}")

    labels = _assign_clusters(pts, centroids)
    _it = 0
    for _it in range(1, max_iters + 1):
        new_centroids = []
        for c in range(k):
            cluster_pts = pts[[i for i, lbl in enumerate(labels) if lbl == c]]
            new_centroids.append(cluster_pts.mean(axis=0) if len(cluster_pts) > 0 else centroids[c])
        new_centroids_arr = np.array(new_centroids)

        max_shift = max(_squared_dist(nc, oc) for nc, oc in zip(new_centroids_arr, centroids, strict=False))
        max_shift = math.sqrt(max_shift)
        centroids = new_centroids_arr
        new_labels = _assign_clusters(pts, centroids)
        if new_labels == labels and max_shift < tol:
            break
        labels = new_labels

    return labels, centroids.tolist(), _it


def kmeans_plusplus(points: list[list[float]], k: int, seed: int | None = None) -> list[list[float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    pts = np.array(points, dtype=float)
    if k > len(pts):
        raise ValueError("k cannot exceed number of points")
    rng = random.Random(seed)
    return _kmeans_pp_init(pts, k, rng).tolist()


def _inertia(points: list[list[float]], labels: list[int], centroids: list[list[float]]) -> float:
    pts = np.array(points, dtype=float)
    cents = np.array(centroids, dtype=float)
    return float(sum(_squared_dist(pts[i], cents[labels[i]]) for i in range(len(pts))))


def elbow(
    points: list[list[float]],
    k_min: int = 1,
    k_max: int = 10,
    seed: int | None = None,
) -> list[float]:
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


def silhouette_score(points: list[list[float]], labels: list[int], centroids: list[list[float]]) -> float:
    n = len(points)
    if n <= 1:
        return 0.0
    k = len(centroids)
    if k <= 1:
        return 0.0

    pts = np.array(points, dtype=float)
    total = 0.0
    for i, p in enumerate(pts):
        own = labels[i]

        own_mask = np.array([j for j in range(n) if labels[j] == own and j != i], dtype=int)
        a = float((((pts[own_mask] - p) ** 2).sum(axis=1)).mean()) if len(own_mask) > 0 else 0.0

        b = float("inf")
        for other in range(k):
            if other == own:
                continue
            other_mask = np.array([j for j in range(n) if labels[j] == other], dtype=int)
            if len(other_mask) == 0:
                continue
            d = float((((pts[other_mask] - p) ** 2).sum(axis=1)).mean())
            b = min(b, d)

        s = 0.0 if (a == 0 and b == float("inf")) or a == 0 or b == 0 else (b - a) / max(a, b)
        total += s

    return total / n


def fit(
    points: list[list[float]],
    k: int,
    max_iters: int = 100,
    seed: int | None = None,
) -> tuple[list[int], list[list[float]], int, float]:
    labels, centroids, iters = lloyd(points, k, max_iters=max_iters, seed=seed)
    return labels, centroids, iters, _inertia(points, labels, centroids)
