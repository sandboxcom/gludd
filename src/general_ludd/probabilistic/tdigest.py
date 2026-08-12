"""T-Digest: approximate quantile estimation with mergeable compressed centroids.

T-Digest is a data structure for accurate online estimation of quantiles
and cumulative distribution functions, supporting merges for distributed
computation.  Bounds the number of centroids to O(compression) via a
scale-function (k-size) invariant; extreme quantiles are represented with
higher resolution than medians because the scale function is steeper near
0 and 1.  Memory is O(compression); query time is O(compression).

Reference:
    Dunning & Ertl, "Computing Extremely Accurate Quantiles Using T-Digests"
    (2019), https://arxiv.org/abs/1902.04023.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Centroid:
    mean: float
    weight: float


class TDigestMergeError(ValueError):
    """Raised when two T-Digests cannot be merged because their parameters differ."""


def _scale(q: float, delta: float) -> float:
    """Scale function k(q, delta) — maps quantile to index in the centroid list."""
    return (delta / (2.0 * math.pi)) * math.asin(2.0 * q - 1.0)


def _inv_scale(k: float, delta: float) -> float:
    """Inverse scale function — maps centroid index back to quantile."""
    return 0.5 + 0.5 * math.sin(2.0 * math.pi * k / delta)


def _weight_integrated_location(centroids: list[Centroid], q: float, total_count: float) -> float:
    if q <= 0.0:
        return centroids[0].mean
    if q >= 1.0:
        return centroids[-1].mean
    target = q * total_count
    cumulative = 0.0
    for i, c in enumerate(centroids):
        if cumulative + c.weight >= target:
            if i == 0:
                return c.mean
            prev = centroids[i - 1]
            frac = (target - cumulative) / c.weight
            return prev.mean + frac * (c.mean - prev.mean)
        cumulative += c.weight
    return centroids[-1].mean


def _cdf_from_centroids(centroids: list[Centroid], x: float, total_count: float) -> float:
    if not centroids:
        return 0.0
    if x < centroids[0].mean:
        return 0.0
    if x >= centroids[-1].mean:
        return 1.0
    cumulative = 0.0
    for i, c in enumerate(centroids):
        if c.mean > x:
            if i == 0:
                return 0.0
            prev = centroids[i - 1]
            frac = (x - prev.mean) / (c.mean - prev.mean)
            return (cumulative + prev.weight * 0.5 + prev.weight * frac * 0.5) / total_count
        cumulative += c.weight
    return 1.0


def _merge_centroid_lists(a: list[Centroid], b: list[Centroid], compression: float) -> list[Centroid]:
    merged: list[Centroid] = sorted(a + b, key=lambda c: c.mean)
    if len(merged) <= 2:
        return merged

    total = sum(c.weight for c in merged)
    delta = float(compression)
    c0 = 0.0
    result: list[Centroid] = [merged[0]]
    for centroid in merged[1:]:
        q0 = c0 / total
        q1 = (c0 + centroid.weight) / total
        k_lo = _scale(q0, delta)
        k_hi = _scale(q1, delta)
        size = k_hi - k_lo
        last = result[-1]
        if last.weight + centroid.weight <= 1.0 or abs(size) < delta * 0.01:
            c_sum = last.mean * last.weight + centroid.mean * centroid.weight
            c_w = last.weight + centroid.weight
            result[-1] = Centroid(mean=c_sum / c_w if c_w > 0 else 0.0, weight=c_w)
        else:
            result.append(centroid)
        c0 += centroid.weight
    return result


class TDigest:
    """T-Digest for approximate quantile estimation.

    Parameters
    ----------
    compression : float
        Controls the number of centroids and error bound.
        Higher → tighter error, more memory.
        Must be positive.
    """

    __slots__ = ("_centroids", "_compression", "_count")

    def __init__(self, compression: float) -> None:
        if compression <= 0:
            raise ValueError(f"compression must be positive, got {compression}")
        self._compression: float = float(compression)
        self._centroids: list[Centroid] = []
        self._count: int = 0

    # ------------------------------------------------------------------
    # public properties
    # ------------------------------------------------------------------

    @property
    def compression(self) -> float:
        return self._compression

    @property
    def centroids(self) -> list[Centroid] | None:
        return list(self._centroids) if self._centroids else None

    @property
    def count(self) -> int:
        return self._count

    @property
    def min_value(self) -> float:
        if not self._centroids:
            raise ValueError("empty — no min_value")
        return self._centroids[0].mean

    @property
    def max_value(self) -> float:
        if not self._centroids:
            raise ValueError("empty — no max_value")
        return self._centroids[-1].mean

    # ------------------------------------------------------------------
    # insert
    # ------------------------------------------------------------------

    def add(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError(f"value must be finite, got {value}")
        self._centroids.append(Centroid(mean=value, weight=1.0))
        self._count += 1
        self._compress_after_add()

    def _compress_after_add(self) -> None:
        self._centroids.sort(key=lambda c: c.mean)
        self._centroids = _merge_centroid_lists(self._centroids, [], self._compression)

    # ------------------------------------------------------------------
    # merge
    # ------------------------------------------------------------------

    def merge(self, other: TDigest) -> None:
        if other._compression != self._compression:
            raise TDigestMergeError(f"Cannot merge: compression mismatch ({self._compression} vs {other._compression})")
        if other._count == 0:
            return
        if self._count == 0:
            self._centroids = list(other._centroids)
            self._count = other._count
            return
        self._centroids = _merge_centroid_lists(self._centroids, other._centroids, self._compression)
        self._count += other._count

    # ------------------------------------------------------------------
    # quantile
    # ------------------------------------------------------------------

    def quantile(self, q: float) -> float:
        if q < 0.0 or q > 1.0:
            raise ValueError(f"q must be between 0 and 1, got {q}")
        if self._count == 0:
            raise ValueError("empty TDigest — no quantile available")
        return _weight_integrated_location(self._centroids, q, float(self._count))

    # ------------------------------------------------------------------
    # cdf
    # ------------------------------------------------------------------

    def cdf(self, x: float) -> float:
        if self._count == 0:
            raise ValueError("empty TDigest — no CDF available")
        return _cdf_from_centroids(self._centroids, x, float(self._count))

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(struct.pack("<d", self._compression))
        buf.extend(struct.pack("<I", len(self._centroids)))
        for c in self._centroids:
            buf.extend(struct.pack("<dd", c.mean, c.weight))
        buf.extend(struct.pack("<I", self._count))
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> TDigest:
        if len(data) < 8:
            raise ValueError("need at least 8 bytes for compression header")
        offset = 0
        compression = struct.unpack_from("<d", data, offset)[0]
        offset += 8
        centroid_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        centroids: list[Centroid] = []
        for _ in range(centroid_len):
            mean, weight = struct.unpack_from("<dd", data, offset)
            offset += 16
            centroids.append(Centroid(mean=mean, weight=weight))
        count = struct.unpack_from("<I", data, offset)[0]
        td = cls(compression=compression)
        td._centroids = centroids
        td._count = count
        return td

    # ------------------------------------------------------------------
    # pickle support
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict[str, Any]:
        return {
            "compression": self._compression,
            "centroids": [(c.mean, c.weight) for c in self._centroids],
            "count": self._count,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self._compression = float(state["compression"])
        self._centroids = [Centroid(mean=m, weight=w) for m, w in state["centroids"]]
        self._count = int(state["count"])
