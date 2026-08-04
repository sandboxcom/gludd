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
        assert a.tokens == 6.0


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
        assert lim.allow(3.0) is True
        assert lim.allow(3.0) is False

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
            _advance(store, 0.5)
        assert 14 <= allowed <= 24

    def test_burst_then_refill(self) -> None:
        clock, store = _fake_clock(0)
        cfg = BucketConfig(capacity=10.0, rate=1.0, burst_multiplier=2.0)
        b = Bucket("a", cfg, clock=clock)
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
        assert b.tokens == 5.0

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
        lim.register("ip", BucketConfig(capacity=5.0, rate=1.0))
        lim.create_group("all", ["api", "user", "ip"], overflow=False)
        assert lim.allow(3.0, group="all") is True
        assert lim.allow(3.0, group="all") is True
        assert lim.allow(3.0, group="all") is False
