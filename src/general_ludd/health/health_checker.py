"""Pluggable health-check framework with timeout, parallel execution, aggregation.

Three-tier status model:
- ``healthy`` — all checks passed within their timeouts.
- ``degraded`` — at least one check returned degraded or was classified degraded
  (e.g. unconfigured backend), but no check is truly unhealthy.
- ``unhealthy`` — at least one check failed, timed out, or raised.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable, Coroutine
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Status / result types
# ---------------------------------------------------------------------------


class HealthStatus(enum.Enum):
    """Aggregated health state for checks and probes."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class CheckResult:
    """Outcome of a single health-check run."""

    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    detail: str = ""
    duration_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# HealthCheck — a single pluggable check
# ---------------------------------------------------------------------------


@dataclass
class HealthCheck:
    """A single pluggable check: name, async check_fn, timeout, and tags."""

    name: str
    check_fn: Callable[[], Coroutine[None, None, object]]
    timeout_s: float = 10.0
    tags: frozenset[str] | AbstractSet[str] = field(default_factory=frozenset)

    _RESULT_OK = CheckResult(name="", status=HealthStatus.HEALTHY, detail="ok")

    def __post_init__(self) -> None:
        """Coerce mutable tag sets to frozenset so checks are hashable."""
        if not isinstance(self.tags, frozenset):
            object.__setattr__(self, "tags", frozenset(self.tags))

    async def run(self) -> CheckResult:
        """Run the check under its timeout and normalise the outcome."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.timeout_s):
                outcome = await self.check_fn()
        except TimeoutError:
            duration = time.monotonic() - start
            return CheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                detail=f"timed out after {self.timeout_s:.1f}s",
                duration_s=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return CheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                detail=f"{type(exc).__name__}: {exc}",
                duration_s=duration,
                error=str(exc),
            )

        return self._normalise(outcome, time.monotonic() - start)

    def _normalise(
        self,
        outcome: object,
        duration_s: float,
    ) -> CheckResult:
        if isinstance(outcome, CheckResult):
            if outcome.name:
                result = outcome
            else:
                result = CheckResult(
                    name=self.name,
                    status=outcome.status,
                    detail=outcome.detail,
                    duration_s=outcome.duration_s,
                    error=outcome.error,
                )
        elif isinstance(outcome, HealthStatus):
            result = CheckResult(
                name=self.name,
                status=outcome,
                detail=outcome.value,
                duration_s=duration_s,
            )
        elif isinstance(outcome, bool):
            result = CheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY if outcome else HealthStatus.UNHEALTHY,
                detail=f"bool: {outcome}",
                duration_s=duration_s,
            )
        elif outcome is None:
            result = CheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                detail="returned None (ok)",
                duration_s=duration_s,
            )
        else:
            result = CheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                detail=f"unhandled return type: {type(outcome).__name__}",
                duration_s=duration_s,
            )
        if not result.name:
            result = CheckResult(
                name=self.name,
                status=result.status,
                detail=result.detail,
                duration_s=result.duration_s,
                error=result.error,
            )
        if not result.duration_s:
            result = CheckResult(
                name=result.name,
                status=result.status,
                detail=result.detail,
                duration_s=duration_s,
                error=result.error,
            )
        return result


# ---------------------------------------------------------------------------
# HealthChecker — aggregate runner
# ---------------------------------------------------------------------------

_AGGREGATION_PRIORITY = (
    HealthStatus.UNHEALTHY,
    HealthStatus.DEGRADED,
    HealthStatus.HEALTHY,
)


@dataclass
class HealthChecker:
    """Aggregate runner for a list of HealthCheck instances."""

    checks: list[HealthCheck] = field(default_factory=list)

    def add_check(self, check: HealthCheck) -> None:
        """Register a check."""
        self.checks.append(check)

    def remove_check(self, name: str) -> bool:
        """Remove a check by name; return True when one was removed."""
        before = len(self.checks)
        self.checks = [c for c in self.checks if c.name != name]
        return len(self.checks) < before

    def get_check(self, name: str) -> HealthCheck | None:
        """Return the check with the given name, or None."""
        for c in self.checks:
            if c.name == name:
                return c
        return None

    def list_checks(self) -> list[str]:
        """Return the names of all registered checks."""
        return [c.name for c in self.checks]

    # -- running ---------------------------------------------------------------

    async def run_all(self) -> tuple[HealthStatus, list[CheckResult]]:
        """Run all checks sequentially and aggregate the status."""
        results: list[CheckResult] = []
        for check in self.checks:
            result = await check.run()
            results.append(result)
        return _aggregate_status(results), results

    async def run_all_parallel(self) -> tuple[HealthStatus, list[CheckResult]]:
        """Run all checks concurrently and aggregate the status."""
        tasks = {asyncio.ensure_future(check.run()): check for check in self.checks}
        if not tasks:
            return HealthStatus.HEALTHY, []
        done, _ = await asyncio.wait(tasks.keys())
        results = [task.result() for task in done]
        results.sort(key=lambda r: r.name)
        return _aggregate_status(results), results

    async def run_by_name(self, name: str) -> CheckResult:
        """Run the named check and return its result."""
        check = self.get_check(name)
        if check is None:
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                detail=f"no check named '{name}'",
            )
        return await check.run()

    async def run_by_tag(self, tag: str) -> tuple[HealthStatus, list[CheckResult]]:
        """Run checks carrying the given tag and aggregate the status."""
        tagged = [c for c in self.checks if tag in c.tags]
        tasks = {asyncio.ensure_future(c.run()): c for c in tagged}
        if not tasks:
            return HealthStatus.HEALTHY, []
        done, _ = await asyncio.wait(tasks.keys())
        results = [task.result() for task in done]
        results.sort(key=lambda r: r.name)
        return _aggregate_status(results), results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aggregate_status(results: list[CheckResult]) -> HealthStatus:
    if not results:
        return HealthStatus.HEALTHY
    accumulator = HealthStatus.HEALTHY
    for r in results:
        for candidate in _AGGREGATION_PRIORITY:
            if r.status is candidate:
                if _AGGREGATION_PRIORITY.index(candidate) < _AGGREGATION_PRIORITY.index(accumulator):
                    accumulator = candidate
                break
    return accumulator
