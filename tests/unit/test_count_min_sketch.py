"""Unit tests for CountMinSketch probabilistic frequency estimator."""

import struct

import pytest

from general_ludd.probabilistic.count_min_sketch import CountMinSketch


class TestCountMinSketchInit:
    def test_basic_construction(self):
        cms = CountMinSketch(width=100, depth=5)
        assert cms.width == 100
        assert cms.depth == 5
        assert cms.conservative is False
        assert cms._counters is not None
        assert len(cms._counters) == 5
        assert len(cms._counters[0]) == 100
        assert all(c == 0 for row in cms._counters for c in row)

    def test_conservative_construction(self):
        cms = CountMinSketch(width=50, depth=3, conservative=True)
        assert cms.conservative is True

    def test_width_must_be_positive(self):
        with pytest.raises(ValueError, match="width must be >= 1"):
            CountMinSketch(width=0, depth=5)
        with pytest.raises(ValueError, match="width must be >= 1"):
            CountMinSketch(width=-1, depth=5)

    def test_depth_must_be_positive(self):
        with pytest.raises(ValueError, match="depth must be >= 1"):
            CountMinSketch(width=100, depth=0)
        with pytest.raises(ValueError, match="depth must be >= 1"):
            CountMinSketch(width=100, depth=-3)


class TestFromEpsilonDelta:
    def test_reasonable_params(self):
        cms = CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=0.01)
        assert cms.width > 1
        assert cms.depth > 1

    def test_epsilon_out_of_range(self):
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.0, delta=0.1)
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=1.0, delta=0.1)
        with pytest.raises(ValueError, match="epsilon must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=-0.1, delta=0.1)

    def test_delta_out_of_range(self):
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=0.0)
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=1.0)
        with pytest.raises(ValueError, match="delta must be in"):
            CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=-0.01)

    def test_width_grows_as_epsilon_shrinks(self):
        cms1 = CountMinSketch.from_epsilon_delta(epsilon=0.01, delta=0.01)
        cms2 = CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=0.01)
        assert cms1.width > cms2.width

    def test_depth_grows_with_confidence(self):
        cms1 = CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=0.0001)
        cms2 = CountMinSketch.from_epsilon_delta(epsilon=0.1, delta=0.01)
        assert cms1.depth > cms2.depth


