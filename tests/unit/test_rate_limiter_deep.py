"""Deep rate limiter and throttling tests covering all implementations.

Tests: _TokenBucket, SlidingWindowRateLimiter, per-key _RateLimiter,
AgentDispatcher dispatch rate limiter, SearxClient rate limiter,
SpendLimiter rolling-window spend cap, FloorController throttling,
and OrchestrationGuardConfig defaults.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.config.user_config import OrchestrationGuardConfig
from general_ludd.controllers.floor import FloorController
from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.receiver.router import _RateLimiter, _TokenBucket
from general_ludd.routers.web_search import SlidingWindowRateLimiter

# ---------------------------------------------------------------------------
# 1 — _TokenBucket tests (src/general_ludd/receiver/router.py)
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_allow_consumes_token(self) -> None:
        tb = _TokenBucket(rate_per_sec=1.0, burst=1.0)
        assert tb.allow() is True
        assert tb.allow() is False

    def test_burst_allows_multiple_immediate(self) -> None:
        tb = _TokenBucket(rate_per_sec=100.0, burst=5.0)
        for _ in range(5):
            assert tb.allow() is True
        assert tb.allow() is False

    def test_refill_over_time(self) -> None:
        tb = _TokenBucket(rate_per_sec=10.0, burst=1.0)
        assert tb.allow() is True
        assert tb.allow() is False
        time.sleep(0.15)
        assert tb.allow() is True

    def test_tokens_capped_at_burst(self) -> None:
        tb = _TokenBucket(rate_per_sec=1000.0, burst=3.0)
        time.sleep(0.5)
        for _ in range(3):
            assert tb.allow() is True
        assert tb.allow() is False

    def test_high_burst_saturates_immediately(self) -> None:
        tb = _TokenBucket(rate_per_sec=1.0, burst=50.0)
        for _ in range(50):
            assert tb.allow() is True
        assert tb.allow() is False

    def test_zero_rate_never_refills(self) -> None:
        tb = _TokenBucket(rate_per_sec=0.0, burst=1.0)
        assert tb.allow() is True
        assert tb.allow() is False
        time.sleep(0.1)
        assert tb.allow() is False

    def test_concurrent_thread_safety(self) -> None:
        tb = _TokenBucket(rate_per_sec=0.0, burst=10.0)
        results: list[bool] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(tb.allow())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        allowed = sum(results)
        assert 0 < allowed <= 10


# ---------------------------------------------------------------------------
# 2 — _RateLimiter per-key isolation tests
# ---------------------------------------------------------------------------


class TestPerKeyRateLimiter:
    def test_isolated_buckets_per_key(self) -> None:
        rl = _RateLimiter(rate_per_sec=1.0, burst=1.0)
        assert rl.allow("key_a") is True
        assert rl.allow("key_a") is False
        assert rl.allow("key_b") is True

    def test_lazy_bucket_creation(self) -> None:
        rl = _RateLimiter(rate_per_sec=1.0, burst=1.0)
        assert len(rl._buckets) == 0
        rl.allow("key_x")
        assert len(rl._buckets) == 1
        assert "key_x" in rl._buckets

    def test_same_key_reuses_bucket(self) -> None:
        rl = _RateLimiter(rate_per_sec=1.0, burst=1.0)
        rl.allow("key_k")
        rl.allow("key_k")
        assert len(rl._buckets) == 1

    def test_multiple_keys_independent_usage(self) -> None:
        rl = _RateLimiter(rate_per_sec=1.0, burst=3.0)
        for _ in range(3):
            rl.allow("key_1")
        assert rl.allow("key_1") is False
        for _ in range(3):
            rl.allow("key_2")
        assert rl.allow("key_2") is False
        for _ in range(3):
            assert rl.allow("key_3") is True

    def test_thread_safe_lazy_creation(self) -> None:
        rl = _RateLimiter(rate_per_sec=100.0, burst=5.0)
        results: list[bool] = []
        errors: list[Exception] = []

        def worker(key: str) -> None:
            try:
                results.append(rl.allow(key))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"tk_{i}",)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(results)


# ---------------------------------------------------------------------------
# 3 — SlidingWindowRateLimiter tests (web_search.py)
# ---------------------------------------------------------------------------


class TestSlidingWindowRateLimiter:
    def test_allow_within_capacity(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            assert rl.allow() is True

    def test_deny_over_capacity(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            assert rl.allow() is True
        assert rl.allow() is False

    def test_window_expiry_frees_slots(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=0.1)
        for _ in range(3):
            assert rl.allow() is True
        assert rl.allow() is False
        time.sleep(0.15)
        for _ in range(3):
            assert rl.allow() is True

    def test_old_timestamps_pruned(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=10, window_seconds=0.1)
        for _ in range(5):
            rl.allow()
        time.sleep(0.15)
        rl._timestamps.clear()
        for _ in range(10):
            assert rl.allow() is True

    def test_thread_safe_concurrent_access(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=50, window_seconds=60.0)
        results: list[bool] = []

        def worker() -> None:
            results.append(rl.allow())

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(results)
        assert allowed == 50

    def test_zero_max_requests_denies_all(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=0, window_seconds=60.0)
        assert rl.allow() is False


# ---------------------------------------------------------------------------
# 4 — AgentDispatcher dispatch rate limiter tests
# ---------------------------------------------------------------------------


class TestDispatchRateLimiter:
    @staticmethod
    def _make_registry() -> AgentRegistry:
        return AgentRegistry()

    @staticmethod
    def _invoker_config(name: str) -> AgentConfig:
        return AgentConfig(
            name=name,
            description=f"Invoker {name}",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_edit=False,
                can_bash=False,
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
            enabled=True,
        )

    @staticmethod
    def _subagent_config(name: str, enabled: bool = True) -> AgentConfig:
        return AgentConfig(
            name=name,
            description=f"Test subagent {name}",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False,
                can_bash=False,
                can_read=True,
                can_dispatch_subagents=False,
                allowed_subagents=[],
            ),
            enabled=enabled,
        )

    def _make_dispatcher(
        self,
        max_per_window: int = 10,
        window_s: float = 60.0,
    ) -> AgentDispatcher:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=max_per_window,
            dispatch_rate_window_s=window_s,
        )
        return AgentDispatcher(
            registry=self._make_registry(),
            orchestration_guard=guard,
        )

    @staticmethod
    def _make_task(name: str = "test_agent") -> AgentTask:
        return AgentTask(
            task_id="task-001",
            agent_name=name,
            description="test task",
            prompt="test prompt",
            invoker_name="test_invoker",
            project_id="proj-1",
            depth=1,
        )

    @pytest.mark.asyncio
    async def test_rate_limit_guard_off_returns_none(self) -> None:
        dispatcher = self._make_dispatcher(max_per_window=0, window_s=60.0)
        result = await dispatcher._check_rate_limiter(self._make_task())
        assert result is None

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_within_window(self) -> None:
        dispatcher = self._make_dispatcher(max_per_window=5, window_s=60.0)
        task = self._make_task()
        for _ in range(5):
            result = await dispatcher._check_rate_limiter(task)
            assert result is None

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_exceeding_window(self) -> None:
        dispatcher = self._make_dispatcher(max_per_window=3, window_s=60.0)
        task = self._make_task()
        for _ in range(3):
            assert await dispatcher._check_rate_limiter(task) is None
        result = await dispatcher._check_rate_limiter(task)
        assert result is not None
        assert result.status == "failed"
        assert "rate limited" in (result.output or "").lower()

    @pytest.mark.asyncio
    async def test_rate_limiter_prunes_expired_timestamps(self) -> None:
        dispatcher = self._make_dispatcher(max_per_window=3, window_s=0.05)
        task = self._make_task()
        for _ in range(3):
            await dispatcher._check_rate_limiter(task)
        await asyncio.sleep(0.1)
        result = await dispatcher._check_rate_limiter(task)
        assert result is None

    @pytest.mark.asyncio
    async def test_rate_limiter_guard_none_returns_none(self) -> None:
        dispatcher = AgentDispatcher(
            registry=self._make_registry(),
            orchestration_guard=None,
        )
        result = await dispatcher._check_rate_limiter(self._make_task())
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_tasks_count_toward_limit(self) -> None:
        dispatcher = self._make_dispatcher(max_per_window=3, window_s=60.0)
        for i in range(3):
            task = AgentTask(
                task_id=f"task-{i:03d}",
                agent_name="test_agent",
                description=f"test {i}",
                prompt=f"prompt {i}",
                invoker_name="test_invoker",
                project_id="proj-1",
                depth=1,
            )
            assert await dispatcher._check_rate_limiter(task) is None
        result = await dispatcher._check_rate_limiter(self._make_task())
        assert result is not None


# ---------------------------------------------------------------------------
# 5 — FloorController throttling tests
# ---------------------------------------------------------------------------


class TestFloorControllerDeep:
    def test_default_floor(self) -> None:
        fc = FloorController()
        assert fc.floor in (5, 10)

    def test_explicit_floor(self) -> None:
        fc = FloorController(floor=7)
        assert fc.floor == 7
        assert fc.get_max_active() == 7

    def test_get_max_active_full_health(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(100.0)
        assert fc.get_max_active() == 10

    def test_get_max_active_halved_below_50(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(40.0)
        assert fc.get_max_active() == 5

    def test_get_max_active_zero_below_25(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(20.0)
        assert fc.get_max_active() == 0

    def test_get_max_active_floor_one_halved_stays_one(self) -> None:
        fc = FloorController(floor=1)
        fc.update_health(30.0)
        assert fc.get_max_active() == 1

    def test_health_clamped_to_range(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(150.0)
        assert fc.health == 100.0
        fc.update_health(-10.0)
        assert fc.health == 0.0

    def test_auto_tune_lowers_floor_on_low_success_rate(self) -> None:
        fc = FloorController(floor=8)
        new_floor = fc.auto_tune(
            cpu_pct=30,
            memory_pct=40,
            dispatch_success_rate=80.0,
            queue_depth=5,
        )
        assert new_floor == 6
        assert fc.floor == 6

    def test_auto_tune_raises_floor_on_high_queue(self) -> None:
        fc = FloorController(floor=8)
        new_floor = fc.auto_tune(
            cpu_pct=30,
            memory_pct=40,
            dispatch_success_rate=97.0,
            queue_depth=25,
        )
        assert new_floor == 10
        assert fc.floor == 10

    def test_auto_tune_no_change_in_band(self) -> None:
        fc = FloorController(floor=8)
        new_floor = fc.auto_tune(
            cpu_pct=30,
            memory_pct=40,
            dispatch_success_rate=95.0,
            queue_depth=10,
        )
        assert new_floor == 8

    def test_auto_tune_floor_never_below_1(self) -> None:
        fc = FloorController(floor=2)
        new_floor = fc.auto_tune(
            cpu_pct=30,
            memory_pct=40,
            dispatch_success_rate=50.0,
            queue_depth=5,
        )
        assert new_floor >= 1

    def test_auto_tune_floor_never_above_20(self) -> None:
        fc = FloorController(floor=19)
        new_floor = fc.auto_tune(
            cpu_pct=30,
            memory_pct=40,
            dispatch_success_rate=98.0,
            queue_depth=30,
        )
        assert new_floor <= 20

    def test_auto_tune_records_history(self) -> None:
        fc = FloorController(floor=8)
        fc.auto_tune(
            cpu_pct=50,
            memory_pct=60,
            dispatch_success_rate=85.0,
            queue_depth=5,
        )
        history = fc.floor_history
        assert len(history) == 1
        assert history[0]["floor"] == 6
        assert history[0]["previous_floor"] == 8
        assert history[0]["reason"] == "low_success_rate"
        assert "timestamp" in history[0]

    def test_auto_tune_history_is_ordered(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(cpu_pct=30, memory_pct=40, dispatch_success_rate=80.0, queue_depth=5)
        fc.auto_tune(cpu_pct=30, memory_pct=40, dispatch_success_rate=98.0, queue_depth=30)
        assert len(fc.floor_history) == 2


# ---------------------------------------------------------------------------
# 6 — SpendLimiter rolling-window tests
# ---------------------------------------------------------------------------


class TestSpendLimiterDeep:
    def test_initial_window_spend_is_zero(self) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        assert limiter.window_spend() == 0.0

    def test_record_and_window_spend(self) -> None:
        clock_val = [0.0]

        def fake_clock() -> float:
            return clock_val[0]

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=fake_clock)
        limiter.record(cost_usd=3.0, kind="token")
        clock_val[0] = 10.0
        limiter.record(cost_usd=2.0, kind="token")
        assert limiter.window_spend() == 5.0

    def test_would_exceed_blocks_over_limit(self) -> None:
        clock_val = [0.0]

        def fake_clock() -> float:
            return clock_val[0]

        limiter = SpendLimiter(limit_usd=5.0, window_seconds=60.0, clock=fake_clock)
        limiter.record(cost_usd=4.5, kind="token")
        result = limiter.would_exceed(projected_usd=1.0)
        assert result is True

    def test_would_exceed_allows_within_limit(self) -> None:
        clock_val = [0.0]

        def fake_clock() -> float:
            return clock_val[0]

        limiter = SpendLimiter(limit_usd=5.0, window_seconds=60.0, clock=fake_clock)
        limiter.record(cost_usd=2.0, kind="token")
        result = limiter.would_exceed(projected_usd=1.0)
        assert result is False

    def test_records_outside_window_pruned(self) -> None:
        clock_val = [0.0]

        def fake_clock() -> float:
            return clock_val[0]

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=30.0, clock=fake_clock)
        limiter.record(cost_usd=5.0, kind="token")
        clock_val[0] = 40.0
        limiter.record(cost_usd=1.0, kind="token")
        assert limiter.window_spend() == 1.0

    def test_snapshot_roundtrip(self) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        limiter.record(cost_usd=3.0, kind="token", project_id="p1")
        limiter.record(cost_usd=2.0, kind="token", project_id="p2")
        snap = limiter.snapshot()
        limiter2 = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        limiter2.restore(snap)
        assert limiter2.window_spend() == pytest.approx(5.0)

    def test_token_cost_usd_falls_back_to_static(self) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60.0)
        cost = limiter.token_cost_usd("claude-3-opus", in_tokens=1000, out_tokens=0)
        assert cost > 0.0

    def test_record_with_project_id(self) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        limiter.record(cost_usd=5.0, kind="token", project_id="proj-a")
        snap = limiter.snapshot()
        assert any(pid == "proj-a" for _ts, _c, pid in snap)


# ---------------------------------------------------------------------------
# 7 — OrchestrationGuardConfig defaults tests
# ---------------------------------------------------------------------------


class TestOrchestrationGuardConfigDefaults:
    def test_default_rate_limiter_is_off(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_dispatches_per_window == 0

    def test_default_window_is_60_seconds(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.dispatch_rate_window_s == 60.0

    def test_default_nesting_depth(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_nesting_depth == 3

    def test_default_redispatch_count(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_redispatch_count == 5

    def test_enforce_capability_escalation_enabled_by_default(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.enforce_capability_escalation is True

    def test_max_concurrent_model_calls_default(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_concurrent_model_calls == 10


# ---------------------------------------------------------------------------
# 8 — Distributed / multi-instance isolation tests
# ---------------------------------------------------------------------------


class TestDistributedRateLimitingIsolation:
    def test_independent_instances_do_not_share_state(self) -> None:
        rl1 = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
        rl2 = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            rl1.allow()
        for _ in range(3):
            assert rl2.allow() is True

    def test_independent_token_buckets_per_instance(self) -> None:
        key_rl1 = _RateLimiter(rate_per_sec=1.0, burst=1.0)
        key_rl2 = _RateLimiter(rate_per_sec=1.0, burst=1.0)
        assert key_rl1.allow("a") is True
        assert key_rl1.allow("a") is False
        assert key_rl2.allow("a") is True

    def test_independent_spend_limiters_do_not_share(self) -> None:
        lim1 = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        lim2 = SpendLimiter(limit_usd=10.0, window_seconds=60.0, clock=lambda: 0.0)
        lim1.record(cost_usd=9.0, kind="token")
        assert lim2.window_spend() == 0.0

    def test_independent_floor_controllers_isolated(self) -> None:
        fc1 = FloorController(floor=10)
        fc2 = FloorController(floor=10)
        fc1.auto_tune(cpu_pct=80, memory_pct=90, dispatch_success_rate=80.0, queue_depth=5)
        assert fc2.floor == 10
