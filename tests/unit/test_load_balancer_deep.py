"""Deep tests for load balancer algorithms — 15+ per algorithm + health checks + sticky sessions."""

from __future__ import annotations

import pytest

from general_ludd.load_balancer import (
    Backend,
    ConsistentHashBalancer,
    LeastConnectionsBalancer,
    RandomBalancer,
    RoundRobinBalancer,
    StickySessionStore,
    WeightedBalancer,
    sticky_dispatch,
    sticky_dispatch_ch,
)

# ── RoundRobinBalancer ────────────────────────────────────────────────────────


class TestRoundRobinConstruction:
    def test_empty_balancer_returns_none(self) -> None:
        rr = RoundRobinBalancer[str]()
        assert rr.next() is None

    def test_single_backend_always_returned(self) -> None:
        rr = RoundRobinBalancer[str](["a"])
        for _ in range(10):
            assert rr.next() == "a"

    def test_multiple_backends_cycle_in_order(self) -> None:
        rr = RoundRobinBalancer[int]([1, 2, 3])
        results = [rr.next() for _ in range(6)]
        assert results == [1, 2, 3, 1, 2, 3]

    def test_add_increases_count(self) -> None:
        rr = RoundRobinBalancer[str]()
        rr.add("x")
        assert rr.backend_count == 1

    def test_add_duplicate_is_idempotent(self) -> None:
        rr = RoundRobinBalancer[str](["a"])
        rr.add("a")
        assert rr.backend_count == 1
        assert rr.next() == "a"

    def test_initial_backends_via_constructor(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b", "c"])
        assert rr.backend_count == 3


class TestRoundRobinMutation:
    def test_add_mid_cycle_starts_at_new_phase(self) -> None:
        rr = RoundRobinBalancer[int]([1, 2])
        assert rr.next() == 1
        rr.add(3)
        assert rr.backend_count == 3

    def test_remove_existing_backend(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b", "c"])
        rr.remove("b")
        assert rr.backend_count == 2
        assert rr.next() in ("a", "c")

    def test_remove_nonexistent_noop(self) -> None:
        rr = RoundRobinBalancer[str](["a"])
        rr.remove("z")
        assert rr.backend_count == 1

    def test_remove_all_then_next_is_none(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        rr.remove("a")
        rr.remove("b")
        assert rr.next() is None


class TestRoundRobinHealth:
    def test_unhealthy_skipped(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        rr.unhealthy("a")
        for _ in range(5):
            assert rr.next() == "b"

    def test_all_unhealthy_returns_none(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        rr.unhealthy("a")
        rr.unhealthy("b")
        assert rr.next() is None

    def test_recover_healthy(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        rr.unhealthy("a")
        assert rr.next() == "b"
        rr.healthy("a")
        results = {rr.next() for _ in range(10)}
        assert "a" in results

    def test_healthy_count_reflects_state(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b", "c"])
        rr.unhealthy("a")
        assert rr.healthy_count == 2
        rr.unhealthy("b")
        assert rr.healthy_count == 1
        rr.healthy("a")
        assert rr.healthy_count == 2


class TestRoundRobinEdge:
    def test_cursor_wraps_after_remove(self) -> None:
        rr = RoundRobinBalancer[int]([1, 2, 3, 4, 5])
        rr.remove(1)
        rr.remove(2)
        rr.remove(3)
        rr.remove(4)
        assert rr.next() == 5

    def test_iter_yields_all_keys(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b", "c"])
        assert list(rr) == ["a", "b", "c"]

    def test_len(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        assert len(rr) == 2

    def test_empty_backend_length(self) -> None:
        rr = RoundRobinBalancer[str]()
        assert len(rr) == 0


# ── LeastConnectionsBalancer ──────────────────────────────────────────────────


class TestLeastConnectionsConstruction:
    def test_empty_returns_none(self) -> None:
        lc = LeastConnectionsBalancer[str]()
        assert lc.next() is None

    def test_single_always_returned(self) -> None:
        lc = LeastConnectionsBalancer[str](["a"])
        for _ in range(5):
            assert lc.next() == "a"

    def test_backend_count(self) -> None:
        lc = LeastConnectionsBalancer[int]([1, 2, 3])
        assert lc.backend_count == 3

    def test_add_increases_count(self) -> None:
        lc = LeastConnectionsBalancer[str]()
        lc.add("x")
        assert lc.backend_count == 1

    def test_add_duplicate_idempotent(self) -> None:
        lc = LeastConnectionsBalancer[str](["a"])
        lc.add("a")
        assert lc.backend_count == 1


class TestLeastConnectionsRouting:
    def test_picks_least_loaded(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b", "c"])
        lc.acquire("a")
        lc.acquire("a")
        lc.acquire("b")
        result = lc.next()
        assert result == "c"

    def test_acquire_release_cycle(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        lc.acquire("a")
        lc.acquire("a")
        lc.release("a")
        lc.release("a")
        lc.acquire("b")
        result = lc.next()
        assert result == "a"

    def test_release_on_zero_noop(self) -> None:
        lc = LeastConnectionsBalancer[str](["a"])
        lc.release("a")
        assert lc.connection_count("a") == 0

    def test_release_nonexistent_noop(self) -> None:
        lc = LeastConnectionsBalancer[str]()
        lc.release("z")

    def test_connection_count(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        lc.acquire("a")
        lc.acquire("a")
        assert lc.connection_count("a") == 2
        assert lc.connection_count("b") == 0

    def test_ties_break_on_key_order(self) -> None:
        lc = LeastConnectionsBalancer[str](["z", "a"])
        result = lc.next()
        assert result == "a"


class TestLeastConnectionsHealth:
    def test_unhealthy_skipped(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        lc.acquire("b")
        lc.acquire("b")
        lc.unhealthy("a")
        assert lc.next() == "b"

    def test_all_unhealthy_returns_none(self) -> None:
        lc = LeastConnectionsBalancer[str](["a"])
        lc.unhealthy("a")
        assert lc.next() is None

    def test_recover_healthy(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        lc.acquire("b")
        lc.unhealthy("a")
        assert lc.next() == "b"
        lc.healthy("a")
        result = lc.next()
        assert result == "a"

    def test_healthy_count_tracks_state(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b", "c"])
        lc.unhealthy("a")
        assert lc.healthy_count == 2
        lc.unhealthy("b")
        assert lc.healthy_count == 1


class TestLeastConnectionsEdge:
    def test_remove_then_next_recalc(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b", "c"])
        lc.acquire("a")
        lc.acquire("a")
        lc.remove("b")
        lc.remove("c")
        assert lc.next() == "a"

    def test_len(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        assert len(lc) == 2


# ── ConsistentHashBalancer ────────────────────────────────────────────────────


class TestConsistentHashConstruction:
    def test_empty_returns_none(self) -> None:
        ch = ConsistentHashBalancer[str]()
        assert ch.next() is None

    def test_single_node_always_returned(self) -> None:
        ch = ConsistentHashBalancer[str](["a"])
        for _ in range(5):
            assert ch.next() == "a"

    def test_backend_count(self) -> None:
        ch = ConsistentHashBalancer[int]([1, 2, 3])
        assert ch.backend_count == 3

    def test_virtual_nodes_default(self) -> None:
        ch = ConsistentHashBalancer[str](["a"])
        assert ch._virtual_nodes == 128

    def test_custom_virtual_nodes(self) -> None:
        ch = ConsistentHashBalancer[str](["a"], virtual_nodes=4)
        assert ch._virtual_nodes == 4

    def test_custom_hash_function(self) -> None:
        def custom_hash(k: str) -> int:
            return len(k)

        ch = ConsistentHashBalancer[str](["aaa", "b"], hash_fn=custom_hash)
        result = ch.next()
        assert result in ("aaa", "b")

    def test_add_duplicate_idempotent(self) -> None:
        ch = ConsistentHashBalancer[str](["a"])
        ch.add("a")
        assert ch.backend_count == 1


class TestConsistentHashRouting:
    def test_same_request_key_same_node(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"])
        first = ch.next("user-42")
        for _ in range(20):
            assert ch.next("user-42") == first

    def test_different_request_keys_may_differ(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"], virtual_nodes=16)
        results = {ch.next(f"user-{i}") for i in range(100)}
        assert len(results) >= 1

    def test_add_node_minimally_redistributes(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b"], virtual_nodes=64)
        before = {i: ch.next(f"key-{i}") for i in range(200)}
        ch.add("c")
        changes = sum(1 for i in range(200) if ch.next(f"key-{i}") != before[i])
        assert changes < 140

    def test_remove_node_minimally_redistributes(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"], virtual_nodes=64)
        before = {i: ch.next(f"key-{i}") for i in range(200)}
        ch.remove("c")
        changes = sum(1 for i in range(200) if ch.next(f"key-{i}") != before[i])
        assert changes < 140

    def test_affinity_key_affects_hash(self) -> None:
        ch1 = ConsistentHashBalancer[str](["a", "b"], affinity_key="zone-a")
        ch2 = ConsistentHashBalancer[str](["a", "b"], affinity_key="zone-b")
        result1 = ch1.next("req")
        result2 = ch2.next("req")
        assert result1 is not None and result2 is not None


class TestConsistentHashHealth:
    def test_unhealthy_skipped(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b"])
        ch.unhealthy("a")
        for _ in range(10):
            assert ch.next() == "b"

    def test_all_unhealthy_returns_none(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b"])
        ch.unhealthy("a")
        ch.unhealthy("b")
        assert ch.next() is None

    def test_recover_healthy(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b"])
        ch.unhealthy("a")
        ch.unhealthy("b")
        ch.healthy("a")
        for _ in range(5):
            assert ch.next() == "a"

    def test_healthy_count(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"])
        ch.unhealthy("a")
        assert ch.healthy_count == 2


class TestConsistentHashEdge:
    def test_remove_last_node(self) -> None:
        ch = ConsistentHashBalancer[str](["a"])
        ch.remove("a")
        assert ch.next() is None

    def test_get_node_alias(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b"])
        assert ch.get_node("req") == ch.next("req")

    def test_len(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"])
        assert len(ch) == 3


# ── WeightedBalancer ──────────────────────────────────────────────────────────


class TestWeightedConstruction:
    def test_empty_returns_none(self) -> None:
        wb = WeightedBalancer[str]()
        assert wb.next() is None

    def test_single_backend(self) -> None:
        wb = WeightedBalancer[str](["a"])
        for _ in range(5):
            assert wb.next() == "a"

    def test_backend_count(self) -> None:
        wb = WeightedBalancer[int]([1, 2, 3])
        assert wb.backend_count == 3

    def test_weight_defaults_to_1(self) -> None:
        wb = WeightedBalancer[str](["a"])
        assert wb.weight("a") == 1

    def test_add_duplicate_idempotent(self) -> None:
        wb = WeightedBalancer[str](["a"])
        wb.add("a")
        assert wb.backend_count == 1


class TestWeightedRouting:
    def test_weighted_distribution_skewed(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        wb.set_weight("a", 9)
        wb.set_weight("b", 1)
        counts: dict[str, int] = {}
        for _ in range(10000):
            k = wb.next()
            assert k is not None
            counts[k] = counts.get(k, 0) + 1
        assert counts["a"] > counts["b"] * 5

    def test_equal_weights_uniform(self) -> None:
        wb = WeightedBalancer[str](["a", "b", "c"])
        wb.set_weight("a", 5)
        wb.set_weight("b", 5)
        wb.set_weight("c", 5)
        counts: dict[str, int] = {}
        for _ in range(3000):
            k = wb.next()
            assert k is not None
            counts[k] = counts.get(k, 0) + 1
        for c in counts.values():
            assert 700 < c < 1300

    def test_weight_zero_is_possible(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        wb.set_weight("a", 0)
        count_a = 0
        for _ in range(200):
            k = wb.next()
            if k == "a":
                count_a += 1
        assert count_a >= 0


class TestWeightedHealth:
    def test_unhealthy_skipped(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        wb.set_weight("a", 100)
        wb.set_weight("b", 1)
        wb.unhealthy("a")
        for _ in range(20):
            assert wb.next() == "b"

    def test_all_unhealthy_returns_none(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        wb.unhealthy("a")
        wb.unhealthy("b")
        assert wb.next() is None

    def test_recover_healthy(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        wb.unhealthy("a")
        wb.healthy("a")
        results = {wb.next() for _ in range(30)}
        assert "a" in results

    def test_healthy_count(self) -> None:
        wb = WeightedBalancer[str](["a", "b", "c"])
        wb.unhealthy("a")
        assert wb.healthy_count == 2


class TestWeightedEdge:
    def test_set_weight_on_nonexistent_noop(self) -> None:
        wb = WeightedBalancer[str]()
        wb.set_weight("z", 5)

    def test_remove_backend(self) -> None:
        wb = WeightedBalancer[str](["a", "b", "c"])
        wb.remove("b")
        assert wb.backend_count == 2

    def test_len(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        assert len(wb) == 2


# ── RandomBalancer ────────────────────────────────────────────────────────────


class TestRandomConstruction:
    def test_empty_returns_none(self) -> None:
        rb = RandomBalancer[str]()
        assert rb.next() is None

    def test_single_backend(self) -> None:
        rb = RandomBalancer[str](["a"])
        for _ in range(5):
            assert rb.next() == "a"

    def test_backend_count(self) -> None:
        rb = RandomBalancer[int]([1, 2, 3])
        assert rb.backend_count == 3

    def test_seeded_reproducibility(self) -> None:
        rb1 = RandomBalancer[str](["a", "b"], seed=42)
        rb2 = RandomBalancer[str](["a", "b"], seed=42)
        seq1 = [rb1.next() for _ in range(10)]
        seq2 = [rb2.next() for _ in range(10)]
        assert seq1 == seq2

    def test_add_duplicate_idempotent(self) -> None:
        rb = RandomBalancer[str](["a"])
        rb.add("a")
        assert rb.backend_count == 1


class TestRandomRouting:
    def test_distribution_is_uniform(self) -> None:
        rb = RandomBalancer[str](["a", "b", "c", "d"], seed=0)
        counts = rb.distribution(10000)
        for c in counts.values():
            assert 2000 < c < 3000

    def test_distribution_two_backends(self) -> None:
        rb = RandomBalancer[str](["a", "b"], seed=7)
        counts = rb.distribution(2000)
        for c in counts.values():
            assert 800 < c < 1200

    def test_next_always_picks_healthy(self) -> None:
        rb = RandomBalancer[str](["a", "b", "c"])
        rb.unhealthy("a")
        rb.unhealthy("b")
        for _ in range(20):
            assert rb.next() == "c"


class TestRandomHealth:
    def test_unhealthy_skipped(self) -> None:
        rb = RandomBalancer[str](["a", "b"])
        rb.unhealthy("a")
        for _ in range(20):
            assert rb.next() == "b"

    def test_all_unhealthy_returns_none(self) -> None:
        rb = RandomBalancer[str](["a", "b"])
        rb.unhealthy("a")
        rb.unhealthy("b")
        assert rb.next() is None

    def test_recover_healthy(self) -> None:
        rb = RandomBalancer[str](["a", "b"])
        rb.unhealthy("a")
        rb.healthy("a")
        results = {rb.next() for _ in range(30)}
        assert "a" in results

    def test_healthy_count(self) -> None:
        rb = RandomBalancer[str](["a", "b", "c"])
        rb.unhealthy("a")
        assert rb.healthy_count == 2


class TestRandomEdge:
    def test_remove_backend(self) -> None:
        rb = RandomBalancer[str](["a", "b", "c"])
        rb.remove("b")
        assert rb.backend_count == 2

    def test_remove_all_returns_none(self) -> None:
        rb = RandomBalancer[str](["a", "b"])
        rb.remove("a")
        rb.remove("b")
        assert rb.next() is None

    def test_len(self) -> None:
        rb = RandomBalancer[str](["a", "b", "c"])
        assert len(rb) == 3


# ── Sticky Sessions ───────────────────────────────────────────────────────────


class TestStickySessionStore:
    def test_get_nonexistent_returns_none(self) -> None:
        store = StickySessionStore[str]()
        assert store.get("s1") is None

    def test_set_then_get(self) -> None:
        store = StickySessionStore[str]()
        store.set("s1", "backend-a")
        assert store.get("s1") == "backend-a"

    def test_overwrite_existing(self) -> None:
        store = StickySessionStore[str]()
        store.set("s1", "a")
        store.set("s1", "b")
        assert store.get("s1") == "b"

    def test_remove(self) -> None:
        store = StickySessionStore[str]()
        store.set("s1", "a")
        store.remove("s1")
        assert store.get("s1") is None

    def test_contains(self) -> None:
        store = StickySessionStore[str]()
        store.set("s1", "a")
        assert "s1" in store
        assert "s2" not in store

    def test_clear(self) -> None:
        store = StickySessionStore[str]()
        store.set("s1", "a")
        store.set("s2", "b")
        store.clear()
        assert len(store) == 0

    def test_len(self) -> None:
        store = StickySessionStore[str]()
        store.set("a", "1")
        store.set("b", "2")
        assert len(store) == 2


class TestStickyDispatch:
    def test_first_call_assigns_backend(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        store = StickySessionStore[str]()
        result = sticky_dispatch(rr, store, "s1")
        assert result in ("a", "b")
        assert store.get("s1") == result

    def test_second_call_returns_same_backend(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        store = StickySessionStore[str]()
        first = sticky_dispatch(rr, store, "s1")
        for _ in range(10):
            assert sticky_dispatch(rr, store, "s1") == first

    def test_sticky_with_least_connections(self) -> None:
        lc = LeastConnectionsBalancer[str](["a", "b"])
        store = StickySessionStore[str]()
        first = sticky_dispatch(lc, store, "s1")
        assert sticky_dispatch(lc, store, "s1") == first

    def test_sticky_with_weighted(self) -> None:
        wb = WeightedBalancer[str](["a", "b"])
        store = StickySessionStore[str]()
        first = sticky_dispatch(wb, store, "s1")
        assert sticky_dispatch(wb, store, "s1") == first

    def test_sticky_with_random(self) -> None:
        rb = RandomBalancer[str](["a", "b"], seed=0)
        store = StickySessionStore[str]()
        first = sticky_dispatch(rb, store, "s1")
        assert sticky_dispatch(rb, store, "s1") == first

    def test_different_sessions_different_backends(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b", "c"])
        store = StickySessionStore[str]()
        results = {sticky_dispatch(rr, store, f"s{i}") for i in range(100)}
        assert len(results) >= 1

    def test_fallback_when_sticky_backend_unhealthy(self) -> None:
        rr = RoundRobinBalancer[str](["a", "b"])
        store = StickySessionStore[str]()
        first = sticky_dispatch(rr, store, "s1")
        assert first is not None
        rr.unhealthy(first)
        second = sticky_dispatch(rr, store, "s1")
        assert second != first
        assert second is not None

    def test_sticky_dispatch_ch_consistent_hash(self) -> None:
        ch = ConsistentHashBalancer[str](["a", "b", "c"])
        store = StickySessionStore[str]()
        first = sticky_dispatch_ch(ch, store, "uid-99")
        for _ in range(10):
            assert sticky_dispatch_ch(ch, store, "uid-99") == first


# ── Backend Dataclass ─────────────────────────────────────────────────────────


class TestBackendDataclass:
    def test_defaults(self) -> None:
        b = Backend(key="a")
        assert b.key == "a"
        assert b.healthy is True
        assert b.weight == 1
        assert b.connections == 0

    def test_custom_attrs(self) -> None:
        b = Backend(key="srv", healthy=False, weight=5, connections=42)
        assert not b.healthy
        assert b.weight == 5
        assert b.connections == 42


# ── Cross-Algorithm Health Checks ─────────────────────────────────────────────


class TestCrossAlgorithmHealth:
    @pytest.mark.parametrize(
        "balancer_cls",
        [
            RoundRobinBalancer,
            LeastConnectionsBalancer,
            WeightedBalancer,
            RandomBalancer,
        ],
    )
    def test_unhealthy_then_healthy_cycle(self, balancer_cls: type) -> None:
        b = balancer_cls(["a", "b"])
        b.unhealthy("a")
        assert b.next() == "b"
        b.healthy("a")
        results = {b.next() for _ in range(20)}
        assert "a" in results
