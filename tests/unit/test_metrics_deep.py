"""Deep metrics collection and aggregation tests.

Covers MetricsExporter gauge/counter/histogram operations, lookup, label filtering,
Prometheus/JSON export, concurrent access safety, and MetricsCollector aggregation
across time windows, projects, and task types.
"""

from __future__ import annotations

import re
import threading
import time

import pytest
from prometheus_client import CollectorRegistry

from general_ludd.metrics.collector import (
    AgentMetrics,
    CostEstimate,
    MetricsCollector,
    ModelUsage,
)
from general_ludd.observability.metrics_exporter import (
    MAX_LABEL_VALUES_PER_KEY,
    MetricsExporter,
)
from general_ludd.scoring.metric import MetricConfig, compute_w_dollar


def _prometheus_counter_value(text: str, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Extract a counter's _total value from Prometheus text exposition."""
    if labels:
        label_parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        pattern = rf"{name}_total\{{{label_parts}\}}\s+([\d.e+-]+)"
    else:
        pattern = rf"^{name}_total\s+([\d.e+-]+)"
    m = re.search(pattern, text, re.MULTILINE)
    if m:
        return float(m.group(1))
    return None


def _prometheus_gauge_value(text: str, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Extract a gauge's value from Prometheus text exposition."""
    if labels:
        label_parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        pattern = rf"{name}\{{{label_parts}\}}\s+([\d.e+-]+)"
    else:
        pattern = rf"^{name}\s+([\d.e+-]+)"
    m = re.search(pattern, text, re.MULTILINE)
    if m:
        return float(m.group(1))
    return None


# ── MetricsExporter — Counter operations ────────────────────────────────


class TestExporterCounterOps:
    def test_counter_inc_basic(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("my_counter")
        exporter.counter_inc("my_counter", value=3)
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "my_counter") == pytest.approx(4.0)

    def test_counter_inc_labelled_accumulates(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("http_reqs", {"method": "GET"}, value=2)
        exporter.counter_inc("http_reqs", {"method": "GET"}, value=3)
        exporter.counter_inc("http_reqs", {"method": "POST"})
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "http_reqs", {"method": "GET"}) == pytest.approx(5.0)
        assert _prometheus_counter_value(text, "http_reqs", {"method": "POST"}) == pytest.approx(1.0)

    def test_counter_reuses_same_metric_object(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("reuse_counter", {"a": "1"})
        exporter.counter_inc("reuse_counter", {"a": "1"}, value=2)
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "reuse_counter", {"a": "1"}) == pytest.approx(3.0)

    def test_counter_label_keys_preserved_across_calls(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("c1", {"x": "v1", "y": "v2"})
        exporter.counter_inc("c1", {"x": "v3", "y": "v4"})
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "c1", {"x": "v1", "y": "v2"}) == pytest.approx(1.0)
        assert _prometheus_counter_value(text, "c1", {"x": "v3", "y": "v4"}) == pytest.approx(1.0)


# ── MetricsExporter — Gauge operations ──────────────────────────────────


class TestExporterGaugeOps:
    def test_gauge_set_basic(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.gauge_set("temperature", 99.9)
        gauges = exporter.get_gauges()
        assert gauges["temperature"] == pytest.approx(99.9)

    def test_gauge_set_overwrites_previous_value(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.gauge_set("temperature", 10.0)
        exporter.gauge_set("temperature", 20.0)
        gauges = exporter.get_gauges()
        assert gauges["temperature"] == pytest.approx(20.0)

    def test_gauge_set_labelled_isolation(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.gauge_set("pool_size", 5.0, {"pool": "http"})
        exporter.gauge_set("pool_size", 3.0, {"pool": "db"})
        gauges = exporter.get_gauges()
        assert gauges["pool_size_pool=db"] == pytest.approx(3.0)
        assert gauges["pool_size_pool=http"] == pytest.approx(5.0)

    def test_gauge_reuses_same_metric_object(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.gauge_set("g", 1.0)
        exporter.gauge_set("g", 2.0)
        assert len(exporter._gauges) == 1


# ── MetricsExporter — Histogram operations ──────────────────────────────


class TestExporterHistogramOps:
    def test_histogram_observe_basic(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.histogram_observe("latency_seconds", 0.1)
        exporter.histogram_observe("latency_seconds", 0.3)
        rendered = exporter.render_prometheus()
        assert "latency_seconds" in rendered

    def test_histogram_labelled_separation(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.histogram_observe("latency", 0.1, {"status": "200"})
        exporter.histogram_observe("latency", 0.5, {"status": "500"})
        rendered = exporter.render_prometheus()
        assert "latency" in rendered

    def test_histogram_reuses_same_metric_object(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.histogram_observe("h", 1.0)
        exporter.histogram_observe("h", 2.0)
        assert len(exporter._histograms) == 1


# ── MetricsExporter — Export format correctness ──────────────────────────


class TestExporterPrometheusFormat:
    def test_prometheus_returns_string(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("requests_total")
        result = exporter.render_prometheus()
        assert isinstance(result, str)
        assert "requests_total" in result

    def test_prometheus_includes_uptime(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        result = exporter.render_prometheus()
        assert "gludd_uptime_seconds" in result

    def test_prometheus_newlines_are_present(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("ctr")
        result = exporter.render_prometheus()
        assert result.endswith("\n")

    def test_prometheus_includes_created_suffix_for_counters(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("myctr")
        result = exporter.render_prometheus()
        assert "myctr_total" in result
        assert "myctr_created" in result

    def test_prometheus_counter_value_parsable(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        for _ in range(42):
            exporter.counter_inc("meaning_of_life")
        text = exporter.render_prometheus()
        val = _prometheus_counter_value(text, "meaning_of_life")
        assert val == pytest.approx(42.0)


class TestExporterJsonFormat:
    def test_json_has_metrics_and_uptime(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("c", {"m": "GET"})
        result = exporter.get_json()
        assert "metrics" in result
        assert "uptime_seconds" in result
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] >= 0

    def test_json_counter_has_total_and_created_samples(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("c", {"m": "GET"}, value=2)
        exporter.counter_inc("c", {"m": "POST"})
        result = exporter.get_json()
        samples = result["metrics"]
        assert "c" in samples
        names = {s["name"] for s in samples["c"]}
        assert "c_total" in names
        assert "c_created" in names

    def test_json_different_metric_types_coexist(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("c")
        exporter.gauge_set("g", 42.0)
        exporter.histogram_observe("h", 1.5)
        result = exporter.get_json()
        assert "c" in result["metrics"]
        assert "g" in result["metrics"]
        assert "h" in result["metrics"]

    def test_json_empty_no_crash(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        result = exporter.get_json()
        assert isinstance(result, dict)
        assert "uptime_seconds" in result

    def test_json_labeled_counter_samples(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("hits", {"path": "/home"}, value=10)
        result = exporter.get_json()
        samples = result["metrics"]["hits"]
        labels_set = {tuple(sorted(s["labels"].items())) for s in samples if s["labels"]}
        assert (("path", "/home"),) in labels_set


# ── MetricsExporter — Label-based filtering / cardinality ────────────────


class TestExporterLabelFiltering:
    def test_low_cardinality_labels_preserved_verbatim(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        for _ in range(3):
            exporter.counter_inc("r", {"status": "200"})
        for _ in range(2):
            exporter.counter_inc("r", {"status": "404"})
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "r", {"status": "200"}) == pytest.approx(3.0)
        assert _prometheus_counter_value(text, "r", {"status": "404"}) == pytest.approx(2.0)

    def test_overflow_bucket_applied_when_limit_hit(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        unique_values = MAX_LABEL_VALUES_PER_KEY + 20
        for i in range(unique_values):
            exporter.counter_inc("overflow_test", {"path": f"/u/{i}"})
        text = exporter.render_prometheus()
        overflow_val = _prometheus_counter_value(text, "overflow_test", {"path": "__other__"})
        assert overflow_val is not None
        assert overflow_val == pytest.approx(float(unique_values - MAX_LABEL_VALUES_PER_KEY))

    def test_bound_labels_coerces_values_to_strings(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        exporter.counter_inc("c", {"code": str(200)})
        exporter.counter_inc("c", {"code": str(200)})
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "c", {"code": "200"}) == pytest.approx(2.0)

    def test_bound_labels_previously_seen_stays_exact(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        for _ in range(MAX_LABEL_VALUES_PER_KEY):
            exporter.counter_inc("stable", {"v": "exact_val"})
        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "stable", {"v": "exact_val"}) == pytest.approx(
            float(MAX_LABEL_VALUES_PER_KEY)
        )

    def test_distinct_label_keys_independent_budgets(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        for i in range(MAX_LABEL_VALUES_PER_KEY + 5):
            exporter.counter_inc("multi_key", {"a": f"v{i}", "b": "constant"})
        text = exporter.render_prometheus()
        val = _prometheus_counter_value(text, "multi_key", {"a": "__other__", "b": "constant"})
        assert val is not None
        assert val == pytest.approx(5.0)


# ── MetricsExporter — Concurrent access safety ──────────────────────────


class TestExporterConcurrency:
    def test_concurrent_counter_increments_no_loss(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        threads_count = 8
        calls_per_thread = 250

        def worker():
            for _ in range(calls_per_thread):
                exporter.counter_inc("concurrent_counter")

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        text = exporter.render_prometheus()
        assert _prometheus_counter_value(text, "concurrent_counter") == pytest.approx(
            float(threads_count * calls_per_thread)
        )

    def test_concurrent_mixed_ops_no_crash(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        errors = []

        def counter_worker():
            try:
                for i in range(200):
                    exporter.counter_inc("mixed", {"w": str(i % 5)})
            except Exception as e:
                errors.append(e)

        def gauge_worker():
            try:
                for i in range(200):
                    exporter.gauge_set("mixed_gauge", float(i), {"w": str(i % 3)})
            except Exception as e:
                errors.append(e)

        def histogram_worker():
            try:
                for i in range(200):
                    exporter.histogram_observe("mixed_hist", float(i % 10), {"w": str(i % 4)})
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=counter_worker),
            threading.Thread(target=gauge_worker),
            threading.Thread(target=histogram_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent mixed ops raised: {errors}"
        rendered = exporter.render_prometheus()
        assert "mixed" in rendered
        assert "mixed_gauge" in rendered
        assert "mixed_hist" in rendered

    def test_concurrent_json_export_safe(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        errors = []

        def writer():
            try:
                for i in range(500):
                    exporter.counter_inc("conc_json", {"x": str(i % 10)})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(500):
                    exporter.get_json()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent JSON read/write raised: {errors}"


# ── MetricsCollector — Aggregation across projects ──────────────────────


class TestMetricsCollectorProjectAggregation:
    def test_task_time_aggregation(self):
        mc = MetricsCollector()
        mc.record_task_time("proj-a", 12.5)
        mc.record_task_time("proj-a", 7.3)
        mc.record_task_time("proj-b", 2.0)
        times = mc.get_time_by_project()
        assert times["proj-a"] == pytest.approx(19.8)
        assert times["proj-b"] == pytest.approx(2.0)

    def test_task_loc_aggregation(self):
        mc = MetricsCollector()
        mc.record_task_loc("proj-a", 100)
        mc.record_task_loc("proj-a", -30)
        mc.record_task_loc("proj-b", 50)
        locs = mc.get_loc_by_project()
        assert locs["proj-a"] == 70
        assert locs["proj-b"] == 50

    def test_cost_by_project(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="alpha")
        mc.register_agent("a2", project="alpha")
        mc.register_agent("a3", project="beta")
        mc.record_model_call("a1", "m", 1000, 0, True, cost_per_input_token=0.01, cost_per_output_token=0.0)
        mc.record_model_call("a2", "m", 500, 0, True, cost_per_input_token=0.01, cost_per_output_token=0.0)
        mc.record_model_call("a3", "m", 200, 0, True, cost_per_input_token=0.01, cost_per_output_token=0.0)
        costs = mc.get_cost_by_project()
        assert costs["alpha"] == pytest.approx(15.0)
        assert costs["beta"] == pytest.approx(2.0)

    def test_project_accounting_summary(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="p1")
        mc.record_task_time("p1", 10.0)
        mc.record_task_loc("p1", 50)
        mc.record_model_call("a1", "m", 1000, 0, True, cost_per_input_token=0.01, cost_per_output_token=0.0)
        summary = mc.get_project_accounting_summary()
        assert "p1" in summary
        assert summary["p1"]["cost_usd"] == pytest.approx(10.0)
        assert summary["p1"]["elapsed_seconds"] == pytest.approx(10.0)
        assert summary["p1"]["loc_changed"] == 50

    def test_project_accounting_empty(self):
        mc = MetricsCollector()
        assert mc.get_project_accounting_summary() == {}

    def test_task_time_skips_empty_project_id(self):
        mc = MetricsCollector()
        mc.record_task_time("", 1.0)
        mc.record_task_time(None, 2.0)  # type: ignore[arg-type]
        assert mc.get_time_by_project() == {}


# ── MetricsCollector — Per-task-type metrics ────────────────────────────


class TestMetricsCollectorTaskTypeMetrics:
    def test_record_task_result_creates_entry(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, tokens_in=100, tokens_out=50, latency_ms=1500.0)
        summary = mc.get_task_type_summary()
        assert "code_generation" in summary
        s = summary["code_generation"]
        assert s["total"] == 1
        assert s["successes"] == 1
        assert s["failures"] == 0
        assert s["tokens_in"] == 100
        assert s["tokens_out"] == 50
        assert s["latency_total_ms"] == pytest.approx(1500.0)

    def test_record_task_result_aggregates_multiple(self):
        mc = MetricsCollector()
        mc.record_task_result("review", True, latency_ms=100.0)
        mc.record_task_result("review", False, latency_ms=200.0, error="SyntaxError")
        mc.record_task_result("review", False, latency_ms=300.0, error="ImportError: no module")
        summary = mc.get_task_type_summary()
        s = summary["review"]
        assert s["total"] == 3
        assert s["successes"] == 1
        assert s["failures"] == 2
        assert s["success_rate"] == pytest.approx(1 / 3)
        assert s["latency_avg_ms"] == pytest.approx(200.0)
        assert s["latency_min_ms"] == pytest.approx(100.0)
        assert s["latency_max_ms"] == pytest.approx(300.0)
        assert s["failure_modes"]["SyntaxError"] == 1
        assert s["failure_modes"]["ImportError"] == 1

    def test_record_task_result_with_phase_latency(self):
        mc = MetricsCollector()
        mc.record_task_result("build", True, phase_latency_ms={"compile": 500.0, "link": 200.0})
        mc.record_task_result("build", True, phase_latency_ms={"compile": 700.0, "link": 300.0})
        summary = mc.get_task_type_summary()
        s = summary["build"]
        assert s["phase_avg_ms"]["compile"] == pytest.approx(600.0)
        assert s["phase_avg_ms"]["link"] == pytest.approx(250.0)

    def test_record_task_result_success_rate_empty(self):
        mc = MetricsCollector()
        summary = mc.get_task_type_summary()
        assert summary == {}

    def test_record_task_result_error_categorization(self):
        mc = MetricsCollector()
        mc.record_task_result("t", False, error="ValueError: bad input")
        mc.record_task_result("t", False, error="ValueError: also bad")
        mc.record_task_result("t", False, error="RuntimeError xyz")
        summary = mc.get_task_type_summary()
        s = summary["t"]
        assert s["failure_modes"]["ValueError"] == 2
        assert s["failure_modes"]["RuntimeError"] == 1

    def test_reset_task_metrics(self):
        mc = MetricsCollector()
        mc.record_task_result("t1", True, latency_ms=100.0)
        mc.record_task_result("t2", False, latency_ms=200.0)
        assert len(mc.get_task_type_summary()) == 2
        mc.reset_task_metrics()
        assert mc.get_task_type_summary() == {}

    def test_task_type_latency_stats_min_max_avg(self):
        mc = MetricsCollector()
        for ms in [5.0, 15.0, 10.0, 20.0]:
            mc.record_task_result("query", True, latency_ms=ms)
        summary = mc.get_task_type_summary()
        s = summary["query"]
        assert s["latency_min_ms"] == pytest.approx(5.0)
        assert s["latency_max_ms"] == pytest.approx(20.0)
        assert s["latency_avg_ms"] == pytest.approx(12.5)


# ── MetricsCollector — Failover metrics ─────────────────────────────────


class TestMetricsCollectorFailover:
    def test_record_failover_increments_count(self):
        mc = MetricsCollector()
        mc.record_failover("primary-a", "fallback-b")
        mc.record_failover("primary-a", "fallback-c")
        report = mc.get_full_report()
        assert report["model_usage"]["failover_count"] == 2

    def test_failover_count_starts_zero(self):
        mc = MetricsCollector()
        report = mc.get_full_report()
        assert report["model_usage"]["failover_count"] == 0


# ── MetricsCollector — Full report model_usage facet ────────────────────


class TestMetricsCollectorModelUsageFacet:
    def test_model_usage_facet_error_count_matches_failed_calls(self):
        mc = MetricsCollector()
        mc.record_model_call("a1", "gpt-4", 10, 5, True)
        mc.record_model_call("a1", "gpt-4", 10, 5, False)
        mc.record_model_call("a1", "gpt-4", 10, 5, False)
        report = mc.get_full_report()
        mu = report["model_usage"]["gpt-4"]
        assert mu["total_calls"] == 3
        assert mu["successful_calls"] == 1
        assert mu["failed_calls"] == 2
        assert mu["error_count"] == 2
        assert mu["success_rate"] == pytest.approx(1 / 3)

    def test_model_usage_facet_unknown_model_not_present(self):
        mc = MetricsCollector()
        report = mc.get_full_report()
        assert "nonexistent" not in report["model_usage"]


# ── MetricsCollector — List agents with project filter ──────────────────


class TestMetricsCollectorProjectFiltering:
    def test_list_agents_by_project(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="alpha")
        mc.register_agent("a2", project="alpha")
        mc.register_agent("a3", project="beta")
        alpha_agents = mc.list_agents(project="alpha")
        beta_agents = mc.list_agents(project="beta")
        assert len(alpha_agents) == 2
        assert len(beta_agents) == 1

    def test_list_agents_by_project_and_status(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="p1")
        mc.register_agent("a2", project="p1")
        mc.unregister_agent("a2")
        running = mc.list_agents(project="p1", status="running")
        stopped = mc.list_agents(project="p1", status="stopped")
        assert len(running) == 1
        assert len(stopped) == 1
        assert running[0].agent_id == "a1"


# ── AgentMetrics — Deep registration and model use ──────────────────────


class TestAgentMetricsDeep:
    def test_record_model_call_propagates_kwargs_to_usage(self):
        agent = AgentMetrics(agent_id="a1")
        agent.record_model_call("gpt-4", 100, 50, True, provider="openai")
        usage = agent.model_usage["gpt-4"]
        assert usage.provider == "openai"

    def test_record_model_call_updates_last_activity_on_each_call(self):
        agent = AgentMetrics(agent_id="a1", last_activity=0.0)
        t1 = time.time()
        agent.record_model_call("m", 10, 5, True)
        assert agent.last_activity >= t1
        t2 = time.time()
        agent.record_model_call("m", 10, 5, False)
        assert agent.last_activity >= t2

    def test_status_defaults_to_running(self):
        agent = AgentMetrics(agent_id="new")
        assert agent.status == "running"

    def test_get_or_create_usage_returns_same_for_same_id(self):
        agent = AgentMetrics(agent_id="a1")
        u1 = agent.get_or_create_usage("m1")
        u2 = agent.get_or_create_usage("m1")
        assert u1 is u2

    def test_get_or_create_usage_different_for_different_ids(self):
        agent = AgentMetrics(agent_id="a1")
        u1 = agent.get_or_create_usage("m1")
        u2 = agent.get_or_create_usage("m2")
        assert u1 is not u2


# ── ModelUsage — Deep success rate and cost ─────────────────────────────


class TestModelUsageDeep:
    def test_success_rate_exactly_one_third(self):
        mu = ModelUsage(model_id="m")
        mu.record_call(10, 5, True)
        mu.record_call(10, 5, False)
        mu.record_call(10, 5, False)
        assert mu.success_rate == pytest.approx(1 / 3)

    def test_record_call_total_tokens_accumulate(self):
        mu = ModelUsage(model_id="m")
        mu.record_call(100, 50, True)
        mu.record_call(200, 100, False)
        assert mu.total_input_tokens == 300
        assert mu.total_output_tokens == 150

    def test_record_call_cost_zero_when_no_cost_rates_set(self):
        mu = ModelUsage(model_id="free")
        mu.record_call(1000, 500, True)
        assert mu.total_cost_usd == 0.0

    def test_record_call_cost_correct_with_rates(self):
        mu = ModelUsage(model_id="paid", cost_per_input_token=0.03, cost_per_output_token=0.06)
        mu.record_call(100, 50, True)
        mu.record_call(200, 100, False)
        expected = 300 * 0.03 + 150 * 0.06
        assert mu.total_cost_usd == pytest.approx(expected)


# ── CostEstimate — Properties and edge cases ────────────────────────────


class TestCostEstimateDeep:
    def test_cost_estimate_all_zero_defaults(self):
        ce = CostEstimate()
        assert ce.total_cost_usd == 0.0
        assert ce.subscription_name == ""
        assert ce.cost_as_pct_of_subscription == 0.0
        assert ce.tokens_as_pct_of_weekly == 0.0
        assert ce.tokens_remaining_this_week == 0

    def test_weeks_per_month_default(self):
        ce = CostEstimate()
        assert ce.weeks_per_month == pytest.approx(4.33)

    def test_tokens_remaining_exact_boundary(self):
        ce = CostEstimate(tokens_per_week=100, tokens_used=100)
        assert ce.tokens_remaining_this_week == 0

    def test_tokens_remaining_negative_clamped_to_zero(self):
        ce = CostEstimate(tokens_per_week=50, tokens_used=200)
        assert ce.tokens_remaining_this_week == 0


# ── Scoring metric — W$ formula ─────────────────────────────────────────


class TestScoringW_Dollar:
    def test_basic_computation(self):
        result = compute_w_dollar(0.5, 1.0)
        expected = 0.5 / 0.3010299956639812
        assert result == pytest.approx(expected)

    def test_cost_one_gives_log10_two_denominator(self):
        result = compute_w_dollar(1.0, 1.0)
        assert result == pytest.approx(1.0 / 0.3010299956639812)

    def test_zero_cost_returns_composite_score(self):
        result = compute_w_dollar(0.8, 0.0)
        assert result == pytest.approx(0.8)

    def test_high_cost_reduces_score(self):
        cheap = compute_w_dollar(0.8, 0.1)
        expensive = compute_w_dollar(0.8, 10.0)
        assert cheap > expensive

    def test_composite_score_boundaries(self):
        result = compute_w_dollar(0.0, 1.0)
        assert result == pytest.approx(0.0)

    def test_composite_score_out_of_range_raises(self):
        with pytest.raises(ValueError, match="composite_score"):
            compute_w_dollar(-0.1, 1.0)
        with pytest.raises(ValueError, match="composite_score"):
            compute_w_dollar(1.1, 1.0)

    def test_negative_cost_raises(self):
        with pytest.raises(ValueError, match="median_dollars_per_mtok"):
            compute_w_dollar(0.5, -0.01)

    def test_config_floor_and_ceiling_clamp(self):
        cfg = MetricConfig(score_floor=0.3, score_ceiling=0.7)
        low = compute_w_dollar(0.05, 1.0, config=cfg)
        high = compute_w_dollar(1.0, 0.1, config=cfg)
        assert low == pytest.approx(0.3)
        assert high == pytest.approx(0.7)

    def test_config_custom_log_base(self):
        cfg_base2 = MetricConfig(log_base=2.0, offset=0.5)
        result = compute_w_dollar(1.0, 3.5, config=cfg_base2)
        assert result == pytest.approx(1.0 / 2.0)

    def test_very_high_cost_damps_score(self):
        result = compute_w_dollar(1.0, 99.0)
        assert result == pytest.approx(0.5)

    def test_metricconfig_defaults(self):
        cfg = MetricConfig()
        assert cfg.log_base == 10.0
        assert cfg.offset == 1.0
        assert cfg.score_floor == 0.0
        assert cfg.score_ceiling == float("inf")

    def test_metricconfig_instances_are_hashable(self):
        cfg = MetricConfig(log_base=5.0)
        d = {cfg: "test"}
        assert d[cfg] == "test"


# ── MetricsExporter — Singleton get_metrics_exporter ────────────────────


class TestMetricsExporterSingleton:
    def test_get_metrics_exporter_returns_same_instance(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        e1 = get_metrics_exporter()
        e2 = get_metrics_exporter()
        assert e1 is e2

    def test_get_metrics_exporter_twice_uses_same_registry(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        e1 = get_metrics_exporter()
        e1.counter_inc("singleton_counter")
        e2 = get_metrics_exporter()
        text = e2.render_prometheus()
        assert _prometheus_counter_value(text, "singleton_counter") == pytest.approx(1.0)


# ── MetricsCollector — agent list by multiple filter combinations ───────


class TestMetricsCollectorMultiFilter:
    def test_list_agents_status_and_project_narrow(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="p1")
        mc.register_agent("a2", project="p1")
        mc.unregister_agent("a2")
        mc.register_agent("a3", project="p2")
        mc.unregister_agent("a3")
        result = mc.list_agents(status="running", project="p1")
        assert len(result) == 1
        assert result[0].agent_id == "a1"

    def test_list_agents_no_match_returns_empty(self):
        mc = MetricsCollector()
        mc.register_agent("a1", project="p1")
        result = mc.list_agents(project="nonexistent")
        assert result == []
