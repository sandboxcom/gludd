"""Deep tests for k-means clustering: Lloyd, k-means++, elbow method, silhouette score."""

from __future__ import annotations

from ansible_collections.general_ludd.physics.plugins.module_utils.kmeans import (
    _inertia,
    elbow,
    fit,
    kmeans_plusplus,
    lloyd,
    silhouette_score,
)


def _squared_dist(a: list[float], b: list[float]) -> float:
    return sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False))


# ── simple datasets ───────────────────────────────────────────────────

BLOB_1D = [[0.0], [0.1], [0.05], [10.0], [10.1], [9.95]]

TWO_CLUSTERS_2D = [
    [0.0, 0.0],
    [0.5, 0.0],
    [0.0, 0.5],  # cluster near origin
    [10.0, 10.0],
    [10.5, 10.0],
    [10.0, 10.5],  # cluster far away
]

THREE_CLUSTERS_2D = [
    [0.0, 0.0],
    [0.2, 0.1],
    [0.1, 0.2],
    [5.0, 5.0],
    [5.1, 5.0],
    [5.0, 5.1],
    [10.0, 0.0],
    [10.1, 0.2],
    [10.0, -0.1],
]

IDENTICAL = [[1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0]]


# ── Lloyd's algorithm ─────────────────────────────────────────────────


def test_lloyd_single_cluster() -> None:
    labels, centroids, iters = lloyd(BLOB_1D, k=1, seed=42)
    assert labels == [0] * len(BLOB_1D)
    assert len(centroids) == 1
    assert iters >= 1


def test_lloyd_two_clusters_separated() -> None:
    labels, centroids, _iters = lloyd(TWO_CLUSTERS_2D, k=2, seed=42)
    assert len(set(labels)) == 2
    assert len(centroids) == 2
    c0 = labels[0]
    assert all(lbl == c0 for lbl in labels[:3])
    assert all(lbl != c0 for lbl in labels[3:])


def test_lloyd_three_clusters() -> None:
    labels, centroids, _iters = lloyd(THREE_CLUSTERS_2D, k=3, seed=42)
    assert len(set(labels)) == 3
    assert len(centroids) == 3
    for g in range(3):
        group = labels[g * 3 : (g + 1) * 3]
        assert len(set(group)) == 1


def test_lloyd_all_points_distinct_seeds_produce_same_k_clusters() -> None:
    for s in [0, 7, 42]:
        labels, _, _ = lloyd(TWO_CLUSTERS_2D, k=2, seed=s)
        assert len(set(labels)) == 2
        c0 = labels[0]
        assert all(lbl == c0 for lbl in labels[:3])
        assert all(lbl != c0 for lbl in labels[3:])


def test_lloyd_k_equals_n() -> None:
    pts = [[0.0], [1.0], [2.0]]
    labels, _centroids, _iters = lloyd(pts, k=3)
    assert len(set(labels)) == 3


def test_lloyd_converges_in_under_max_iters() -> None:
    points = [[float(i)] for i in range(20)]
    _labels, _centroids, iters = lloyd(points, k=3, max_iters=100, seed=0)
    assert iters < 100


def test_lloyd_random_init() -> None:
    labels, centroids, _iters = lloyd(TWO_CLUSTERS_2D, k=2, init="random", seed=42)
    assert len(set(labels)) == 2
    assert len(centroids) == 2


def test_lloyd_identity_points() -> None:
    labels, _centroids, _iters = lloyd(IDENTICAL, k=2, seed=0)
    assert len(labels) == len(IDENTICAL)


# ── validation ────────────────────────────────────────────────────────


