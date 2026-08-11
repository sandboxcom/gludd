"""Deep edge-case tests for health_checker.py — uncovered coverage gaps.

Targets:
- Zero timeout (always timeout)
- String/float/list unhandled return types
- Exception with no message (empty string error)
- Zero-duration CheckResult falsy 0.0 or branch
- Remove duplicates (same name, multiple checks)
- _aggregate_status with single result
- CheckResult name=None (not empty string)
- Tags with duplicates (dedup via frozenset)
- CheckResult with error="" (empty string, not None)
- HealthCheck.tags already frozen (no-op re-freeze)
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
# Zero timeout — always fails
# ---------------------------------------------------------------------------


class TestZeroTimeout:
    @pytest.mark.asyncio
    async def test_timeout_s_zero_catches_slow_fn(self):
        """timeout_s=0.0: a fast fn may win the race, but a slow fn triggers timeout."""

        async def _slow():
            await asyncio.sleep(0.1)
            return True

        hc = HealthCheck(name="t-zero", check_fn=_slow, timeout_s=0.0)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# Unhandled return types — string, float, list
# ---------------------------------------------------------------------------


class TestUnhandledReturnTypes:
    @pytest.mark.asyncio
    async def test_string_return_becomes_unhealthy(self):
        async def _fn():
            return "some string"

        hc = HealthCheck(name="str-return", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "unhandled return type: str" in result.detail

    @pytest.mark.asyncio
    async def test_float_return_becomes_unhealthy(self):
        async def _fn():
            return 3.14

        hc = HealthCheck(name="float-return", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "unhandled return type: float" in result.detail

    @pytest.mark.asyncio
    async def test_list_return_becomes_unhealthy(self):
        async def _fn():
            return [1, 2, 3]

        hc = HealthCheck(name="list-return", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "unhandled return type: list" in result.detail

    @pytest.mark.asyncio
    async def test_dict_return_becomes_unhealthy(self):
        async def _fn():
            return {"key": "val"}

        hc = HealthCheck(name="dict-return", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "unhandled return type: dict" in result.detail


# ---------------------------------------------------------------------------
# Exception edge cases
# ---------------------------------------------------------------------------


class TestExceptionEdgeCases:
    @pytest.mark.asyncio
    async def test_exception_with_no_message(self):
        async def _raise():
            raise Exception()

        hc = HealthCheck(name="empty-exc", check_fn=_raise)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert "Exception" in result.detail
        assert result.error is not None
        assert result.name == "empty-exc"

    @pytest.mark.asyncio
    async def test_exception_very_long_message_is_preserved(self):
        long_msg = "x" * 500

        async def _raise():
            raise RuntimeError(long_msg)

        hc = HealthCheck(name="long-exc", check_fn=_raise)
        result = await hc.run()
        assert result.status == HealthStatus.UNHEALTHY
        assert result.error == long_msg
        assert long_msg in result.detail

    @pytest.mark.asyncio
    async def test_base_exception_propagates_not_caught(self):
        """BaseException subclasses propagate — they are NOT caught by except Exception."""

        async def _raise():
            raise SystemExit(0)

        hc = HealthCheck(name="base-exc", check_fn=_raise)
        raised = False
        try:
            await hc.run()
        except SystemExit:
            raised = True
        assert raised is True


# ---------------------------------------------------------------------------
# CheckResult zero-duration falsy branch
# ---------------------------------------------------------------------------


class TestZeroDurationFalsy:
    @pytest.mark.asyncio
    async def test_checkresult_zero_duration_falls_back_to_measured(self):
        """When a check_fn returns CheckResult(duration_s=0) the 0.0 or fallback kicks in."""

        async def _fn():
            return CheckResult(
                name="explicit",
                status=HealthStatus.HEALTHY,
                detail="ok",
                duration_s=0.0,
            )

        hc = HealthCheck(name="fallback-name", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "explicit"
        assert result.duration_s > 0

    @pytest.mark.asyncio
    async def test_checkresult_none_duration_falls_back(self):
        async def _fn():
            cr = CheckResult(name="x", status=HealthStatus.HEALTHY)
            cr.duration_s = None
            return cr

        hc = HealthCheck(name="none-dur", check_fn=_fn)
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY
        assert result.duration_s > 0


# ---------------------------------------------------------------------------
# Remove duplicates — same name in multiple checks
# ---------------------------------------------------------------------------


class TestRemoveAllDuplicates:
    def test_removes_all_checks_with_same_name(self):
        checker = HealthChecker()

        async def _ok():
            return True

        checker.add_check(HealthCheck(name="dup", check_fn=_ok))
        checker.add_check(HealthCheck(name="dup", check_fn=_ok))
        checker.add_check(HealthCheck(name="unique", check_fn=_ok))

        assert len(checker.checks) == 3
        removed = checker.remove_check("dup")
        assert removed is True
        assert len(checker.checks) == 1
        assert checker.checks[0].name == "unique"


# ---------------------------------------------------------------------------
# _aggregate_status with single result
# ---------------------------------------------------------------------------


class TestAggregateStatusEdgeCases:
    def test_single_healthy(self):
        from general_ludd.health.health_checker import _aggregate_status

        results = [CheckResult(name="a", status=HealthStatus.HEALTHY)]
        assert _aggregate_status(results) == HealthStatus.HEALTHY

    def test_single_degraded(self):
        from general_ludd.health.health_checker import _aggregate_status

        results = [CheckResult(name="a", status=HealthStatus.DEGRADED)]
        assert _aggregate_status(results) == HealthStatus.DEGRADED

    def test_single_unhealthy(self):
        from general_ludd.health.health_checker import _aggregate_status

        results = [CheckResult(name="a", status=HealthStatus.UNHEALTHY)]
        assert _aggregate_status(results) == HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# CheckResult name=None (not empty string)
# ---------------------------------------------------------------------------


class TestCheckResultNameNone:
    @pytest.mark.asyncio
    async def test_name_none_filled_from_check(self):
        async def _fn():
            cr = CheckResult(name="", status=HealthStatus.HEALTHY)
            object.__setattr__(cr, "name", None)
            return cr

        hc = HealthCheck(name="filler", check_fn=_fn)
        result = await hc.run()
        assert result.name == "filler"
        assert result.status == HealthStatus.HEALTHY


# ---------------------------------------------------------------------------
# Tags with duplicates
# ---------------------------------------------------------------------------


class TestTagsDeduplication:
    def test_duplicate_tags_deduped(self):
        async def _ok():
            return None

        hc = HealthCheck(name="t", check_fn=_ok, tags=["db", "db", "critical"])
        assert hc.tags == frozenset({"db", "critical"})
        assert len(hc.tags) == 2

    def test_tags_already_frozen_not_refrozen(self):
        async def _ok():
            return None

        frozen = frozenset({"db", "cache"})
        hc = HealthCheck(name="t", check_fn=_ok, tags=frozen)
        assert hc.tags is frozen


# ---------------------------------------------------------------------------
# CheckResult.error = "" (empty string, not None)
# ---------------------------------------------------------------------------


class TestCheckResultErrorEdgeCases:
    @pytest.mark.asyncio
    async def test_error_empty_string_preserved(self):
        async def _fn():
            return CheckResult(
                name="e",
                status=HealthStatus.UNHEALTHY,
                detail="bad",
                error="",
            )

        hc = HealthCheck(name="empty-err", check_fn=_fn)
        result = await hc.run()
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_error_none_preserved(self):
        async def _fn():
            return CheckResult(
                name="e",
                status=HealthStatus.HEALTHY,
                detail="ok",
                error=None,
            )

        hc = HealthCheck(name="none-err", check_fn=_fn)
        result = await hc.run()
        assert result.error is None


# ---------------------------------------------------------------------------
# HealthCheck.__post_init__ no-op with frozen tags
# ---------------------------------------------------------------------------


class TestPostInitEdgeCases:
    def test_frozen_tags_passthrough(self):
        async def _ok():
            return None

        froz = frozenset(["a", "b"])
        hc = HealthCheck(name="t", check_fn=_ok, tags=froz)
        assert hc.tags is froz

    def test_set_tags_converted_to_frozenset(self):
        async def _ok():
            return None

        hc = HealthCheck(name="t", check_fn=_ok, tags={"a"})
        assert isinstance(hc.tags, frozenset)


# ---------------------------------------------------------------------------
# HealthChecker boundary: many checks, mixed statuses
# ---------------------------------------------------------------------------


class TestManyChecksAggregation:
    @pytest.mark.asyncio
    async def test_degraded_at_end_wins_with_many_healthy(self):
        checker = HealthChecker()
        for i in range(50):
            checker.add_check(
                HealthCheck(
                    name=f"ok-{i}",
                    check_fn=lambda: asyncio.sleep(0, result=True),
                )
            )

        async def _degraded():
            return HealthStatus.DEGRADED

        checker.add_check(HealthCheck(name="last-bad", check_fn=_degraded))
        status, _results = await checker.run_all()
        assert status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_healthy_at_start_unhealthy_at_end_wins(self):
        checker = HealthChecker()

        async def _unhealthy():
            return HealthStatus.UNHEALTHY

        checker.add_check(HealthCheck(name="bad", check_fn=_unhealthy))
        for i in range(50):
            checker.add_check(
                HealthCheck(
                    name=f"ok-{i}",
                    check_fn=lambda: asyncio.sleep(0, result=True),
                )
            )
        status, _ = await checker.run_all()
        assert status == HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# run_by_tag — tag matches multiple checks, then one degrades
# ---------------------------------------------------------------------------


class TestRunByTagEdgeCases:
    @pytest.mark.asyncio
    async def test_tag_matches_multiple_checks_one_degraded(self):
        checker = HealthChecker()

        async def _ok():
            return True

        async def _degraded():
            return HealthStatus.DEGRADED

        checker.add_check(HealthCheck(name="a", check_fn=_ok, tags={"shared"}))
        checker.add_check(HealthCheck(name="b", check_fn=_degraded, tags={"shared"}))
        checker.add_check(HealthCheck(name="c", check_fn=_ok, tags={"other"}))

        status, results = await checker.run_by_tag("shared")
        assert status == HealthStatus.DEGRADED
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_check_matches_multiple_tags_still_runs_once(self):
        """When a check has tags {tag1, tag2}, running by tag1 only runs it once."""
        checker = HealthChecker()

        async def _ok():
            return True

        checker.add_check(HealthCheck(name="multi", check_fn=_ok, tags={"critical", "aux"}))

        _, results = await checker.run_by_tag("critical")
        assert len(results) == 1
        assert results[0].name == "multi"


# ---------------------------------------------------------------------------
# timeout_s boundary values
# ---------------------------------------------------------------------------


class TestTimeoutBoundaryValues:
    @pytest.mark.asyncio
    async def test_negative_timeout_is_accepted_as_dataclass(self):
        """Negative timeout is stored but is a user error, not a framework error."""

        async def _ok():
            return True

        hc = HealthCheck(name="neg", check_fn=_ok, timeout_s=-1.0)
        assert hc.timeout_s == -1.0

    @pytest.mark.asyncio
    async def test_very_large_timeout_still_runs(self):
        """Huge timeout — check still completes normally."""

        async def _ok():
            return True

        hc = HealthCheck(name="huge", check_fn=_ok, timeout_s=99999.0)
        result = await hc.run()
        assert result.status == HealthStatus.HEALTHY
