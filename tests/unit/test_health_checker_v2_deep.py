"""Deep health-check framework tests — 22 tests.

Coverage:
- HealthCheck creation, name, timeout, tags
- run(): bool, HealthStatus, CheckResult, None return handling
- run(): timeout path
- run(): exception path
- CheckResult normalisation edge cases
- HealthChecker: add, remove, get, list
- HealthChecker.run_all(): serial execution + status aggregation
- HealthChecker.run_all_parallel(): concurrent execution
- HealthChecker.run_by_name(): existing + missing
- HealthChecker.run_by_tag()
- Status aggregation: healthy, degraded, unhealthy priority
- Empty checker, empty tag filter
"""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.health.health_checker import (
    CheckResult,
    HealthCheck,
    HealthChecker,
    HealthStatus,
)

# ---------------------------------------------------------------------------
# HealthCheck — creation
# ---------------------------------------------------------------------------


class TestHealthCheckCreation:
    def test_defaults(self):
        async def _ok() -> None:
            return None

        hc = HealthCheck(name="db", check_fn=_ok)
        assert hc.name == "db"
        assert hc.timeout_s == 10.0
        assert hc.tags == frozenset()
        assert callable(hc.check_fn)

    def test_custom_timeout(self):
        async def _ok() -> None:
            return None

        hc = HealthCheck(name="slow", check_fn=_ok, timeout_s=0.5)
        assert hc.timeout_s == 0.5

    def test_tags_frozen(self):
        async def _ok() -> None:
            return None

        hc = HealthCheck(name="tagged", check_fn=_ok, tags={"db", "critical"})
        assert hc.tags == frozenset({"db", "critical"})
        assert "db" in hc.tags
        assert "critical" in hc.tags


# ---------------------------------------------------------------------------
# HealthCheck.run() — boolean return
# ---------------------------------------------------------------------------


class TestHealthCheckRunBool:
    @pytest.mark.asyncio
    async def test_returns_true_becomes_healthy(self):
        hc = HealthCheck(name="bool-ok", check_fn=lambda: asyncio.sleep(0, result=True))
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "bool-ok"
        assert result.duration_s >= 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_returns_false_becomes_unhealthy(self):
        hc = HealthCheck(name="bool-bad", check_fn=lambda: asyncio.sleep(0, result=False))
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "bool: False" in result.detail


# ---------------------------------------------------------------------------
# HealthCheck.run() — HealthStatus return
# ---------------------------------------------------------------------------


