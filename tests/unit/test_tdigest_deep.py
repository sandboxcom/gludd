"""Deep tests for TDigest — merge, quantile, CDF, compression, serialization."""

from __future__ import annotations

import math
import pickle
import random

import pytest

from general_ludd.probabilistic.tdigest import TDigest, TDigestMergeError


class TestTDigestSingleValue:
    def test_empty_quantile_raises(self) -> None:
        td = TDigest(compression=100)
        with pytest.raises(ValueError, match="empty"):
            td.quantile(0.5)

    def test_empty_cdf_raises(self) -> None:
        td = TDigest(compression=100)
        with pytest.raises(ValueError, match="empty"):
            td.cdf(0.0)

    def test_single_value_quantile(self) -> None:
        td = TDigest(compression=100)
        td.add(42.0)
        assert td.quantile(0.0) == 42.0
        assert td.quantile(0.5) == 42.0
        assert td.quantile(1.0) == 42.0

    def test_single_value_cdf(self) -> None:
        td = TDigest(compression=100)
        td.add(7.0)
        assert td.cdf(7.0) == 0.5
        assert td.cdf(6.0) == 0.0
        assert td.cdf(8.0) == 1.0

    def test_count_single(self) -> None:
        td = TDigest(compression=100)
        assert td.count == 0
        td.add(1.0)
        assert td.count == 1


class TestTDigestUniformDistribution:
    def test_median_uniform(self) -> None:
        td = TDigest(compression=200)
        for v in range(10001):
            td.add(float(v))
        q50 = td.quantile(0.5)
        assert abs(q50 - 5000.0) < 200.0

    def test_quantiles_uniform(self) -> None:
        td = TDigest(compression=500)
        for v in range(10001):
            td.add(float(v))
        for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
            estimated = td.quantile(q)
            true = q * 10000.0
            assert abs(estimated - true) < 300.0, f"quantile {q}: {estimated} vs {true}"

    def test_cdf_uniform(self) -> None:
        td = TDigest(compression=500)
        for v in range(1001):
            td.add(float(v))
        for x in (0, 250, 500, 750, 1000):
            c = td.cdf(float(x))
            true = (x + 0.5) / 1001.0
            assert abs(c - true) < 0.05, f"cdf({x})={c} vs {true}"

    def test_count_uniform(self) -> None:
        td = TDigest(compression=100)
        for v in range(10000):
            td.add(float(v))
        assert td.count == 10000


class TestTDigestMerge:
    def test_merge_two_identical(self) -> None:
        a = TDigest(compression=100)
        b = TDigest(compression=100)
        for v in range(1000):
            a.add(float(v))
            b.add(float(v))
        a.merge(b)
        assert a.count == 2000
        assert abs(a.quantile(0.5) - 500.0) < 50.0

    def test_merge_disjoint_ranges(self) -> None:
        a = TDigest(compression=100)
        b = TDigest(compression=100)
        for v in range(1000):
            a.add(float(v))
        for v in range(10000, 11000):
            b.add(float(v))
        a.merge(b)
        assert a.count == 2000
        q01 = a.quantile(0.01)
        q50 = a.quantile(0.5)
        q99 = a.quantile(0.99)
        assert q01 < 100.0
        assert q99 > 10900.0
        assert 450.0 < q50 < 10550.0
        assert a.min_value == 0.0
        assert a.max_value == 10999.0
        assert a.centroids is not None
        assert sum(c.weight for c in a.centroids) == 2000
        assert all(left.mean <= right.mean for left, right in zip(a.centroids, a.centroids[1:], strict=False))

    def test_merge_different_compression_raises(self) -> None:
        a = TDigest(compression=100)
        b = TDigest(compression=200)
        with pytest.raises(TDigestMergeError, match="compression"):
            a.merge(b)

    def test_merge_empty_is_noop(self) -> None:
        a = TDigest(compression=100)
        b = TDigest(compression=100)
        for v in range(100):
            a.add(float(v))
        a.merge(b)
        assert a.count == 100

    def test_merge_into_empty(self) -> None:
        a = TDigest(compression=100)
        b = TDigest(compression=100)
        for v in range(100):
            b.add(float(v))
        a.merge(b)
        assert a.count == 100


