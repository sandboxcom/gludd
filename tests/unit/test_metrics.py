"""Unit tests for metrics collector — agent tracking, model usage, cost estimation."""

from __future__ import annotations

import time

import pytest

from general_ludd.metrics.collector import (
    AgentMetrics,
    CostEstimate,
    MetricsCollector,
    ModelUsage,
)


class TestModelUsageSuccessRate:
    def test_success_rate_zero_calls(self):
        mu = ModelUsage(model_id="gpt-4")
        assert mu.success_rate == 0.0

    def test_success_rate_all_success(self):
        mu = ModelUsage(model_id="gpt-4")
        mu.record_call(100, 50, True)
        mu.record_call(200, 75, True)
        assert mu.success_rate == 1.0

    def test_success_rate_mixed(self):
        mu = ModelUsage(model_id="gpt-4")
        mu.record_call(100, 50, True)
        mu.record_call(100, 50, False)
        assert mu.success_rate == pytest.approx(0.5)

    def test_success_rate_all_failures(self):
        mu = ModelUsage(model_id="gpt-4")
        mu.record_call(100, 50, False)
        mu.record_call(100, 50, False)
        mu.record_call(100, 50, False)
        assert mu.success_rate == pytest.approx(0.0)


class TestModelUsageRecordCall:
    def test_record_single_call(self):
        mu = ModelUsage(model_id="gpt-4")
        mu.record_call(100, 50, True)
        assert mu.total_calls == 1
        assert mu.successful_calls == 1
        assert mu.failed_calls == 0
        assert mu.total_input_tokens == 100
        assert mu.total_output_tokens == 50

    def test_record_multiple_calls_accumulate(self):
        mu = ModelUsage(model_id="gpt-4")
        mu.record_call(100, 50, True)
        mu.record_call(200, 75, False)
        assert mu.total_calls == 2
        assert mu.successful_calls == 1
        assert mu.failed_calls == 1
        assert mu.total_input_tokens == 300
        assert mu.total_output_tokens == 125

    def test_record_call_updates_cost(self):
        mu = ModelUsage(
            model_id="gpt-4",
            cost_per_input_token=0.00003,
            cost_per_output_token=0.00006,
        )
        mu.record_call(1000, 500, True)
        expected = 1000 * 0.00003 + 500 * 0.00006
        assert mu.total_cost_usd == pytest.approx(expected)

    def test_record_call_cost_recalculated_on_each_call(self):
        mu = ModelUsage(
            model_id="gpt-4",
            cost_per_input_token=0.01,
            cost_per_output_token=0.02,
        )
        mu.record_call(100, 50, True)
        mu.record_call(200, 100, True)
        expected = 300 * 0.01 + 150 * 0.02
        assert mu.total_cost_usd == pytest.approx(expected)


class TestAgentMetricsProperties:
    def test_uptime_is_positive(self):
        agent = AgentMetrics(agent_id="a1", started_at=time.time() - 10)
        assert agent.uptime_seconds >= 10.0

    def test_total_tokens_empty(self):
        agent = AgentMetrics(agent_id="a1")
        assert agent.total_tokens == 0

    def test_total_tokens_with_usage(self):
        agent = AgentMetrics(agent_id="a1")
        agent.record_model_call("gpt-4", 100, 50, True)
        agent.record_model_call("claude", 200, 75, True)
        assert agent.total_tokens == 425

    def test_total_cost_empty(self):
        agent = AgentMetrics(agent_id="a1")
        assert agent.total_cost_usd == 0.0

    def test_total_cost_with_usage(self):
        agent = AgentMetrics(agent_id="a1")
        agent.record_model_call(
            "gpt-4", 1000, 500, True,
            cost_per_input_token=0.01, cost_per_output_token=0.02,
        )
        expected = 1000 * 0.01 + 500 * 0.02
        assert agent.total_cost_usd == pytest.approx(expected)


class TestAgentMetricsGetOrCreateUsage:
    def test_creates_new_usage(self):
        agent = AgentMetrics(agent_id="a1")
        usage = agent.get_or_create_usage("gpt-4")
        assert isinstance(usage, ModelUsage)
        assert usage.model_id == "gpt-4"
        assert "gpt-4" in agent.model_usage

    def test_returns_existing_usage(self):
        agent = AgentMetrics(agent_id="a1")
        first = agent.get_or_create_usage("gpt-4")
        second = agent.get_or_create_usage("gpt-4")
        assert first is second

    def test_creates_with_kwargs(self):
        agent = AgentMetrics(agent_id="a1")
        usage = agent.get_or_create_usage(
            "gpt-4", provider="openai", cost_per_input_token=0.01,
        )
        assert usage.provider == "openai"
        assert usage.cost_per_input_token == 0.01