class TestHealthCheckRunHealthStatus:
    @pytest.mark.asyncio
    async def test_returns_healthy(self):
        async def _fn():
            return HealthStatus.HEALTHY

        hc = HealthCheck(name="hs", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_returns_degraded(self):
        async def _fn():
            return HealthStatus.DEGRADED

        hc = HealthCheck(name="hs-d", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_returns_unhealthy(self):
        async def _fn():
            return HealthStatus.UNHEALTHY

        hc = HealthCheck(name="hs-u", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# HealthCheck.run() — CheckResult return
# ---------------------------------------------------------------------------


class TestHealthCheckRunCheckResult:
    @pytest.mark.asyncio
    async def test_passes_through_named_result(self):
        async def _fn():
            return CheckResult(
                name="custom",
                status=HealthStatus.DEGRADED,
                detail="low disk",
                duration_s=0.3,
            )

        hc = HealthCheck(name="orig", check_fn=_fn)
        result = await hc.run()
        assert result.name == "custom"
        assert result.status == HealthStatus.DEGRADED
        assert result.detail == "low disk"
        assert result.duration_s == 0.3

    @pytest.mark.asyncio
    async def test_fills_empty_name_from_check(self):
        async def _fn():
            return CheckResult(name="", status=HealthStatus.HEALTHY, detail="ok")

        hc = HealthCheck(name="filler", check_fn=_fn)
        result = await hc.run()
        assert result.name == "filler"


# ---------------------------------------------------------------------------
# HealthCheck.run() — None return
# ---------------------------------------------------------------------------


class TestHealthCheckRunNone:
    @pytest.mark.asyncio
    async def test_none_means_healthy(self):
        async def _fn():
            return None

        hc = HealthCheck(name="none-ok", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY
        assert result.error is None


# ---------------------------------------------------------------------------
# HealthCheck.run() — timeout
# ---------------------------------------------------------------------------


class TestHealthCheckRunTimeout:
    @pytest.mark.asyncio
    async def test_timeout_becomes_unhealthy(self):
        async def _slow():
            await asyncio.sleep(99)

        hc = HealthCheck(name="timeout", check_fn=_slow, timeout_s=0.01)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.detail
        assert result.name == "timeout"


# ---------------------------------------------------------------------------
# HealthCheck.run() — exception
# ---------------------------------------------------------------------------


class TestHealthCheckRunException:
    @pytest.mark.asyncio
    async def test_exception_becomes_unhealthy(self):
        async def _raise():
            raise ValueError("boom")

        hc = HealthCheck(name="crash", check_fn=_raise)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "ValueError" in result.detail
        assert "boom" in result.detail
        assert result.error is not None


# ---------------------------------------------------------------------------
# CheckResult normalisation — unhandled type
# ---------------------------------------------------------------------------


class TestHealthCheckRunUnhandledType:
    @pytest.mark.asyncio
    async def test_int_return_becomes_unhealthy(self):
        async def _fn():
            return 42

        hc = HealthCheck(name="int-return", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "int" in result.detail


# ---------------------------------------------------------------------------
# HealthChecker — add / remove / get / list
# ---------------------------------------------------------------------------


class TestHealthCheckerCRUD:
    @pytest.mark.asyncio
    async def test_add_get_list(self):
        checker = HealthChecker()
        hc = HealthCheck(name="db", check_fn=lambda: asyncio.sleep(0, result=True))
        checker.add_check(hc)
        assert checker.list_checks() == ["db"]
        assert checker.get_check("db") is hc

    def test_remove_existing(self):
        checker = HealthChecker()
        hc = HealthCheck(name="x", check_fn=lambda: asyncio.sleep(0, result=True))
        checker.add_check(hc)
        assert checker.remove_check("x") is True
        assert checker.list_checks() == []

    def test_remove_missing(self):
        checker = HealthChecker()
        assert checker.remove_check("ghost") is False

    def test_get_missing(self):
        checker = HealthChecker()
        assert checker.get_check("nope") is None


# ---------------------------------------------------------------------------
# HealthChecker.run_all() — serial
# ---------------------------------------------------------------------------


class TestHealthCheckerRunAll:
    @pytest.mark.asyncio
    async def test_all_healthy_returns_healthy(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="a", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="b", check_fn=lambda: asyncio.sleep(0, result=True)))

        status, results = await checker.run_all()
        assert status == HealthStatus.HEALTHY
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_one_unhealthy_aggregates_unhealthy(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="ok", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="bad", check_fn=lambda: asyncio.sleep(0, result=False)))

        status, _ = await checker.run_all()
        assert status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_mixed_healthy_degraded_becomes_degraded(self):
        checker = HealthChecker()

        async def _degraded():
            return HealthStatus.DEGRADED

        checker.add_check(HealthCheck(name="a", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="b", check_fn=_degraded))

        status, _ = await checker.run_all()
        assert status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_unhealthy_wins_over_degraded(self):
        checker = HealthChecker()

        async def _degraded():
            return HealthStatus.DEGRADED

        checker.add_check(HealthCheck(name="a", check_fn=_degraded))
        checker.add_check(HealthCheck(name="b", check_fn=lambda: asyncio.sleep(0, result=False)))

        status, _ = await checker.run_all()
        assert status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_empty_checks_returns_healthy(self):
        checker = HealthChecker()
        status, results = await checker.run_all()
        assert status == HealthStatus.HEALTHY
        assert results == []


# ---------------------------------------------------------------------------
# HealthChecker.run_all_parallel() — concurrent
# ---------------------------------------------------------------------------


class TestHealthCheckerRunAllParallel:
    @pytest.mark.asyncio
    async def test_parallel_all_healthy(self):
        checker = HealthChecker()
        for name in ("a", "b", "c"):
            checker.add_check(HealthCheck(name=name, check_fn=lambda: asyncio.sleep(0, result=True)))

        status, results = await checker.run_all_parallel()
        assert status == HealthStatus.HEALTHY
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_parallel_one_unhealthy(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="ok", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="bad", check_fn=lambda: asyncio.sleep(0, result=False)))

        status, _ = await checker.run_all_parallel()
        assert status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_parallel_empty(self):
        checker = HealthChecker()
        status, results = await checker.run_all_parallel()
        assert status == HealthStatus.HEALTHY
        assert results == []

    @pytest.mark.asyncio
    async def test_parallel_timeout_in_mix(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="ok-a", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="ok-b", check_fn=lambda: asyncio.sleep(0, result=True)))
        checker.add_check(HealthCheck(name="slow", check_fn=lambda: asyncio.sleep(99), timeout_s=0.01))

        status, results = await checker.run_all_parallel()
        assert status == HealthStatus.UNHEALTHY
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_parallel_preserves_timing_independence(self):
        """A slow check does not delay fast checks (parallelism is real)."""

        async def _slow():
            await asyncio.sleep(0.2)
            return True

        checker = HealthChecker()
        checker.add_check(HealthCheck(name="slow", check_fn=_slow, timeout_s=5.0))
        checker.add_check(HealthCheck(name="fast", check_fn=lambda: asyncio.sleep(0, result=True)))

        _, results = await checker.run_all_parallel()
        slow_result = next(r for r in results if r.name == "slow")
        fast_result = next(r for r in results if r.name == "fast")
        assert slow_result.duration_s > 0.1
        assert fast_result.duration_s < 0.1


# ---------------------------------------------------------------------------
# HealthChecker.run_by_name()
# ---------------------------------------------------------------------------


class TestHealthCheckerRunByName:
    @pytest.mark.asyncio
    async def test_runs_existing_check(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="ping", check_fn=lambda: asyncio.sleep(0, result=True)))

        result = await checker.run_by_name("ping")
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "ping"

    @pytest.mark.asyncio
    async def test_missing_name_returns_unhealthy(self):
        checker = HealthChecker()
        result = await checker.run_by_name("ghost")
        assert result.status == HealthStatus.UNHEALTHY
        assert "no check named" in result.detail


