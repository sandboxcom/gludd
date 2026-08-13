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
    """Immutable weighted cluster used by a T-Digest.

    Attributes:
        mean: Representative value for the cluster.
        weight: Number of observations represented by the cluster.
    """

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
    if len(centroids) == 1:
        return centroids[0].mean

    target = q * total_count
    previous = centroids[0]
    previous_midpoint = previous.weight / 2.0
    if target <= previous_midpoint:
        return previous.mean

    cumulative = previous.weight
    for current in centroids[1:]:
        current_midpoint = cumulative + current.weight / 2.0
        if target <= current_midpoint:
            if current.mean == previous.mean:
                return previous.mean
            span = current_midpoint - previous_midpoint
            fraction = (target - previous_midpoint) / span
            return previous.mean + fraction * (current.mean - previous.mean)
        previous = current
        previous_midpoint = current_midpoint
        cumulative += current.weight
    return centroids[-1].mean


def _cdf_from_centroids(centroids: list[Centroid], x: float, total_count: float) -> float:
    if not centroids:
        return 0.0
    first = centroids[0]
    if x < first.mean:
        return 0.0
    if len(centroids) == 1:
        if x > first.mean:
            return 1.0
        return 0.5
    if x >= centroids[-1].mean:
        return 1.0

    previous = first
    previous_midpoint = previous.weight / 2.0
    if x == previous.mean:
        return previous_midpoint / total_count

    cumulative = previous.weight
    for current in centroids[1:]:
        current_midpoint = cumulative + current.weight / 2.0
        if x <= current.mean:
            if current.mean == previous.mean:
                rank = current_midpoint
            else:
                fraction = (x - previous.mean) / (current.mean - previous.mean)
                rank = previous_midpoint + fraction * (current_midpoint - previous_midpoint)
            return min(1.0, max(0.0, rank / total_count))
        previous = current
        previous_midpoint = current_midpoint
        cumulative += current.weight
    return 1.0


def _merge_centroid_lists(a: list[Centroid], b: list[Centroid], compression: float) -> list[Centroid]:
    merged: list[Centroid] = sorted(a + b, key=lambda c: c.mean)
    if len(merged) <= 1:
        return merged

    total = sum(c.weight for c in merged)
    weight_before = 0.0
    current = merged[0]
    result: list[Centroid] = []
    for index, centroid in enumerate(merged[1:], start=1):
        proposed_weight = current.weight + centroid.weight
        q0 = weight_before / total
        q1 = (weight_before + proposed_weight) / total
        within_scale_bound = _scale(q1, compression) - _scale(q0, compression) <= 1.0
        preserves_singleton_tail = weight_before > 0.0 and index < len(merged) - 1
        if within_scale_bound and preserves_singleton_tail:
            if current.mean == centroid.mean:
                mean = current.mean
            else:
                mean = current.mean + (centroid.mean - current.mean) * centroid.weight / proposed_weight
            current = Centroid(mean=mean, weight=proposed_weight)
        else:
            result.append(current)
            weight_before += current.weight
            current = centroid
    result.append(current)
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
        """Initialize an empty digest.

        Args:
            compression: Positive accuracy and memory trade-off parameter.

        Raises:
            ValueError: If ``compression`` is not positive.
        """
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
        """Return the configured compression parameter."""
        return self._compression

    @property
    def centroids(self) -> list[Centroid] | None:
        """Return a defensive copy of the centroids, or ``None`` when empty."""
        return list(self._centroids) if self._centroids else None

    @property
    def count(self) -> int:
        """Return the number of observations represented by the digest."""
        return self._count

    @property
    def min_value(self) -> float:
        """Return the exact minimum observation.

        Raises:
            ValueError: If the digest is empty.
        """
        if not self._centroids:
            raise ValueError("empty — no min_value")
        return self._centroids[0].mean

    @property
    def max_value(self) -> float:
        """Return the exact maximum observation.

        Raises:
            ValueError: If the digest is empty.
        """
        if not self._centroids:
            raise ValueError("empty — no max_value")
        return self._centroids[-1].mean

    # ------------------------------------------------------------------
    # insert
    # ------------------------------------------------------------------

    def add(self, value: float) -> None:
        """Add one finite observation to the digest.

        Args:
            value: Observation to incorporate.

        Raises:
            ValueError: If ``value`` is not finite.
        """
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
        """Merge another digest with the same compression into this digest.

        Args:
            other: Digest whose observations should be incorporated.

        Raises:
            TDigestMergeError: If the digests use different compression values.
        """
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
        """Estimate a quantile using centroid midpoint interpolation.

        Args:
            q: Quantile in the inclusive interval ``[0, 1]``.

        Returns:
            Estimated value at ``q``.

        Raises:
            ValueError: If ``q`` is outside ``[0, 1]`` or the digest is empty.
        """
        if q < 0.0 or q > 1.0:
            raise ValueError(f"q must be between 0 and 1, got {q}")
        if self._count == 0:
            raise ValueError("empty TDigest — no quantile available")
        return _weight_integrated_location(self._centroids, q, float(self._count))

    # ------------------------------------------------------------------
    # cdf
    # ------------------------------------------------------------------

    def cdf(self, x: float) -> float:
        """Estimate the cumulative probability at a value.

        Args:
            x: Value at which to evaluate the cumulative distribution.

        Returns:
            Estimated probability in the inclusive interval ``[0, 1]``.

        Raises:
            ValueError: If the digest is empty.
        """
        if self._count == 0:
            raise ValueError("empty TDigest — no CDF available")
        return _cdf_from_centroids(self._centroids, x, float(self._count))

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialize the digest using the stable binary representation."""
        buf = bytearray()
        buf.extend(struct.pack("<d", self._compression))
        buf.extend(struct.pack("<I", len(self._centroids)))
        for c in self._centroids:
            buf.extend(struct.pack("<dd", c.mean, c.weight))
        buf.extend(struct.pack("<I", self._count))
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> TDigest:
        """Deserialize a digest from its stable binary representation.

        Args:
            data: Bytes produced by :meth:`to_bytes`.

        Returns:
            Reconstructed digest.

        Raises:
            ValueError: If the compression header is missing.
        """
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
        """Return pickle state without changing the serialized field layout."""
        return {
            "compression": self._compression,
            "centroids": [(c.mean, c.weight) for c in self._centroids],
            "count": self._count,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore pickle state created by :meth:`__getstate__`."""
        self._compression = float(state["compression"])
        self._centroids = [Centroid(mean=m, weight=w) for m, w in state["centroids"]]
        self._count = int(state["count"])
