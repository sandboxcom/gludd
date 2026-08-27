"""Physics-collection K-means adapter backed by SciPy.
elbow method, and silhouette score.
"""

from __future__ import annotations

import random

import numpy as np
from numpy.typing import NDArray
from scipy.cluster.vq import kmeans as _scipy_kmeans
from scipy.cluster.vq import vq as _scipy_vq

FloatArray = NDArray[np.float64]


def _kmeans_pp_init(points: FloatArray, k: int, rng: random.Random | None = None) -> FloatArray:
    if rng is None:
        rng = random.Random()
    centroids = [points[rng.randint(0, len(points) - 1)].copy()]
    for _ in range(1, k):
        dists = np.array([min(((p - c) ** 2).sum() for c in centroids) for p in points])
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
    return np.asarray(centroids, dtype=np.float64)


def kmeans_plusplus(points: list[list[float]], k: int, seed: int | None = None) -> list[list[float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    pts = np.array(points, dtype=float)
    if k > len(pts):
        raise ValueError("k cannot exceed number of points")
    rng = random.Random(seed)
    return [[float(value) for value in row] for row in _kmeans_pp_init(pts, k, rng)]


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

    if k == 1:
        codebook, _distortion = _scipy_kmeans(pts, 1, iter=max_iters, thresh=tol, seed=seed)
    elif init == "kmeans++":
        rng = random.Random(seed)
        guess = _kmeans_pp_init(pts, k, rng)
        codebook, _distortion = _scipy_kmeans(pts, guess, iter=max_iters, thresh=tol)
    elif init == "random":
        codebook, _distortion = _scipy_kmeans(pts, k, iter=max_iters, thresh=tol, seed=seed)
    else:
        raise ValueError(f"Unknown init method: {init}")

    labels_arr, _dist = _scipy_vq(pts, codebook)
    return labels_arr.tolist(), codebook.tolist(), 1


def _inertia(points: list[list[float]], labels: list[int], centroids: list[list[float]]) -> float:
    pts = np.array(points, dtype=float)
    cents = np.array(centroids, dtype=float)
    return float(sum(((pts[i] - cents[labels[i]]) ** 2).sum() for i in range(len(pts))))


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