# ---------------------------------------------------------------------------
# HealthChecker.run_by_tag()
# ---------------------------------------------------------------------------


class TestHealthCheckerRunByTag:
    @pytest.mark.asyncio
    async def test_runs_only_tagged_checks(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="db", check_fn=lambda: asyncio.sleep(0, result=True), tags={"critical"}))
        checker.add_check(HealthCheck(name="cache", check_fn=lambda: asyncio.sleep(0, result=True), tags={"aux"}))

        status, results = await checker.run_by_tag("critical")
        assert status == HealthStatus.HEALTHY
        assert len(results) == 1
        assert results[0].name == "db"

    @pytest.mark.asyncio
    async def test_no_matching_tag_returns_healthy_empty(self):
        checker = HealthChecker()
        checker.add_check(HealthCheck(name="db", check_fn=lambda: asyncio.sleep(0, result=True), tags={"critical"}))

        status, results = await checker.run_by_tag("nonexistent")
        assert status == HealthStatus.HEALTHY
        assert results == []


# ---------------------------------------------------------------------------
# Status aggregation priority
# ---------------------------------------------------------------------------


class TestStatusAggregationPriority:
    def test_unhealthy_wins_over_all(self):
        from general_ludd.health.health_checker import _aggregate_status

        crs = [
            CheckResult(name="a", status=HealthStatus.HEALTHY),
            CheckResult(name="b", status=HealthStatus.DEGRADED),
            CheckResult(name="c", status=HealthStatus.UNHEALTHY),
        ]
        assert _aggregate_status(crs) == HealthStatus.UNHEALTHY

    def test_degraded_wins_over_healthy(self):
        from general_ludd.health.health_checker import _aggregate_status

        crs = [
            CheckResult(name="a", status=HealthStatus.HEALTHY),
            CheckResult(name="b", status=HealthStatus.DEGRADED),
        ]
        assert _aggregate_status(crs) == HealthStatus.DEGRADED

    def test_healthy_when_all_healthy(self):
        from general_ludd.health.health_checker import _aggregate_status

        crs = [
            CheckResult(name="a", status=HealthStatus.HEALTHY),
            CheckResult(name="b", status=HealthStatus.HEALTHY),
        ]
        assert _aggregate_status(crs) == HealthStatus.HEALTHY

    def test_empty_list_returns_healthy(self):
        from general_ludd.health.health_checker import _aggregate_status

        assert _aggregate_status([]) == HealthStatus.HEALTHY


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------


class TestCheckResultDataclass:
    def test_default_values(self):
        cr = CheckResult(name="test")
        assert cr.name == "test"
        assert cr.status == HealthStatus.HEALTHY
        assert cr.detail == ""
        assert cr.duration_s == 0.0
        assert cr.error is None

    def test_custom_fields(self):
        cr = CheckResult(
            name="fail",
            status=HealthStatus.UNHEALTHY,
            detail="disk full",
            duration_s=2.5,
            error="OSError",
        )
        assert cr.status == HealthStatus.UNHEALTHY
        assert cr.duration_s == 2.5
        assert cr.error == "OSError"
