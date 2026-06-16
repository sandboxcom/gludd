"""Coverage tests for observability/dashboard_data.py (DashboardDataProvider).

The CI coverage report flagged this module at 44%. The uncovered paths are the
metrics-backed branches of ``_get_counter`` / ``_get_uptime`` (the ``if
self._metrics:`` true-branches) and the three async ``get_*`` accessors. These
tests exercise both the no-metrics (None) and metrics-present code paths.

Pure provider — no DB / network. A tiny fake metrics exporter supplies
``get_counters()`` and ``_started_at`` exactly as the real MetricsExporter does.
"""

from __future__ import annotations

import datetime

import pytest

from general_ludd.observability.dashboard_data import DashboardDataProvider


class _FakeMetrics:
    """Minimal stand-in for MetricsExporter used by DashboardDataProvider."""

    def __init__(self, counters: dict[str, int], started_at: float = 0.0) -> None:
        self._counters = counters
        self._started_at = started_at

    def get_counters(self) -> dict[str, int]:
        return self._counters


class TestGetCounterNoMetrics:
    async def test_overview_defaults_to_zero_without_metrics(self) -> None:
        provider = DashboardDataProvider()
        overview = await provider.get_overview()
        assert overview["model_calls_today"] == 0
        assert overview["todos_completed_today"] == 0
        assert overview["active_jobs"] == 0
        assert overview["queue_depths"] == {}
        assert overview["spend_today_usd"] == pytest.approx(0.0)
        # uptime no-metrics branch returns 0.0
        assert overview["uptime"] == pytest.approx(0.0)

    def test_get_counter_returns_zero_when_no_metrics(self) -> None:
        provider = DashboardDataProvider(metrics_exporter=None)
        assert provider._get_counter("anything") == 0

    def test_get_uptime_zero_when_no_metrics(self) -> None:
        provider = DashboardDataProvider(metrics_exporter=None)
        assert provider._get_uptime() == pytest.approx(0.0)


class TestGetCounterWithMetrics:
    async def test_overview_sums_prefix_matching_counters(self) -> None:
        metrics = _FakeMetrics(
            {
                "gludd_model_calls_total": 3,
                'gludd_model_calls_total{model="x"}': 5,
                "gludd_todos_completed_total": 7,
                "unrelated_counter": 99,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        overview = await provider.get_overview()
        # prefix match should sum both gludd_model_calls_total* entries (3 + 5)
        assert overview["model_calls_today"] == 8
        assert overview["todos_completed_today"] == 7

    def test_get_counter_prefix_match_excludes_non_matching(self) -> None:
        metrics = _FakeMetrics({"gludd_ticks_total": 4, "other": 100})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_ticks_total") == 4

    def test_get_uptime_uses_monotonic_minus_started_at(self) -> None:
        import time

        metrics = _FakeMetrics({}, started_at=time.monotonic() - 5.0)
        provider = DashboardDataProvider(metrics_exporter=metrics)
        uptime = provider._get_uptime()
        # ~5 s elapsed; allow generous slack, just assert positive + float
        assert isinstance(uptime, float)
        assert uptime >= 4.0


class TestAgentStatus:
    async def test_agent_status_shape_no_metrics(self) -> None:
        provider = DashboardDataProvider()
        agents = await provider.get_agent_status()
        assert len(agents) == 1
        agent = agents[0]
        assert agent["agent_id"] == "main"
        assert agent["status"] == "running"
        assert agent["tasks_completed"] == 0
        # last_tick must be an ISO-8601 parseable timestamp
        datetime.datetime.fromisoformat(agent["last_tick"])

    async def test_agent_status_tasks_completed_from_metrics(self) -> None:
        metrics = _FakeMetrics({"gludd_ticks_total": 11})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        agents = await provider.get_agent_status()
        assert agents[0]["tasks_completed"] == 11


class TestPipelineHealth:
    async def test_pipeline_health_static_shape(self) -> None:
        provider = DashboardDataProvider()
        health = await provider.get_pipeline_health()
        assert health == {
            "event_loop": "running",
            "db": "connected",
            "worker": "available",
            "model_gateway": "configured",
        }