class TestAddAndEstimate:
    def test_add_string_item(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add("apple")
        cms.add("apple")
        cms.add("banana")
        assert cms.estimate("apple") == 2
        assert cms.estimate("banana") == 1
        assert cms.estimate("cherry") == 0

    def test_add_integer_item(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add(42, count=5)
        assert cms.estimate(42) == 5

    def test_add_bytes_item(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add(b"\x00\x01\x02", count=3)
        assert cms.estimate(b"\x00\x01\x02") == 3

    def test_add_float_item(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add(3.14, count=2)
        assert cms.estimate(3.14) == 2

    def test_add_with_default_count(self):
        cms = CountMinSketch(width=100, depth=3)
        cms.add("x")
        assert cms.estimate("x") == 1

    def test_add_count_must_be_positive(self):
        cms = CountMinSketch(width=100, depth=3)
        with pytest.raises(ValueError, match="count must be >= 1"):
            cms.add("x", count=0)
        with pytest.raises(ValueError, match="count must be >= 1"):
            cms.add("x", count=-1)

    def test_estimate_never_negative(self):
        cms = CountMinSketch(width=100, depth=3)
        cms.add("hot", count=100)
        assert cms.estimate("cold") >= 0

    def test_estimate_is_upper_bound(self):
        cms = CountMinSketch(width=100, depth=3)
        cms.add("x", count=10)
        assert cms.estimate("x") >= 10

    def test_conservative_update(self):
        cms = CountMinSketch(width=200, depth=4, conservative=True)
        for _ in range(5):
            cms.add("cat")
        assert cms.estimate("cat") == 5

    def test_conservative_vs_regular(self):
        cms_cons = CountMinSketch(width=50, depth=3, conservative=True)
        cms_reg = CountMinSketch(width=50, depth=3, conservative=False)
        for _ in range(20):
            cms_cons.add("a")
            cms_reg.add("a")
        assert cms_cons.estimate("a") <= cms_reg.estimate("a")


class TestHeavyHitters:
    def test_hits_above_threshold(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add("hot", count=10)
        cms.add("warm", count=5)
        cms.add("cold", count=2)
        results = cms.heavy_hitters(threshold=5, candidates={"hot", "warm", "cold"})
        assert len(results) == 2
        assert results[0][0] == "hot"
        assert results[1][0] == "warm"

    def test_no_candidates_returns_empty(self):
        cms = CountMinSketch(width=100, depth=3)
        cms.add("x", count=100)
        assert cms.heavy_hitters(threshold=1) == []
        assert cms.heavy_hitters(threshold=1, candidates=None) == []

    def test_empty_candidates(self):
        cms = CountMinSketch(width=100, depth=3)
        assert cms.heavy_hitters(threshold=5, candidates=set()) == []

    def test_threshold_must_be_positive(self):
        cms = CountMinSketch(width=100, depth=3)
        with pytest.raises(ValueError, match="threshold must be >= 1"):
            cms.heavy_hitters(threshold=0, candidates={"x"})

    def test_results_sorted_descending(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add("a", count=3)
        cms.add("b", count=7)
        cms.add("c", count=5)
        results = cms.heavy_hitters(threshold=3, candidates={"a", "b", "c"})
        assert results == [("b", 7), ("c", 5), ("a", 3)]


class TestMerge:
    def test_merge_adds_counts(self):
        cms1 = CountMinSketch(width=100, depth=3)
        cms1.add("shared", count=2)
        cms1.add("only1", count=1)

        cms2 = CountMinSketch(width=100, depth=3)
        cms2.add("shared", count=3)
        cms2.add("only2", count=1)

        cms1.merge(cms2)
        assert cms1.estimate("shared") >= 5
        assert cms1.estimate("only1") == 1
        assert cms1.estimate("only2") >= 1

    def test_merge_dimension_mismatch(self):
        cms1 = CountMinSketch(width=100, depth=3)
        cms2 = CountMinSketch(width=200, depth=3)
        with pytest.raises(ValueError, match="cannot merge count-min sketches with different dimensions"):
            cms1.merge(cms2)

        cms3 = CountMinSketch(width=100, depth=5)
        with pytest.raises(ValueError, match="cannot merge count-min sketches with different dimensions"):
            cms1.merge(cms3)


class TestClear:
    def test_clear_resets_all_counters(self):
        cms = CountMinSketch(width=100, depth=3)
        cms.add("a", count=5)
        cms.add("b", count=10)
        cms.clear()
        assert cms.estimate("a") == 0
        assert cms.estimate("b") == 0
        assert all(c == 0 for row in cms._counters for c in row)


class TestSerialization:
    def test_roundtrip_empty(self):
        cms = CountMinSketch(width=100, depth=3)
        raw = cms.to_bytes()
        cms2 = CountMinSketch.from_bytes(raw)
        assert cms2.width == 100
        assert cms2.depth == 3
        assert cms2.conservative is False
        assert all(c == 0 for row in cms2._counters for c in row)

    def test_roundtrip_with_data(self):
        cms = CountMinSketch(width=200, depth=4)
        cms.add("hello", count=7)
        cms.add("world", count=3)
        raw = cms.to_bytes()
        cms2 = CountMinSketch.from_bytes(raw)
        assert cms2.width == 200
        assert cms2.depth == 4
        assert cms2.estimate("hello") == 7
        assert cms2.estimate("world") == 3

    def test_roundtrip_conservative(self):
        cms = CountMinSketch(width=50, depth=3, conservative=True)
        cms.add("x", count=2)
        raw = cms.to_bytes()
        cms2 = CountMinSketch.from_bytes(raw)
        assert cms2.conservative is True
        assert cms2.estimate("x") == 2

    def test_from_bytes_truncated_header(self):
        with pytest.raises(ValueError, match="truncated"):
            CountMinSketch.from_bytes(b"\x00\x00")

    def test_from_bytes_body_length_mismatch(self):
        header = struct.pack("!II?", 100, 3, False)
        bad_body = b"\x00" * 10
        with pytest.raises(ValueError, match="body length mismatch"):
            CountMinSketch.from_bytes(header + bad_body)

    def test_to_bytes_deterministic(self):
        cms = CountMinSketch(width=100, depth=3)
        raw1 = cms.to_bytes()
        raw2 = cms.to_bytes()
        assert raw1 == raw2


class TestItemToBytes:
    def test_string(self):
        assert CountMinSketch._item_to_bytes("abc") == b"abc"

    def test_bytes_passthrough(self):
        assert CountMinSketch._item_to_bytes(b"abc") == b"abc"

    def test_integer(self):
        assert CountMinSketch._item_to_bytes(123) == b"123"

    def test_float(self):
        result = CountMinSketch._item_to_bytes(3.5)
        assert b"3.5" in result

    def test_custom_object(self):
        result = CountMinSketch._item_to_bytes(object())
        assert isinstance(result, bytes)
        assert len(result) > 0


class TestHashFunctions:
    def test_hash_is_deterministic(self):
        h1 = CountMinSketch._hash(b"test", 2)
        h2 = CountMinSketch._hash(b"test", 2)
        assert h1 == h2

    def test_hash_varying_seed(self):
        h1 = CountMinSketch._hash(b"test", 0)
        h2 = CountMinSketch._hash(b"test", 1)
        assert h1 != h2

    def test_hash_varying_key(self):
        h1 = CountMinSketch._hash(b"abc", 0)
        h2 = CountMinSketch._hash(b"def", 0)
        assert h1 != h2

    def test_hash_is_non_negative(self):
        for seed in range(10):
            h = CountMinSketch._hash(b"test", seed)
            assert 0 <= h <= 0x7FFFFFFF

    def test_fnv1a_known_vector(self):
        assert CountMinSketch._fnv1a(b"") == 0x811C9DC5
