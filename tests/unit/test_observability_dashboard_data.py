"""Structural tests for observability/dashboard_data.py — DashboardDataProvider."""

from __future__ import annotations

from general_ludd.observability.dashboard_data import DashboardDataProvider


class TestDashboardDataProvider:
    def test_instantiation_defaults(self):
        provider = DashboardDataProvider()
        assert provider._metrics is None
        assert provider._session_factory is None

    def test_instantiation_with_args(self):
        fake_metrics = object()
        fake_session = object()
        provider = DashboardDataProvider(metrics_exporter=fake_metrics, session_factory=fake_session)
        assert provider._metrics is fake_metrics
        assert provider._session_factory is fake_session

    def test_get_counter_no_metrics(self):
        provider = DashboardDataProvider()
        result = provider._get_counter("gludd_model_calls_total")
        assert result == 0

    def test_get_uptime_no_metrics(self):
        provider = DashboardDataProvider()
        result = provider._get_uptime()
        assert result == 0.0

    def test_get_pipeline_health_sync_shape(self):
        provider = DashboardDataProvider()
        import asyncio
        result = asyncio.run(provider.get_pipeline_health())
        assert isinstance(result, dict)
        assert "event_loop" in result
        assert "db" in result
        assert "worker" in result
        assert "model_gateway" in result

    def test_get_overview_sync_shape(self):
        provider = DashboardDataProvider()
        import asyncio
        result = asyncio.run(provider.get_overview())
        assert isinstance(result, dict)
        assert "uptime" in result
        assert "active_jobs" in result
        assert result["active_jobs"] == 0

    def test_get_agent_status_sync_shape(self):
        provider = DashboardDataProvider()
        import asyncio
        result = asyncio.run(provider.get_agent_status())
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["agent_id"] == "main"