class TestAgentMetricsRecordModelCall:
    def test_updates_last_activity(self):
        agent = AgentMetrics(agent_id="a1", last_activity=0.0)
        before = time.time()
        agent.record_model_call("gpt-4", 100, 50, True)
        assert agent.last_activity >= before

    def test_creates_usage_if_missing(self):
        agent = AgentMetrics(agent_id="a1")
        agent.record_model_call("gpt-4", 100, 50, True)
        assert "gpt-4" in agent.model_usage
        assert agent.model_usage["gpt-4"].total_calls == 1


class TestCostEstimateProperties:
    def test_cost_as_pct_zero_subscription(self):
        ce = CostEstimate(total_cost_usd=10.0)
        assert ce.cost_as_pct_of_subscription == 0.0

    def test_cost_as_pct_with_subscription(self):
        ce = CostEstimate(
            total_cost_usd=25.0, subscription_cost_usd_per_month=100.0,
        )
        assert ce.cost_as_pct_of_subscription == pytest.approx(25.0)

    def test_tokens_as_pct_zero_weekly(self):
        ce = CostEstimate(tokens_used=500)
        assert ce.tokens_as_pct_of_weekly == 0.0

    def test_tokens_as_pct_with_weekly(self):
        ce = CostEstimate(tokens_per_week=1000, tokens_used=250)
        assert ce.tokens_as_pct_of_weekly == pytest.approx(25.0)

    def test_tokens_remaining_this_week(self):
        ce = CostEstimate(tokens_per_week=1000, tokens_used=300)
        assert ce.tokens_remaining_this_week == 700

    def test_tokens_remaining_cannot_go_negative(self):
        ce = CostEstimate(tokens_per_week=100, tokens_used=500)
        assert ce.tokens_remaining_this_week == 0