def test_lloyd_raises_on_zero_k() -> None:
    try:
        lloyd(BLOB_1D, k=0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_lloyd_raises_k_exceeds_n() -> None:
    try:
        lloyd([[0.0]], k=2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_lloyd_raises_bad_init() -> None:
    try:
        lloyd(BLOB_1D, k=2, init="fuzzy")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── k-means++ ─────────────────────────────────────────────────────────


def test_kmeans_pp_returns_k_centroids() -> None:
    centroids = kmeans_plusplus(TWO_CLUSTERS_2D, k=2, seed=0)
    assert len(centroids) == 2
    for c in centroids:
        assert len(c) == 2  # 2D


def test_kmeans_pp_all_centroids_from_data() -> None:
    centroids = kmeans_plusplus(TWO_CLUSTERS_2D, k=2, seed=0)
    for c in centroids:
        assert c in TWO_CLUSTERS_2D


def test_kmeans_pp_deterministic_with_seed() -> None:
    c1 = kmeans_plusplus(TWO_CLUSTERS_2D, k=2, seed=42)
    c2 = kmeans_plusplus(TWO_CLUSTERS_2D, k=2, seed=42)
    assert c1 == c2


def test_kmeans_pp_raises_on_zero_k() -> None:
    try:
        kmeans_plusplus(TWO_CLUSTERS_2D, k=0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── elbow method ──────────────────────────────────────────────────────


def test_elbow_returns_monotonic_decreasing() -> None:
    points = [
        [0.0, 0.0],
        [0.1, 0.0],
        [9.9, 10.0],
        [10.0, 10.0],
        [5.0, 5.0],
        [5.1, 4.9],
    ]
    inertias = elbow(points, k_min=1, k_max=4, seed=42)
    assert len(inertias) == 4
    for i in range(len(inertias) - 1):
        assert inertias[i] >= inertias[i + 1]


def test_elbow_k1_returns_single_inertia() -> None:
    inertias = elbow(TWO_CLUSTERS_2D, k_min=1, k_max=1, seed=0)
    assert len(inertias) == 1
    assert inertias[0] > 0


def test_elbow_truncates_at_n() -> None:
    pts = [[0.0], [1.0], [2.0]]
    inertias = elbow(pts, k_min=1, k_max=10, seed=0)
    assert len(inertias) == 3  # truncated at n=3


def test_elbow_raises_on_invalid_range() -> None:
    pts = [[0.0]]
    try:
        elbow(pts, k_min=5, k_max=2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── silhouette score ──────────────────────────────────────────────────


def test_silhouette_score_in_range() -> None:
    labels, centroids, _ = lloyd(TWO_CLUSTERS_2D, k=2, seed=42)
    s = silhouette_score(TWO_CLUSTERS_2D, labels, centroids)
    assert -1.0 <= s <= 1.0


def test_silhouette_well_separated_is_high() -> None:
    labels, centroids, _ = lloyd(TWO_CLUSTERS_2D, k=2, seed=42)
    s = silhouette_score(TWO_CLUSTERS_2D, labels, centroids)
    assert s > 0.5


def test_silhouette_single_point() -> None:
    s = silhouette_score([[1.0, 1.0]], [0], [[1.0, 1.0]])
    assert s == 0.0


def test_silhouette_single_cluster() -> None:
    s = silhouette_score(TWO_CLUSTERS_2D, [0] * 6, [[0.0, 0.0]])
    assert s == 0.0


def test_silhouette_all_same_point() -> None:
    pts = [[0.0, 0.0], [0.0, 0.0]]
    labels, centroids, _ = lloyd(pts, k=2, seed=0)
    s = silhouette_score(pts, labels, centroids)
    assert -1.0 <= s <= 1.0


# ── inertia ───────────────────────────────────────────────────────────


def test_inertia_positive() -> None:
    labels, centroids, _ = lloyd(TWO_CLUSTERS_2D, k=2, seed=0)
    inert = _inertia(TWO_CLUSTERS_2D, labels, centroids)
    assert inert >= 0


def test_inertia_zero_for_perfect_centroids() -> None:
    pts = [[0.0], [0.0], [0.0]]
    inert = _inertia(pts, [0, 0, 0], [[0.0]])
    assert inert == 0.0


# ── fit convenience ───────────────────────────────────────────────────


def test_fit_returns_labels_centroids_iters_inertia() -> None:
    labels, centroids, iters, inertia = fit(TWO_CLUSTERS_2D, k=2, seed=42)
    assert len(labels) == len(TWO_CLUSTERS_2D)
    assert len(centroids) == 2
    assert iters >= 1
    assert inertia >= 0


def test_fit_on_three_clusters() -> None:
    labels, _centroids, iters, _inertia = fit(THREE_CLUSTERS_2D, k=3, seed=42)
    assert len(set(labels)) == 3
    assert iters < 100
