"""Deep tests for Count-Min Sketch — frequency estimation, heavy hitters, conservative update."""

from __future__ import annotations

import math
import os
import tempfile

import pytest

from general_ludd.probabilistic.count_min_sketch import CountMinSketch


class TestCountMinSketchAddEstimate:
    def test_add_and_estimate_single_item(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        cms.add("hello")
        assert cms.estimate("hello") >= 1

    def test_empty_sketch_estimates_zero(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        assert cms.estimate("anything") == 0
        assert cms.estimate("nope") == 0

    def test_add_multiple_items(self) -> None:
        cms = CountMinSketch(width=500, depth=6)
        items = ["alpha", "beta", "gamma"]
        for item in items:
            for _ in range(10):
                cms.add(item)
        for item in items:
            assert cms.estimate(item) >= 10

    def test_add_with_count(self) -> None:
        cms = CountMinSketch(width=200, depth=4)
        cms.add("key", count=7)
        assert cms.estimate("key") >= 7

    def test_add_count_must_be_positive(self) -> None:
        cms = CountMinSketch(width=100, depth=5)
        with pytest.raises(ValueError, match="count must be >= 1"):
            cms.add("x", count=0)

    def test_frequency_estimate_never_underestimates(self) -> None:
        cms = CountMinSketch(width=1000, depth=6)
        n = 500
        for _ in range(n):
            cms.add("frequent")
        est = cms.estimate("frequent")
        assert est >= n


class TestCountMinSketchConservative:
    def test_conservative_add(self) -> None:
        cms = CountMinSketch(width=300, depth=5, conservative=True)
        for _ in range(100):
            cms.add("item_a")
        for _ in range(50):
            cms.add("item_b")
        assert cms.estimate("item_a") >= 1
        assert cms.estimate("item_b") >= 1

    def test_conservative_more_accurate(self) -> None:
        normal = CountMinSketch(width=500, depth=5, conservative=False)
        conserv = CountMinSketch(width=500, depth=5, conservative=True)
        for _ in range(200):
            normal.add("target")
            conserv.add("target")
        normal_err = normal.estimate("target") - 200
        conserv_err = conserv.estimate("target") - 200
        assert conserv_err <= normal_err

    def test_conservative_property(self) -> None:
        cms = CountMinSketch(width=100, depth=3, conservative=True)
        assert cms.conservative is True

    def test_conservative_false_by_default(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        assert cms.conservative is False


class TestCountMinSketchHeavyHitters:
    def test_heavy_hitters_returns_above_threshold(self) -> None:
        cms = CountMinSketch(width=500, depth=5)
        items = {"a", "b", "c", "d", "e"}
        for _ in range(20):
            cms.add("a")
        for _ in range(15):
            cms.add("b")
        for _ in range(5):
            cms.add("c")
        cms.add("d")
        hh = cms.heavy_hitters(threshold=10, candidates=items)
        names = {item for item, _ in hh}
        assert "a" in names
        assert "b" in names
        assert "c" not in names
        assert "d" not in names

    def test_heavy_hitters_sorted_by_frequency(self) -> None:
        cms = CountMinSketch(width=500, depth=5)
        items = {"x", "y", "z"}
        for _ in range(30):
            cms.add("x")
        for _ in range(20):
            cms.add("y")
        for _ in range(10):
            cms.add("z")
        hh = cms.heavy_hitters(threshold=10, candidates=items)
        freqs = [f for _, f in hh]
        assert freqs == sorted(freqs, reverse=True)

    def test_heavy_hitters_no_candidates(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        for _ in range(10):
            cms.add("present")
        hh = cms.heavy_hitters(threshold=5)
        assert hh == []

    def test_heavy_hitters_invalid_threshold(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        with pytest.raises(ValueError, match="threshold must be >= 1"):
            cms.heavy_hitters(threshold=0, candidates={"a"})

    def test_heavy_hitters_empty_candidates(self) -> None:
        cms = CountMinSketch(width=100, depth=3)
        for _ in range(10):
            cms.add("item")
        hh = cms.heavy_hitters(threshold=5, candidates=set())
        assert hh == []


class TestCountMinSketchMerge:
    def test_merge_two_sketches(self) -> None:
        a = CountMinSketch(width=200, depth=4)
        b = CountMinSketch(width=200, depth=4)
        a.add("x", count=5)
        b.add("x", count=3)
        b.add("y", count=7)
        a.merge(b)
        assert a.estimate("x") >= 8
        assert a.estimate("y") >= 7

    def test_merge_different_dimensions_raises(self) -> None:
        a = CountMinSketch(width=100, depth=3)
        b = CountMinSketch(width=200, depth=3)
        with pytest.raises(ValueError, match="different dimensions"):
            a.merge(b)

    def test_merge_different_depth_raises(self) -> None:
        a = CountMinSketch(width=100, depth=3)
        b = CountMinSketch(width=100, depth=5)
        with pytest.raises(ValueError, match="different dimensions"):
            a.merge(b)


class TestCountMinSketchClear:
    def test_clear_resets_all_counters(self) -> None:
        cms = CountMinSketch(width=100, depth=4)
        cms.add("persist", count=50)
        assert cms.estimate("persist") >= 1
        cms.clear()
        assert cms.estimate("persist") == 0

    def test_clear_on_empty_is_noop(self) -> None:
        cms = CountMinSketch(width=100, depth=4)
        cms.clear()
        assert cms.estimate("x") == 0


class TestCountMinSketchSerialization:
    def test_roundtrip_bytes(self) -> None:
        cms = CountMinSketch(width=200, depth=5, conservative=True)
        cms.add("alpha", count=10)
        cms.add("beta", count=5)
        raw = cms.to_bytes()
        restored = CountMinSketch.from_bytes(raw)
        assert restored.width == cms.width
        assert restored.depth == cms.depth
        assert restored.conservative == cms.conservative
        assert restored.estimate("alpha") == cms.estimate("alpha")
        assert restored.estimate("beta") == cms.estimate("beta")

    def test_from_bytes_truncated_raises(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            CountMinSketch.from_bytes(b"\x00\x00")


class TestCountMinSketchFromEpsilonDelta:
    def test_from_epsilon_delta_common_parameters(self) -> None:
        cms = CountMinSketch.from_epsilon_delta(epsilon=0.01, delta=0.01)
        expected_width = math.ceil(math.e / 0.01)
        expected_depth = math.ceil(math.log(1.0 / 0.01))
        assert cms.width == expected_width
        assert cms.depth == expected_depth

    def test_from_epsilon_delta_invalid_epsilon(self) -> None:
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.0, delta=0.01)

    def test_from_epsilon_delta_invalid_delta(self) -> None:
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.01, delta=0.0)


class TestCountMinSketchEdgeCases:
    def test_invalid_width_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be >= 1"):
            CountMinSketch(width=0, depth=3)

    def test_invalid_depth_raises(self) -> None:
        with pytest.raises(ValueError, match="depth must be >= 1"):
            CountMinSketch(width=100, depth=0)

    def test_properties(self) -> None:
        cms = CountMinSketch(width=256, depth=7, conservative=True)
        assert cms.width == 256
        assert cms.depth == 7
        assert cms.conservative is True

    def test_non_member_estimate_zero(self) -> None:
        cms = CountMinSketch(width=5000, depth=8)
        for i in range(100):
            cms.add(f"present_{i}")
        assert cms.estimate("definitely_never_added") == 0

    def test_string_bytes_int_float_types(self) -> None:
        cms = CountMinSketch(width=200, depth=4)
        for val in ["str", b"bytes", 42, 3.14]:
            cms.add(val)
            assert cms.estimate(val) >= 1

    def test_single_width_single_depth(self) -> None:
        cms = CountMinSketch(width=1, depth=1)
        cms.add("only_slot", count=5)
        assert cms.estimate("only_slot") >= 5

    def test_large_scale_frequency_estimation(self) -> None:
        cms = CountMinSketch(width=10000, depth=6)
        n = 10000
        for _ in range(n):
            cms.add("heavy")
        est = cms.estimate("heavy")
        error = abs(est - n) / n
        assert error < 0.05

    def test_many_distinct_items(self) -> None:
        cms = CountMinSketch(width=2000, depth=5)
        m = 5000
        for i in range(m):
            cms.add(f"distinct_{i}")
        for i in range(m):
            assert cms.estimate(f"distinct_{i}") >= 1

    def test_serialize_deserialize_directory(self) -> None:
        cms = CountMinSketch(width=100, depth=4)
        for i in range(50):
            cms.add(f"item_{i}")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cms.bin")
            with open(path, "wb") as f:
                f.write(cms.to_bytes())
            with open(path, "rb") as f:
                restored = CountMinSketch.from_bytes(f.read())
        for i in range(50):
            assert restored.estimate(f"item_{i}") == cms.estimate(f"item_{i}")