class TestMetricsCollectorRegistration:
    def test_register_agent(self):
        mc = MetricsCollector()
        agent = mc.register_agent("a1", agent_name="Bot", project="proj")
        assert agent.agent_id == "a1"
        assert agent.agent_name == "Bot"
        assert agent.project == "proj"
        assert agent.status == "running"

    def test_unregister_agent(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.unregister_agent("a1")
        assert mc.get_agent("a1").status == "stopped"

    def test_unregister_unknown_agent_no_error(self):
        mc = MetricsCollector()
        mc.unregister_agent("nonexistent")

    def test_get_agent_returns_none_for_unknown(self):
        mc = MetricsCollector()
        assert mc.get_agent("ghost") is None


class TestMetricsCollectorListAgents:
    def test_list_all_agents(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.register_agent("a2")
        assert len(mc.list_agents()) == 2

    def test_list_agents_by_status(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.register_agent("a2")
        mc.unregister_agent("a2")
        running = mc.list_agents(status="running")
        stopped = mc.list_agents(status="stopped")
        assert len(running) == 1
        assert len(stopped) == 1
        assert running[0].agent_id == "a1"
        assert stopped[0].agent_id == "a2"

    def test_list_running_agents(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.register_agent("a2")
        mc.unregister_agent("a2")
        running = mc.list_running_agents()
        assert len(running) == 1
        assert running[0].agent_id == "a1"

    def test_list_agents_empty(self):
        mc = MetricsCollector()
        assert mc.list_agents() == []


class TestMetricsCollectorModelCalls:
    def test_record_model_call_for_agent(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        agent = mc.get_agent("a1")
        assert agent.total_tokens == 150
        assert agent.model_usage["gpt-4"].total_calls == 1

    def test_record_model_call_unknown_agent_still_tracks_global(self):
        mc = MetricsCollector()
        mc.record_model_call("ghost", "gpt-4", 100, 50, True)
        global_usage = mc.get_global_model_usage()
        assert "gpt-4" in global_usage
        assert global_usage["gpt-4"].total_calls == 1

    def test_global_model_usage_aggregates_across_agents(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.register_agent("a2")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a2", "gpt-4", 200, 75, True)
        global_usage = mc.get_global_model_usage()
        assert global_usage["gpt-4"].total_calls == 2
        assert global_usage["gpt-4"].total_input_tokens == 300
        assert global_usage["gpt-4"].total_output_tokens == 125

    def test_global_model_usage_multiple_models(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a1", "claude-3", 200, 100, True)
        global_usage = mc.get_global_model_usage()
        assert len(global_usage) == 2
        assert "gpt-4" in global_usage
        assert "claude-3" in global_usage

    def test_record_model_call_with_cost(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call(
            "a1", "gpt-4", 1000, 500, True,
            cost_per_input_token=0.01, cost_per_output_token=0.02,
        )
        agent = mc.get_agent("a1")
        expected = 1000 * 0.01 + 500 * 0.02
        assert agent.total_cost_usd == pytest.approx(expected)


class TestMetricsCollectorCostEstimate:
    def test_cost_estimate_no_agents(self):
        mc = MetricsCollector()
        ce = mc.get_cost_estimate(
            subscription_name="Pro", subscription_cost_usd_per_month=200.0,
            tokens_per_week=50000,
        )
        assert ce.total_cost_usd == 0.0
        assert ce.subscription_name == "Pro"
        assert ce.cost_as_pct_of_subscription == 0.0

    def test_cost_estimate_with_agents(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call(
            "a1", "gpt-4", 1000, 500, True,
            cost_per_input_token=0.01, cost_per_output_token=0.02,
        )
        ce = mc.get_cost_estimate(
            subscription_cost_usd_per_month=100.0, tokens_per_week=5000,
        )
        assert ce.total_cost_usd > 0
        assert ce.cost_as_pct_of_subscription > 0
        assert ce.tokens_used == 1500
        assert ce.tokens_as_pct_of_weekly == pytest.approx(30.0)
        assert ce.tokens_remaining_this_week == 3500


class TestMetricsCollectorAgentSummary:
    def test_summary_unknown_agent(self):
        mc = MetricsCollector()
        assert mc.get_agent_summary("ghost") == {}

    def test_summary_known_agent(self):
        mc = MetricsCollector()
        mc.register_agent("a1", agent_name="Bot", project="test")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        summary = mc.get_agent_summary("a1")
        assert summary["agent_id"] == "a1"
        assert summary["agent_name"] == "Bot"
        assert summary["project"] == "test"
        assert summary["status"] == "running"
        assert summary["total_tokens"] == 150
        assert "gpt-4" in summary["models_used"]
        assert summary["models_used"]["gpt-4"]["total_calls"] == 1
        assert summary["models_used"]["gpt-4"]["success_rate"] == 1.0

    def test_summary_includes_failed_calls(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a1", "gpt-4", 100, 50, False)
        summary = mc.get_agent_summary("a1")
        model_info = summary["models_used"]["gpt-4"]
        assert model_info["total_calls"] == 2
        assert model_info["successful_calls"] == 1
        assert model_info["failed_calls"] == 1
        assert model_info["success_rate"] == pytest.approx(0.5)


class TestMetricsCollectorFullReport:
    def test_full_report_empty(self):
        mc = MetricsCollector()
        report = mc.get_full_report()
        assert report["total_agents"] == 0
        assert report["running_agents"] == 0
        assert report["agents"] == []
        assert report["global_model_usage"] == {}

    def test_full_report_with_data(self):
        mc = MetricsCollector()
        mc.register_agent("a1", agent_name="Alpha")
        mc.register_agent("a2", agent_name="Beta")
        mc.unregister_agent("a2")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a2", "gpt-4", 200, 75, False)
        report = mc.get_full_report()
        assert report["total_agents"] == 2
        assert report["running_agents"] == 1
        assert len(report["agents"]) == 2
        assert "gpt-4" in report["global_model_usage"]
        gu = report["global_model_usage"]["gpt-4"]
        assert gu["total_calls"] == 2
        assert gu["success_rate"] == pytest.approx(0.5)

    def test_full_report_global_usage_includes_cost(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call(
            "a1", "gpt-4", 1000, 500, True,
            cost_per_input_token=0.01, cost_per_output_token=0.02,
        )
        report = mc.get_full_report()
        gu = report["global_model_usage"]["gpt-4"]
        assert gu["total_cost_usd"] > 0


class TestEdgeCases:
    def test_zero_tokens_zero_cost(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        summary = mc.get_agent_summary("a1")
        assert summary["total_tokens"] == 0
        assert summary["total_cost_usd"] == 0.0

    def test_model_usage_default_cost_rates(self):
        mu = ModelUsage(model_id="free-model")
        mu.record_call(1000, 500, True)
        assert mu.total_cost_usd == 0.0

    def test_agent_uptime_increases(self):
        agent = AgentMetrics(agent_id="a1", started_at=time.time() - 60)
        uptime1 = agent.uptime_seconds
        time.sleep(0.05)
        uptime2 = agent.uptime_seconds
        assert uptime2 >= uptime1

    def test_multiple_agents_same_model(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.register_agent("a2")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a2", "gpt-4", 200, 100, False)
        a1 = mc.get_agent("a1")
        a2 = mc.get_agent("a2")
        assert a1.model_usage["gpt-4"].successful_calls == 1
        assert a2.model_usage["gpt-4"].failed_calls == 1
        global_usage = mc.get_global_model_usage()["gpt-4"]
        assert global_usage.total_calls == 2
        assert global_usage.total_input_tokens == 300

    def test_get_global_model_usage_returns_dict_copy(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        usage = mc.get_global_model_usage()
        usage["new-key"] = ModelUsage(model_id="fake")
        assert "new-key" not in mc.get_global_model_usage()