class TestTDigestCompression:
    def test_ordered_input_preserves_singleton_extremes_and_size_bound(self) -> None:
        for compression in (10, 50, 200):
            td = TDigest(compression=compression)
            for value in range(10000):
                td.add(float(value))

            assert td.centroids is not None
            assert td.centroids[0].weight == 1.0
            assert td.centroids[-1].weight == 1.0
            assert len(td.centroids) <= 2 * compression + 10

    def test_higher_compression_never_reduces_ordered_resolution(self) -> None:
        low = TDigest(compression=10)
        high = TDigest(compression=200)
        for value in range(10000):
            low.add(float(value))
            high.add(float(value))

        assert low.centroids is not None
        assert high.centroids is not None
        assert len(high.centroids) >= len(low.centroids)
        assert abs(high.quantile(0.99) - 9900.0) <= abs(low.quantile(0.99) - 9900.0)

    def test_low_compression_loose_bounds(self) -> None:
        td = TDigest(compression=10)
        for v in range(10000):
            td.add(float(v))
        q50 = td.quantile(0.5)
        assert abs(q50 - 5000.0) < 600.0

    def test_high_compression_tight_bounds(self) -> None:
        td = TDigest(compression=500)
        for v in range(10000):
            td.add(float(v))
        q50 = td.quantile(0.5)
        assert abs(q50 - 5000.0) < 100.0

    def test_centroid_count_within_compression(self) -> None:
        for c in (10, 50, 200):
            td = TDigest(compression=c)
            for _ in range(10000):
                td.add(random.gauss(0, 1))
            assert td.centroids is not None
            assert len(td.centroids) <= 2 * c + 10


class TestTDigestSkewedDistribution:
    def test_long_tail_quantiles(self) -> None:
        td = TDigest(compression=200)
        rng = random.Random(42)
        for _ in range(20000):
            v = rng.expovariate(1.0)
            td.add(v)
        q50 = td.quantile(0.5)
        q95 = td.quantile(0.95)
        q99 = td.quantile(0.99)
        assert q50 < 1.5
        assert q95 > 2.0
        assert q99 > 3.5
        assert q50 < q95 < q99

    def test_bimodal_quantiles(self) -> None:
        td = TDigest(compression=200)
        rng = random.Random(7)
        for _ in range(5000):
            td.add(rng.gauss(0, 1))
        for _ in range(5000):
            td.add(rng.gauss(10, 1))
        q50 = td.quantile(0.5)
        q25 = td.quantile(0.25)
        q75 = td.quantile(0.75)
        assert q25 < q50 < q75
        assert 0.0 < q50 < 10.0

    def test_power_law_quantiles(self) -> None:
        td = TDigest(compression=300)
        rng = random.Random(99)
        for _ in range(10000):
            td.add(rng.paretovariate(2.0))
        q10 = td.quantile(0.1)
        q90 = td.quantile(0.9)
        q99 = td.quantile(0.99)
        assert q10 < q90 < q99
        assert q90 / q10 > 2.0


class TestTDigestBoundaryValues:
    def test_inf_raises(self) -> None:
        td = TDigest(compression=100)
        with pytest.raises(ValueError, match="finite"):
            td.add(float("inf"))
        with pytest.raises(ValueError, match="finite"):
            td.add(float("-inf"))

    def test_nan_raises(self) -> None:
        td = TDigest(compression=100)
        with pytest.raises(ValueError, match="finite"):
            td.add(float("nan"))

    def test_very_small_values(self) -> None:
        td = TDigest(compression=100)
        for scale in range(-20, 21):
            td.add(math.exp(float(scale)))
        assert td.quantile(0.0) > 0.0
        assert td.quantile(1.0) > 0.0
        assert td.cdf(1e-10) >= 0.0
        assert td.cdf(1e10) <= 1.0

    def test_very_large_values(self) -> None:
        td = TDigest(compression=100)
        for e in range(0, 20):
            td.add(10.0**e)
        q50 = td.quantile(0.5)
        assert 1.0 <= q50 <= 1e19

    def test_identical_values(self) -> None:
        td = TDigest(compression=100)
        for _ in range(10000):
            td.add(3.14)
        assert td.quantile(0.0) == 3.14
        assert td.quantile(0.5) == 3.14
        assert td.quantile(1.0) == 3.14
        assert td.count == 10000


