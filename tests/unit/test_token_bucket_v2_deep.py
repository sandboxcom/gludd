"""Deep tests for token_bucket_v2: multi-bucket, hierarchical groups,
burst allowance, smooth refill, overflow gating, AND gating, concurrency
safety, snapshot, reset, and edge cases.

≥15 test methods across multiple classes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from general_ludd.network.token_bucket_v2 import (
    Bucket,
    BucketConfig,
    BucketGroup,
    BucketState,
    LimiterV2,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_MOCK_EPOCH = 1000.0


def _fake_clock(inc: float = 0.0) -> tuple[Callable[[], float], list[float]]:
    """Return (clock_fn, store) where store[0] is the current mock time."""
    store: list[float] = [_MOCK_EPOCH + inc]

    def clock() -> float:
        return store[0]

    return clock, store


def _advance(store: list[float], delta: float) -> None:
    store[0] += delta


# ── BucketConfig ─────────────────────────────────────────────────────────────


class TestBucketConfig:
    def test_defaults(self) -> None:
        cfg = BucketConfig(capacity=10.0, rate=2.0)
        assert cfg.capacity == 10.0
        assert cfg.rate == 2.0
        assert cfg.burst_multiplier == 1.0
        assert cfg.parent is None
        assert cfg.metadata == {}

    def test_burst_multiplier(self) -> None:
        cfg = BucketConfig(capacity=5.0, rate=1.0, burst_multiplier=3.0)
        assert cfg.capacity == 5.0
        assert cfg.burst_multiplier == 3.0

    def test_metadata_passthrough(self) -> None:
        cfg = BucketConfig(capacity=1.0, rate=0.5, metadata={"layer": "api"})
        assert cfg.metadata == {"layer": "api"}

    def test_capacity_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity must be > 0"):
            BucketConfig(capacity=0.0, rate=1.0)

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="rate must be >= 0"):
            BucketConfig(capacity=10.0, rate=-1.0)

    def test_burst_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"burst_multiplier must be >= 1.0"):
            BucketConfig(capacity=5.0, rate=1.0, burst_multiplier=0.5)


# ── Bucket (single) ──────────────────────────────────────────────────────────


class TestBucketConstruction:
    def test_starts_full(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        assert b.tokens == 10.0
        assert b.capacity == 10.0
        assert b.rate == 2.0
        assert b.name == "a"

    def test_starts_with_custom_state(self) -> None:
        state = BucketState(tokens=3.0, last_refill=_MOCK_EPOCH)
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), state=state, clock=clock)
        assert b.tokens == 3.0


class TestBucketRefill:
    def test_refill_no_elapsed_no_change(self) -> None:
        clock, _store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(5.0, auto_refill=False)
        assert b.tokens == 5.0
        b.refill()
        assert b.tokens == 5.0

    def test_refill_with_elapsed(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(10.0, auto_refill=False)
        assert b.tokens == 0.0
        _advance(store, 3.0)
        b.refill()
        assert b.tokens == 6.0

    def test_refill_respects_burst_cap(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=5.0, rate=2.0, burst_multiplier=2.0)
        b = Bucket("a", cfg, clock=clock)
        b.consume(5.0, auto_refill=False)
        _advance(store, 10.0)
        b.refill()
        assert b.tokens == 10.0


class TestBucketConsume:
    def test_consume_sufficient(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        assert b.consume(3.0) is True
        assert b.tokens == 2.0

    def test_consume_insufficient(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=2.0, rate=1.0), clock=clock)
        assert b.consume(5.0) is False
        assert b.tokens == 2.0

    def test_consume_exact(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=4.0, rate=1.0), clock=clock)
        assert b.consume(4.0) is True
        assert b.tokens == 0.0

    def test_consume_negative_raises(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        with pytest.raises(ValueError, match="tokens must be >= 0"):
            b.consume(-1.0)

    def test_auto_refill_on_consume(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(10.0)
        assert b.tokens == 0.0
        _advance(store, 2.5)
        assert b.consume(5.0) is True
        assert b.tokens == 0.0

    def test_consume_no_auto_refill(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(10.0, auto_refill=False)
        _advance(store, 5.0)
        assert b.consume(1.0, auto_refill=False) is False


class TestBucketReset:
    def test_reset_restores_full(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(7.0, auto_refill=False)
        assert b.tokens == 3.0
        b.reset()
        assert b.tokens == 10.0

    def test_reset_updates_timestamp(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        old_ts = b.last_refill
        _advance(store, 5.0)
        b.reset()
        assert b.last_refill > old_ts


class TestBucketSnapshot:
    def test_snapshot_independent(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        s = b.snapshot()
        assert s.tokens == 10.0
        b.consume(5.0, auto_refill=False)
        assert s.tokens == 10.0

    def test_snapshot_after_consume(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(3.0, auto_refill=False)
        s = b.snapshot()
        assert s.tokens == 7.0


class TestBucketConcurrency:
    def test_parallel_consumes_sum(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=100.0, rate=0.0), clock=clock)
        results: list[bool] = []

        def worker(n: float) -> None:
            results.append(b.consume(n, auto_refill=False))

        threads = [threading.Thread(target=worker, args=(1.0,)) for _ in range(80)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 80
        assert b.tokens == 20.0


# ── BucketGroup ──────────────────────────────────────────────────────────────


class TestBucketGroupOverflow:
    def test_overflow_first_bucket_sufficient(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(5.0, auto_refill=False) is True
        assert a.tokens == 5.0
        assert b.tokens == 5.0

    def test_overflow_falls_through(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=3.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(5.0, auto_refill=False) is True
        assert a.tokens == 3.0
        assert b.tokens == 5.0

    def test_overflow_all_insufficient(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=2.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(3.0, auto_refill=False) is False
        assert a.tokens == 2.0
        assert b.tokens == 1.0

    def test_overflow_refills_before_check(self) -> None:
        clock_a, store_a = _fake_clock(0)
        clock_b, store_b = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=3.0, rate=2.0), clock=clock_a)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=1.0), clock=clock_b)
        a.consume(3.0, auto_refill=False)
        b.consume(5.0, auto_refill=False)
        _advance(store_a, 5.0)
        _advance(store_b, 5.0)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(4.0, auto_refill=True) is True
        assert a.tokens == 3.0
        assert b.tokens == 1.0


class TestBucketGroupAndGate:
    def test_and_gate_all_sufficient(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=False)
        assert g.consume(5.0, auto_refill=False) is True
        assert a.tokens == 5.0
        assert b.tokens == 5.0

    def test_and_gate_one_insufficient_rolls_back(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=2.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=False)
        assert g.consume(5.0, auto_refill=False) is False
        assert a.tokens == 10.0
        assert b.tokens == 2.0

    def test_and_gate_three_buckets(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        c = Bucket("c", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b, c], overflow=False)
        assert g.consume(3.0, auto_refill=False) is True
        assert a.tokens == 2.0
        assert b.tokens == 2.0
        assert c.tokens == 2.0


class TestBucketGroupRefillReset:
    def test_refill_all(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        a.consume(10.0, auto_refill=False)
        b.consume(5.0, auto_refill=False)
        _advance(store, 5.0)
        g = BucketGroup("g", [a, b])
        g.refill_all()
        assert a.tokens == 10.0
        assert b.tokens == 5.0

    def test_reset_all(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        a.consume(5.0, auto_refill=False)
        b.consume(3.0, auto_refill=False)
        g = BucketGroup("g", [a, b])
        g.reset_all()
        assert a.tokens == 10.0
        assert b.tokens == 5.0


# ── LimiterV2 (multi-bucket coordinator) ─────────────────────────────────────


class TestLimiterV2Registration:
    def test_register_bucket(self) -> None:
        lim = LimiterV2()
        b = lim.register("user", BucketConfig(capacity=10.0, rate=2.0))
        assert b.name == "user"
        assert "user" in lim.buckets

    def test_duplicate_raises(self) -> None:
        lim = LimiterV2()
        lim.register("x", BucketConfig(capacity=1.0, rate=1.0))
        with pytest.raises(KeyError, match="bucket 'x' already registered"):
            lim.register("x", BucketConfig(capacity=1.0, rate=1.0))

    def test_create_group(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=5.0, rate=1.0))
        lim.register("b", BucketConfig(capacity=5.0, rate=1.0))
        g = lim.create_group("g1", ["a", "b"], overflow=True)
        assert g.name == "g1"
        assert len(g) == 2
        assert g.overflow is True


class TestLimiterV2Allow:
    def test_allow_no_group_all_buckets_and(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=5.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=5.0, rate=0.0))
        assert lim.allow(3.0) is True
        assert lim.allow(3.0) is False
        assert lim.buckets["a"].tokens == 2.0
        assert lim.buckets["b"].tokens == 2.0

    def test_allow_no_group_rolls_back_when_later_bucket_denies(self) -> None:
        """Ungrouped AND gating never partially charges an earlier bucket."""
        lim = LimiterV2()
        lim.register("first", BucketConfig(capacity=10.0, rate=0.0))
        lim.register("second", BucketConfig(capacity=2.0, rate=0.0))

        assert lim.allow(3.0) is False
        assert lim.buckets["first"].tokens == 10.0
        assert lim.buckets["second"].tokens == 2.0

    def test_allow_with_group_overflow(self) -> None:
        lim = LimiterV2()
        lim.register("pri", BucketConfig(capacity=3.0, rate=0.0))
        lim.register("sec", BucketConfig(capacity=10.0, rate=0.0))
        lim.create_group("tiered", ["pri", "sec"], overflow=True)
        assert lim.allow(5.0, group="tiered") is True
        assert lim.buckets["pri"].tokens == 3.0
        assert lim.buckets["sec"].tokens == 5.0

    def test_allow_with_group_and(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=5.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=2.0, rate=0.0))
        lim.create_group("g", ["a", "b"], overflow=False)
        assert lim.allow(3.0, group="g") is False
        assert lim.allow(2.0, group="g") is True


class TestLimiterV2DefaultGroup:
    def test_default_group_fallback(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=10.0, rate=0.0))
        lim.create_group("g", ["a"], overflow=False)
        lim.default_group = "g"
        assert lim.allow(3.0) is True

    def test_explicit_group_overrides_default(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=3.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=10.0, rate=0.0))
        lim.create_group("ga", ["a"], overflow=False)
        lim.create_group("gb", ["b"], overflow=False)
        lim.default_group = "ga"
        assert lim.allow(5.0, group="gb") is True


class TestLimiterV2Ops:
    def test_snapshot(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=5.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=10.0, rate=0.0))
        lim.buckets["a"].consume(2.0, auto_refill=False)
        snap = lim.snapshot()
        assert snap["a"].tokens == 3.0
        assert snap["b"].tokens == 10.0

    def test_reset_all(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=5.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=10.0, rate=0.0))
        lim.buckets["a"].consume(5.0, auto_refill=False)
        lim.buckets["b"].consume(7.0, auto_refill=False)
        lim.reset_all()
        assert lim.buckets["a"].tokens == 5.0
        assert lim.buckets["b"].tokens == 10.0

    def test_refill_all(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        lim = LimiterV2(buckets={"a": a, "b": b})
        a.consume(5.0, auto_refill=False)
        b.consume(10.0, auto_refill=False)
        _advance(store, 3.0)
        lim.refill_all()
        assert a.tokens == 3.0
        assert b.tokens == 6.0


# ── integration: smooth refill over many steps ───────────────────────────────


class TestSmoothRefillIntegration:
    def test_rate_limited_over_time(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        allowed = 0
        for _ in range(100):
            if b.consume(1.0):
                allowed += 1
            _advance(store, 0.05)
        assert 19 <= allowed <= 20

    def test_burst_then_refill(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=10.0, rate=1.0, burst_multiplier=2.0)
        b = Bucket("a", cfg, clock=clock)
        _advance(store, 10.0)
        b.refill()
        burst_allowed = 0
        for _ in range(30):
            if b.consume(1.0):
                burst_allowed += 1
        assert burst_allowed == 20
        b.reset()
        _advance(store, 5.0)
        assert b.consume(5.0) is True


class TestEdgeCases:
    def test_zero_rate_bucket(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        assert b.consume(5.0) is True
        assert b.consume(1.0) is False
        _advance(store, 100.0)
        b.refill()
        assert b.tokens == 0.0

    def test_empty_group(self) -> None:
        g = BucketGroup("g", [])
        assert len(g) == 0
        assert g.consume(1.0) is True

    def test_group_add_dynamic(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=5.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [])
        g.add(a)
        assert g.consume(2.0, auto_refill=False) is True

    def test_try_consume_alias(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=3.0, rate=0.0), clock=clock)
        assert b.try_consume(3.0, auto_refill=False) is True
        assert b.try_consume(1.0, auto_refill=False) is False

    def test_snapshot_deep_independence(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        s = b.snapshot()
        s.tokens = 999.0
        assert b.tokens == 10.0

    def test_group_len_and_iter(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b])
        assert len(g) == 2
        names = [bkt.name for bkt in g]
        assert names == ["a", "b"]

    def test_hierarchical_three_tier(self) -> None:
        lim = LimiterV2()
        lim.register("api", BucketConfig(capacity=100.0, rate=10.0))
        lim.register("user", BucketConfig(capacity=20.0, rate=2.0))
        lim.register("ip", BucketConfig(capacity=6.0, rate=1.0))
        lim.create_group("all", ["api", "user", "ip"], overflow=False)
        assert lim.allow(3.0, group="all") is True
        assert lim.allow(3.0, group="all") is True
        assert lim.allow(3.0, group="all") is False


# ── deep edge cases ───────────────────────────────────────────────────────────


class TestBucketConfigDeepEdge:
    def test_negative_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity must be > 0"):
            BucketConfig(capacity=-5.0, rate=1.0)

    def test_rate_exactly_zero_valid(self) -> None:
        cfg = BucketConfig(capacity=10.0, rate=0.0)
        assert cfg.rate == 0.0

    def test_burst_exactly_one_valid(self) -> None:
        cfg = BucketConfig(capacity=10.0, rate=2.0, burst_multiplier=1.0)
        assert cfg.burst_multiplier == 1.0

    def test_very_large_values(self) -> None:
        cfg = BucketConfig(capacity=1_000_000.0, rate=500_000.0, burst_multiplier=100.0)
        b = Bucket("x", cfg)
        assert b.effective_capacity == 100_000_000.0


class TestBucketStateDeepEdge:
    def test_deepcopy_independent(self) -> None:
        import copy

        s1 = BucketState(tokens=7.0, last_refill=1234.5)
        s2 = copy.deepcopy(s1)
        assert s2.tokens == 7.0
        assert s2.last_refill == 1234.5
        s2.tokens = 99.0
        assert s1.tokens == 7.0

    def test_two_states_different_timestamps(self) -> None:
        s1 = BucketState(tokens=1.0)
        s2 = BucketState(tokens=2.0)
        assert s1.last_refill <= s2.last_refill


class TestBucketDeepEdge:
    def test_clock_goes_backwards_refill_noop(self) -> None:
        clock, store = _fake_clock(10.0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=2.0), clock=clock)
        b.consume(5.0, auto_refill=False)
        store[0] = 5.0
        b.refill()
        assert b.tokens == 5.0

    def test_consume_zero_tokens(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        assert b.consume(0.0) is True
        assert b.tokens == 5.0

    def test_consume_zero_does_refill(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=10.0, rate=2.0, burst_multiplier=3.0)
        b = Bucket("a", cfg, clock=clock)
        b.consume(10.0, auto_refill=False)
        _advance(store, 100.0)
        b.consume(0.0, auto_refill=True)
        assert b.tokens == 30.0

    def test_consume_exact_effective_capacity(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=5.0, rate=1.0, burst_multiplier=3.0)
        b = Bucket("a", cfg, clock=clock)
        _advance(store, 100.0)
        b.refill()
        assert b.tokens == 15.0
        assert b.consume(15.0, auto_refill=False) is True
        assert b.tokens == 0.0

    def test_refill_very_large_elapsed(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=10.0, rate=1.0, burst_multiplier=5.0)
        b = Bucket("a", cfg, clock=clock)
        b.consume(10.0, auto_refill=False)
        _advance(store, 999_999.0)
        b.refill()
        assert b.tokens == 50.0

    def test_float_precision_very_small_tokens(self) -> None:
        clock, _s = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=1e-9, rate=0.0), clock=clock)
        assert b.consume(5e-10, auto_refill=False) is True
        assert b.consume(5e-10, auto_refill=False) is True
        assert b.consume(1e-10, auto_refill=False) is False

    def test_float_precision_very_small_rate(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=1.0, rate=1e-9), clock=clock)
        b.consume(1.0, auto_refill=False)
        _advance(store, 1e8)
        b.refill()
        assert b.tokens == pytest.approx(0.1, rel=1e-6)

    def test_concurrent_consume_with_refill(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=100.0), clock=clock)
        b.consume(10.0, auto_refill=False)
        _advance(store, 1.0)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                b.consume(1.0, auto_refill=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert b.tokens >= 0.0

    def test_reset_preserves_effective_capacity(self) -> None:
        clock, _s = _fake_clock(0)
        cfg = BucketConfig(capacity=5.0, rate=1.0, burst_multiplier=2.0)
        b = Bucket("a", cfg, clock=clock)
        b.consume(10.0, auto_refill=False)
        b.reset()
        assert b.tokens == 5.0
        assert b.effective_capacity == 10.0

    def test_last_refill_monotonic(self) -> None:
        clock, store = _fake_clock(0)
        b = Bucket("a", BucketConfig(capacity=10.0, rate=1.0), clock=clock)
        ts1 = b.last_refill
        _advance(store, 1.0)
        b.refill()
        assert b.last_refill > ts1


class TestBucketGroupDeepEdge:
    def test_overflow_exact_boundary_refill(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=10.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=10.0, rate=10.0), clock=clock)
        a.consume(10.0, auto_refill=False)
        b.consume(10.0, auto_refill=False)
        _advance(store, 0.5)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(5.0, auto_refill=True) is True
        assert a.tokens == 0.0

    def test_overflow_auto_refill_false_skips(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=0.1, rate=10.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=100.0, rate=0.0), clock=clock)
        a.consume(0.1, auto_refill=False)
        g = BucketGroup("g", [a, b], overflow=True)
        _advance(store, 1.0)
        assert g.consume(5.0, auto_refill=False) is True
        assert a.tokens == 0.0
        assert b.tokens == 95.0

    def test_overflow_all_empty_no_refill_fallback(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=0.01, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=0.01, rate=0.0), clock=clock)
        a.consume(0.01, auto_refill=False)
        b.consume(0.01, auto_refill=False)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(1.0, auto_refill=False) is False

    def test_and_gate_rollback_restores_refill_timestamp(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=1.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=2.0, rate=1.0), clock=clock)
        ts_a_before = a.last_refill
        ts_b_before = b.last_refill
        _advance(store, 5.0)
        g = BucketGroup("g", [a, b], overflow=False)
        assert g.consume(5.0, auto_refill=True) is False
        assert a.tokens == 10.0
        assert b.tokens == 2.0
        assert a.last_refill == ts_a_before
        assert b.last_refill == ts_b_before

    def test_and_gate_auto_refill_false_no_refill(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=3.0, rate=10.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=3.0, rate=10.0), clock=clock)
        a.consume(3.0, auto_refill=False)
        b.consume(3.0, auto_refill=False)
        _advance(store, 1.0)
        g = BucketGroup("g", [a, b], overflow=False)
        assert g.consume(1.0, auto_refill=False) is False
        assert a.tokens == 0.0
        assert b.tokens == 0.0

    def test_empty_group_overflow(self) -> None:
        g = BucketGroup("g", [], overflow=True)
        assert g.consume(1.0) is False

    def test_getitem_negative_index(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=2.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b])
        assert g[-1].name == "b"
        assert g[-2].name == "a"

    def test_getitem_out_of_range_raises(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a])
        with pytest.raises(IndexError):
            g[5]

    def test_and_gate_three_buckets_middle_insufficient(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=1.0, rate=0.0), clock=clock)
        c = Bucket("c", BucketConfig(capacity=10.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b, c], overflow=False)
        assert g.consume(5.0, auto_refill=False) is False
        assert a.tokens == 10.0
        assert b.tokens == 1.0
        assert c.tokens == 10.0

    def test_overflow_single_bucket_sufficient_no_fallthrough(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=100.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=100.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(10.0, auto_refill=False) is True
        assert a.tokens == 90.0
        assert b.tokens == 100.0


class TestLimiterV2DeepEdge:
    def test_allow_non_existent_group_raises(self) -> None:
        lim = LimiterV2()
        with pytest.raises(KeyError):
            lim.allow(1.0, group="nonexistent")

    def test_create_group_missing_bucket_raises(self) -> None:
        lim = LimiterV2()
        with pytest.raises(KeyError):
            lim.create_group("g", ["no_such_bucket"])

    def test_allow_no_buckets_no_default(self) -> None:
        lim = LimiterV2()
        assert lim.allow(1.0) is True

    def test_allow_no_buckets_with_nonexistent_default(self) -> None:
        lim = LimiterV2()
        lim.default_group = "ghost"
        with pytest.raises(KeyError):
            lim.allow(1.0)

    def test_allow_with_group_and_auto_refill_false(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=3.0, rate=10.0), clock=clock)
        lim = LimiterV2(buckets={"a": a})
        a.consume(3.0, auto_refill=False)
        _advance(store, 1.0)
        lim.create_group("g", ["a"], overflow=False)
        assert lim.allow(1.0, group="g", auto_refill=False) is False

    def test_snapshot_after_consume_independence(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=10.0, rate=0.0))
        lim.allow(3.0)
        snap = lim.snapshot()
        assert snap["a"].tokens == 7.0
        lim.allow(2.0)
        assert snap["a"].tokens == 7.0

    def test_refill_all_different_rates(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=5.0, rate=1.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=5.0, rate=3.0), clock=clock)
        lim = LimiterV2(buckets={"a": a, "b": b})
        a.consume(5.0, auto_refill=False)
        b.consume(5.0, auto_refill=False)
        _advance(store, 2.0)
        lim.refill_all()
        assert a.tokens == 2.0
        assert b.tokens == 5.0

    def test_reset_all_preserves_config(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=7.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=3.0, rate=0.0))
        lim.buckets["a"].consume(7.0, auto_refill=False)
        lim.buckets["b"].consume(2.0, auto_refill=False)
        lim.reset_all()
        assert lim.buckets["a"].tokens == 7.0
        assert lim.buckets["b"].tokens == 3.0

    def test_allow_default_group_implicit_and_gate(self) -> None:
        lim = LimiterV2()
        lim.register("a", BucketConfig(capacity=10.0, rate=0.0))
        lim.register("b", BucketConfig(capacity=4.0, rate=0.0))
        lim.create_group("g", ["a", "b"], overflow=False)
        lim.default_group = "g"
        assert lim.allow(5.0) is False
        assert lim.allow(3.0) is True

    def test_allow_default_group_implicit_overflow(self) -> None:
        lim = LimiterV2()
        lim.register("pri", BucketConfig(capacity=2.0, rate=0.0))
        lim.register("sec", BucketConfig(capacity=10.0, rate=0.0))
        lim.create_group("tier", ["pri", "sec"], overflow=True)
        lim.default_group = "tier"
        assert lim.allow(5.0) is True
        assert lim.buckets["pri"].tokens == 2.0
        assert lim.buckets["sec"].tokens == 5.0


class TestOverflowRefillEdge:
    def test_first_bucket_refills_to_exact(self) -> None:
        clock, store = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=10.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=100.0, rate=0.0), clock=clock)
        a.consume(10.0, auto_refill=False)
        _advance(store, 0.5)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(5.0, auto_refill=True) is True
        assert a.tokens == 0.0
        assert b.tokens == 100.0

    def test_second_bucket_refills_after_first_empty(self) -> None:
        clock_a, _store_a = _fake_clock(0)
        clock_b, store_b = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=10.0, rate=0.0), clock=clock_a)
        b = Bucket("b", BucketConfig(capacity=10.0, rate=5.0), clock=clock_b)
        a.consume(10.0, auto_refill=False)
        b.consume(10.0, auto_refill=False)
        _advance(store_b, 1.0)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(3.0, auto_refill=True) is True
        assert a.tokens == 0.0
        assert b.tokens == 2.0

    def test_overflow_no_refill_seq_fail(self) -> None:
        clock, _s = _fake_clock(0)
        a = Bucket("a", BucketConfig(capacity=2.0, rate=0.0), clock=clock)
        b = Bucket("b", BucketConfig(capacity=2.0, rate=0.0), clock=clock)
        g = BucketGroup("g", [a, b], overflow=True)
        assert g.consume(5.0, auto_refill=False) is False
        assert a.tokens == 2.0
        assert b.tokens == 2.0
