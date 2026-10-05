"""Deep behavioral tests for DashboardDataProvider — counter aggregation,
prefix matching, uptime calculation, and integration with real metric objects.
"""

from __future__ import annotations

import asyncio
import time

from general_ludd.observability.dashboard_data import DashboardDataProvider


class FakeMetrics:
    def __init__(self, counters: dict[str, int], started_at: float | None = None) -> None:
        self._counters = counters
        self._started_at = started_at

    def get_counters(self) -> dict[str, int]:
        return dict(self._counters)


class TestCounterWithMetrics:
    def test_exact_match_returns_value(self) -> None:
        metrics = FakeMetrics({"gludd_model_calls_total": 42})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total") == 42

    def test_prefix_match_aggregates_multiple_keys(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_model_calls_total": 10,
                "gludd_model_calls_total:gpt4": 20,
                "gludd_model_calls_total:sonnet": 30,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total") == 60

    def test_no_matching_prefix_returns_zero(self) -> None:
        metrics = FakeMetrics({"other_counter": 99})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total") == 0

    def test_empty_counters_returns_zero(self) -> None:
        metrics = FakeMetrics({})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total") == 0

    def test_partial_prefix_overlap_not_included(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_model_calls_total": 10,
                "gludd_model_calls_total_errors": 5,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total_errors") == 5
        assert provider._get_counter("gludd_model_calls_total") == 15

    def test_prefix_matches_only_beginning(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_model_calls_total": 10,
                "other_gludd_model_calls_total": 99,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_model_calls_total") == 10

    def test_counter_key_is_substring_of_another_key(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_ticks": 5,
                "gludd_ticks_total": 15,
                "gludd_ticks_failed": 3,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_ticks_total") == 15
        assert provider._get_counter("gludd_ticks") == 23

    def test_zero_values_sum_correctly(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_ticks_total": 0,
                "gludd_ticks_total:agent1": 0,
                "gludd_ticks_total:agent2": 0,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_ticks_total") == 0

    def test_many_matching_keys_sums_correctly(self) -> None:
        metrics = FakeMetrics({f"gludd_ticks_total:{i}": i for i in range(1, 101)})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_ticks_total") == 5050


class TestUptimeWithMetrics:
    def test_uptime_with_metrics_returns_positive_float(self) -> None:
        now = time.monotonic()
        metrics = FakeMetrics({}, started_at=now - 10.0)
        provider = DashboardDataProvider(metrics_exporter=metrics)
        uptime = provider._get_uptime()
        assert uptime >= 10.0
        assert uptime < now + 1.0

    def test_uptime_without_metrics_returns_zero(self) -> None:
        provider = DashboardDataProvider()
        assert provider._get_uptime() == 0.0

    def test_uptime_with_uninitialized_metrics_returns_zero(self) -> None:
        provider = DashboardDataProvider(metrics_exporter=FakeMetrics({}))
        assert provider._get_uptime() == 0.0

    def test_uptime_zero_elapsed_returns_near_zero(self) -> None:
        now = time.monotonic()
        metrics = FakeMetrics({}, started_at=now)
        provider = DashboardDataProvider(metrics_exporter=metrics)
        result = provider._get_uptime()
        assert result >= 0.0
        assert result < 1.0

    def test_uptime_large_value_works(self) -> None:
        metrics = FakeMetrics({}, started_at=0.0)
        provider = DashboardDataProvider(metrics_exporter=metrics)
        result = provider._get_uptime()
        assert result > 0
        assert isinstance(result, float)


class TestGetOverviewBehavioral:
    def test_overview_with_metrics_populates_counters(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_model_calls_total": 100,
                "gludd_todos_completed_total": 50,
            },
            started_at=time.monotonic() - 60.0,
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        result = asyncio.run(provider.get_overview())
        assert result["model_calls_today"] == 100
        assert result["todos_completed_today"] == 50
        assert result["uptime"] >= 60.0
        assert result["active_jobs"] == 0
        assert result["spend_today_usd"] == 0.0
        assert isinstance(result["queue_depths"], dict)

    def test_overview_keys_always_present(self) -> None:
        provider = DashboardDataProvider()
        result = asyncio.run(provider.get_overview())
        assert set(result.keys()) == {
            "uptime",
            "active_jobs",
            "queue_depths",
            "model_calls_today",
            "spend_today_usd",
            "todos_completed_today",
        }

    def test_overview_counter_with_labels_sums_all(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_model_calls_total": 3,
                "gludd_model_calls_total:gpt4": 7,
                "gludd_model_calls_total:haiku": 1,
                "gludd_model_calls_total:opus": 2,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        result = asyncio.run(provider.get_overview())
        assert result["model_calls_today"] == 13


class TestGetAgentStatusBehavioral:
    def test_agent_status_with_counter_metrics(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_ticks_total": 42,
                "gludd_ticks_total:main": 15,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        result = asyncio.run(provider.get_agent_status())
        assert len(result) == 1
        assert result[0]["tasks_completed"] == 57

    def test_agent_status_default_structure(self) -> None:
        provider = DashboardDataProvider()
        result = asyncio.run(provider.get_agent_status())
        assert result[0]["status"] == "running"
        assert result[0]["agent_id"] == "main"
        assert isinstance(result[0]["last_tick"], str)
        assert result[0]["tasks_completed"] == 0


class TestGetPipelineHealthBehavioral:
    def test_pipeline_health_all_readings_present(self) -> None:
        provider = DashboardDataProvider()
        result = asyncio.run(provider.get_pipeline_health())
        assert result["event_loop"] == "running"
        assert result["db"] == "connected"
        assert result["worker"] == "available"
        assert result["model_gateway"] == "configured"

    def test_pipeline_health_is_deterministic(self) -> None:
        provider = DashboardDataProvider()
        r1 = asyncio.run(provider.get_pipeline_health())
        r2 = asyncio.run(provider.get_pipeline_health())
        assert r1 == r2


class TestCounterPrefixEdgeCases:
    def test_empty_prefix_matches_all_keys(self) -> None:
        metrics = FakeMetrics({"a": 1, "b": 2, "c": 3})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("") == 6

    def test_counter_key_without_underscore_still_matches(self) -> None:
        metrics = FakeMetrics({"gluddticks": 10, "gludd_ticks_total": 5})
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_ticks_total") == 5
        assert provider._get_counter("gluddticks") == 10

    def test_overlapping_prefix_not_fooled(self) -> None:
        metrics = FakeMetrics(
            {
                "gludd_a_counter": 1,
                "gludd_ab_counter": 2,
                "gludd_abc_counter": 4,
            }
        )
        provider = DashboardDataProvider(metrics_exporter=metrics)
        assert provider._get_counter("gludd_a_counter") == 1
        assert provider._get_counter("gludd_ab_counter") == 2
        assert provider._get_counter("gludd_a") == 7


class TestProviderIndependence:
    def test_providers_do_not_share_mutable_state(self) -> None:
        m1 = FakeMetrics({"gludd_ticks_total": 10})
        m2 = FakeMetrics({"gludd_ticks_total": 20})
        p1 = DashboardDataProvider(metrics_exporter=m1)
        p2 = DashboardDataProvider(metrics_exporter=m2)
        assert p1._get_counter("gludd_ticks_total") == 10
        assert p2._get_counter("gludd_ticks_total") == 20

    def test_setting_metrics_after_init_uses_new_value(self) -> None:
        provider = DashboardDataProvider()
        assert provider._get_counter("gludd_ticks_total") == 0
        metrics = FakeMetrics({"gludd_ticks_total": 99})
        provider._metrics = metrics
        assert provider._get_counter("gludd_ticks_total") == 99