class TestTDigestQuantileInputValidation:
    def test_quantile_below_zero_raises(self) -> None:
        td = TDigest(compression=100)
        td.add(1.0)
        with pytest.raises(ValueError, match="must be between"):
            td.quantile(-0.01)

    def test_quantile_above_one_raises(self) -> None:
        td = TDigest(compression=100)
        td.add(1.0)
        with pytest.raises(ValueError, match="must be between"):
            td.quantile(1.01)


class TestTDigestSerialization:
    def test_pickle_roundtrip(self) -> None:
        td = TDigest(compression=100)
        for v in range(5000):
            td.add(float(v))
        data = pickle.dumps(td)
        td2 = pickle.loads(data)
        assert td2.compression == 100
        assert td2.count == 5000
        assert abs(td2.quantile(0.5) - 2500.0) < 100.0
        assert abs(td2.cdf(2500.0) - 0.5) < 0.05

    def test_bytes_roundtrip(self) -> None:
        td = TDigest(compression=50)
        for v in range(1000):
            td.add(math.sin(float(v) * 0.01))
        raw = td.to_bytes()
        td2 = TDigest.from_bytes(raw)
        assert td2.compression == 50
        assert td2.count == 1000
        for q in (0.1, 0.5, 0.9):
            assert abs(td.quantile(q) - td2.quantile(q)) < 1e-10

    def test_from_bytes_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 8 bytes"):
            TDigest.from_bytes(b"\x00\x01")


class TestTDigestNormalDistribution:
    def test_normal_quantiles_against_scipy(self) -> None:
        td = TDigest(compression=300)
        rng = random.Random(123)
        for _ in range(20000):
            td.add(rng.gauss(100, 15))
        q01 = td.quantile(0.01)
        q50 = td.quantile(0.5)
        q99 = td.quantile(0.99)
        assert 60.0 < q01 < 70.0
        assert 95.0 < q50 < 105.0
        assert 130.0 < q99 < 140.0

    def test_normal_cdf_monotonic(self) -> None:
        td = TDigest(compression=200)
        rng = random.Random(42)
        for _ in range(5000):
            td.add(rng.gauss(0, 1))
        prev = 0.0
        for x_scaled in range(-40, 41):
            x = x_scaled * 0.1
            cur = td.cdf(x)
            assert cur >= prev - 1e-12, f"non-monotonic at {x}: {prev} -> {cur}"
            prev = cur

    def test_normal_cdf_boundaries(self) -> None:
        td = TDigest(compression=200)
        rng = random.Random(1)
        for _ in range(5000):
            td.add(rng.gauss(0, 1))
        assert 0.0 <= td.cdf(-10.0) <= 1.0
        assert 0.0 <= td.cdf(10.0) <= 1.0
        assert td.cdf(-10.0) < td.cdf(10.0)

    def test_min_max_helpers(self) -> None:
        td = TDigest(compression=100)
        rng = random.Random(9)
        values = [rng.gauss(50, 10) for _ in range(1000)]
        for v in values:
            td.add(v)
        assert td.min_value == pytest.approx(min(values), abs=5.0)
        assert td.max_value == pytest.approx(max(values), abs=5.0)


class TestTDigestExtremeCompressions:
    def test_compression_1(self) -> None:
        td = TDigest(compression=1)
        for v in range(5000):
            td.add(float(v))
        q50 = td.quantile(0.5)
        assert abs(q50 - 2500.0) < 1000.0

    def test_compression_1000(self) -> None:
        td = TDigest(compression=1000)
        for v in range(5000):
            td.add(float(v))
        q50 = td.quantile(0.5)
        assert abs(q50 - 2500.0) < 50.0

    def test_compression_nonpositive_raises(self) -> None:
        with pytest.raises(ValueError, match="compression"):
            TDigest(compression=0)
        with pytest.raises(ValueError, match="compression"):
            TDigest(compression=-1)
