"""Deep behavioral tests for MetricsExporter (observability/metrics_exporter.py).

Covers:
- Constructor creates uptime gauge
- counter_inc lazy-creates Counter, increments with/without labels
- gauge_set lazy-creates Gauge, sets with/without labels
- histogram_observe lazy-creates Histogram, records with/without labels
- render_prometheus returns valid Prometheus text format
- get_json returns structured JSON including uptime_seconds
- get_counters returns counter snapshot with label-flattened keys
- get_gauges returns gauge snapshot with label-flattened keys
- Uptime gauge advances on render / get_json calls
- Re-creation uses existing metric (no duplicate registration)
- Cardinality guard: _bound_labels caps novel values at MAX_LABEL_VALUES_PER_KEY
- Cardinality guard: overflow bucket catches excess novel values
- Cardinality guard: already-seen values pass through verbatim
- Cardinality guard: values coerced to str for consistent bucketing
- Cardinality guard: separate metric names have independent budgets
- Custom registry passed through
- Global singleton: get_metrics_exporter returns same instance
- set_trace_id / get_trace_id thread-safe trace context
- CorrelatedLogAdapter prefixes log messages with trace/span
- get_correlated_logger returns CorrelatedLogAdapter
- Empty labels dict treated same as None
- Counter increment by value > 1
- Gauge set to zero
- Histogram observe with zero value
- Registry isolation: custom registry receives no metrics from default
- render_prometheus with empty registry returns minimal output
- get_json with no metrics returns empty samples but includes uptime
"""

from __future__ import annotations

import threading
import time

from general_ludd.observability.metrics_exporter import (
    MAX_LABEL_VALUES_PER_KEY,
    OVERFLOW_LABEL_VALUE,
    CorrelatedLogAdapter,
    MetricsExporter,
    get_correlated_logger,
    get_metrics_exporter,
    get_trace_id,
    set_trace_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_exporter() -> MetricsExporter:
    from prometheus_client import CollectorRegistry

    return MetricsExporter(registry=CollectorRegistry(auto_describe=False))


# ---------------------------------------------------------------------------
# Constructor and uptime gauge
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_creates_uptime_gauge(self):
        ex = _fresh_exporter()
        assert ex._uptime is not None
        assert ex._uptime._name == "gludd_uptime_seconds"

    def test_started_at_stored(self):
        ex = _fresh_exporter()
        assert ex._started_at > 0.0

    def test_custom_registry_accepted(self):
        from prometheus_client import CollectorRegistry

        reg = CollectorRegistry(auto_describe=False)
        ex = MetricsExporter(registry=reg)
        assert ex._registry is reg

    def test_default_registry_is_class_level_shared(self):
        from general_ludd.observability.metrics_exporter import _REGISTRY

        ex1 = MetricsExporter()
        assert ex1._registry is _REGISTRY


# ---------------------------------------------------------------------------
# Counter operations
# ---------------------------------------------------------------------------


def _counter_total(ex: MetricsExporter, name: str) -> float:
    for sample in ex._counters[name].collect():
        for s in sample.samples:
            if s.name == f"{name}_total":
                return s.value
    return 0.0


class TestCounterOperations:
    def test_counter_inc_lazy_creates(self):
        ex = _fresh_exporter()
        assert "test_counter" not in ex._counters
        ex.counter_inc("test_counter")
        assert "test_counter" in ex._counters

    def test_counter_inc_initial_value(self):
        ex = _fresh_exporter()
        ex.counter_inc("my_counter")
        assert _counter_total(ex, "my_counter") == 1.0

    def test_counter_inc_increment_by_value(self):
        ex = _fresh_exporter()
        ex.counter_inc("my_counter", value=5)
        assert _counter_total(ex, "my_counter") == 5.0

    def test_counter_inc_multiple_increments(self):
        ex = _fresh_exporter()
        ex.counter_inc("my_counter")
        ex.counter_inc("my_counter", value=3)
        assert _counter_total(ex, "my_counter") == 4.0

    def test_counter_inc_reuses_existing(self):
        ex = _fresh_exporter()
        ex.counter_inc("same_counter")
        ex.counter_inc("same_counter")
        assert len(ex._counters) == 1

    def test_counter_inc_with_labels(self):
        ex = _fresh_exporter()
        ex.counter_inc("labeled_counter", labels={"method": "GET", "status": "200"})
        rendered = ex.render_prometheus()
        assert 'method="GET"' in rendered
        assert 'status="200"' in rendered

    def test_counter_inc_empty_labels_treated_as_none(self):
        ex = _fresh_exporter()
        ex.counter_inc("plain", labels={})
        assert "plain" in ex._counters

    def test_counter_inc_none_labels(self):
        ex = _fresh_exporter()
        ex.counter_inc("plain")
        assert "plain" in ex._counters


# ---------------------------------------------------------------------------
# Gauge operations
# ---------------------------------------------------------------------------


class TestGaugeOperations:
    def test_gauge_set_lazy_creates(self):
        ex = _fresh_exporter()
        ex.gauge_set("temp", 37.5)
        assert "temp" in ex._gauges

    def test_gauge_set_returns_value(self):
        ex = _fresh_exporter()
        ex.gauge_set("temp", 42.0)
        assert ex.get_gauges()["temp"] == 42.0

    def test_gauge_set_overwrites(self):
        ex = _fresh_exporter()
        ex.gauge_set("temp", 10.0)
        ex.gauge_set("temp", 20.0)
        assert ex.get_gauges()["temp"] == 20.0

    def test_gauge_set_zero(self):
        ex = _fresh_exporter()
        ex.gauge_set("zero", 0.0)
        assert ex.get_gauges()["zero"] == 0.0

    def test_gauge_set_negative(self):
        ex = _fresh_exporter()
        ex.gauge_set("subzero", -5.0)
        assert ex.get_gauges()["subzero"] == -5.0

    def test_gauge_set_with_labels(self):
        ex = _fresh_exporter()
        ex.gauge_set("cpu", 80.5, labels={"host": "a"})
        gauges = ex.get_gauges()
        assert any("host=a" in k for k in gauges)

    def test_gauge_set_reuses_existing(self):
        ex = _fresh_exporter()
        ex.gauge_set("temp", 1.0)
        ex.gauge_set("temp", 2.0)
        assert len(ex._gauges) == 1


# ---------------------------------------------------------------------------
# Histogram operations
# ---------------------------------------------------------------------------


class TestHistogramOperations:
    def test_histogram_observe_lazy_creates(self):
        ex = _fresh_exporter()
        ex.histogram_observe("request_duration", 0.5)
        assert "request_duration" in ex._histograms

    def test_histogram_observe_multiple(self):
        ex = _fresh_exporter()
        for v in [0.1, 0.2, 0.05, 0.3]:
            ex.histogram_observe("latency", v)
        # Observation should not raise
        assert True

    def test_histogram_observe_zero(self):
        ex = _fresh_exporter()
        ex.histogram_observe("zero_latency", 0.0)
        assert True

    def test_histogram_observe_with_labels(self):
        ex = _fresh_exporter()
        ex.histogram_observe("db_latency", 12.3, labels={"endpoint": "query"})
        assert True

    def test_histogram_observe_reuses_existing(self):
        ex = _fresh_exporter()
        ex.histogram_observe("dur", 1.0)
        ex.histogram_observe("dur", 2.0)
        assert len(ex._histograms) == 1


# ---------------------------------------------------------------------------
# render_prometheus
# ---------------------------------------------------------------------------


class TestRenderPrometheus:
    def test_returns_string_type(self):
        ex = _fresh_exporter()
        out = ex.render_prometheus()
        assert isinstance(out, str)

    def test_includes_uptime_gauge(self):
        ex = _fresh_exporter()
        out = ex.render_prometheus()
        assert "gludd_uptime_seconds" in out

    def test_includes_counter(self):
        ex = _fresh_exporter()
        ex.counter_inc("reqs")
        out = ex.render_prometheus()
        assert "reqs" in out

    def test_includes_gauge(self):
        ex = _fresh_exporter()
        ex.gauge_set("mem", 1024.0)
        out = ex.render_prometheus()
        assert "mem" in out

    def test_uptime_advances_on_render(self):
        ex = _fresh_exporter()
        first = ex.render_prometheus()
        time.sleep(0.01)
        second = ex.render_prometheus()
        assert first != second

    def test_empty_registry_returns_minimal_output(self):
        from prometheus_client import CollectorRegistry

        reg = CollectorRegistry(auto_describe=False)
        ex = MetricsExporter(registry=reg)
        out = ex.render_prometheus()
        assert isinstance(out, str)
        assert len(out) > 0


# ---------------------------------------------------------------------------
# get_json
# ---------------------------------------------------------------------------


class TestGetJson:
    def test_returns_dict_with_metrics_and_uptime(self):
        ex = _fresh_exporter()
        result = ex.get_json()
        assert "metrics" in result
        assert "uptime_seconds" in result

    def test_uptime_seconds_is_positive(self):
        ex = _fresh_exporter()
        result = ex.get_json()
        assert result["uptime_seconds"] >= 0.0

    def test_uptime_seconds_non_decreasing(self):
        ex = _fresh_exporter()
        r1 = ex.get_json()
        time.sleep(0.01)
        r2 = ex.get_json()
        assert r2["uptime_seconds"] >= r1["uptime_seconds"]

    def test_counter_appears_in_json(self):
        ex = _fresh_exporter()
        ex.counter_inc("my_metric")
        result = ex.get_json()
        assert "my_metric" in result["metrics"]

    def test_counter_json_structure(self):
        ex = _fresh_exporter()
        ex.counter_inc("my_metric")
        result = ex.get_json()
        samples = result["metrics"]["my_metric"]
        assert isinstance(samples, list)
        assert len(samples) > 0
        sample = samples[0]
        assert "name" in sample
        assert "labels" in sample
        assert "value" in sample

    def test_no_user_metrics_produces_only_uptime(self):
        from prometheus_client import CollectorRegistry

        reg = CollectorRegistry(auto_describe=False)
        ex = MetricsExporter(registry=reg)
        result = ex.get_json()
        assert "uptime_seconds" in result
        assert "gludd_uptime_seconds" in result["metrics"]


# ---------------------------------------------------------------------------
# get_counters
# ---------------------------------------------------------------------------


class TestGetCounters:
    def test_empty_returns_empty_dict(self):
        ex = _fresh_exporter()
        assert ex.get_counters() == {}

    def test_labeled_counter_has_flattened_key(self):
        ex = _fresh_exporter()
        ex.counter_inc("api", labels={"method": "POST"})
        counters = ex.get_counters()
        # key format: api_method=POST
        assert any(k.startswith("api_") for k in counters)

    def test_multiple_label_combinations(self):
        ex = _fresh_exporter()
        ex.counter_inc("api", labels={"method": "GET"})
        ex.counter_inc("api", labels={"method": "POST"})
        counters = ex.get_counters()
        assert len(counters) >= 2

    def test_integer_values(self):
        ex = _fresh_exporter()
        ex.counter_inc("int_check", value=42)
        assert _counter_total(ex, "int_check") == 42.0

    def test_multiple_counters_independent(self):
        ex = _fresh_exporter()
        ex.counter_inc("a")
        ex.counter_inc("b")
        counters = ex.get_counters()
        assert "a" in counters
        assert "b" in counters


# ---------------------------------------------------------------------------
# get_gauges
# ---------------------------------------------------------------------------


class TestGetGauges:
    def test_empty_returns_empty_dict(self):
        ex = _fresh_exporter()
        assert ex.get_gauges() == {}

    def test_labeled_gauge_has_flattened_key(self):
        ex = _fresh_exporter()
        ex.gauge_set("cpu", 50.0, labels={"host": "web-1"})
        gauges = ex.get_gauges()
        assert any("host=web-1" in k for k in gauges)

    def test_float_values(self):
        ex = _fresh_exporter()
        ex.gauge_set("pi", 3.14159)
        val = ex.get_gauges()["pi"]
        assert isinstance(val, float)


# ---------------------------------------------------------------------------
# Cardinality guard — _bound_labels
# ---------------------------------------------------------------------------


class TestCardinalityGuardNovel:
    def test_first_value_passes_verbatim(self):
        ex = _fresh_exporter()
        result = ex._bound_labels("metric", {"path": "/foo"})
        assert result["path"] == "/foo"

    def test_already_seen_value_passes_verbatim(self):
        ex = _fresh_exporter()
        for _ in range(5):
            result = ex._bound_labels("metric", {"path": "/home"})
            assert result["path"] == "/home"

    def test_exactly_max_values_before_overflow(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            result = ex._bound_labels("metric", {"path": f"/p{i}"})
            assert result["path"] == f"/p{i}"

    def test_exceeds_max_triggers_overflow(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric", {"path": f"/p{i}"})
        # Next novel value should be mapped to __other__
        result = ex._bound_labels("metric", {"path": "/overflow_trigger"})
        assert result["path"] == OVERFLOW_LABEL_VALUE

    def test_overflow_bucket_stable_after_first_occurrence(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric", {"path": f"/p{i}"})
        ex._bound_labels("metric", {"path": "/new1"})
        # Subsequent novel values also map to __other__
        result = ex._bound_labels("metric", {"path": "/new2"})
        assert result["path"] == OVERFLOW_LABEL_VALUE

    def test_values_coerced_to_str(self):
        ex = _fresh_exporter()
        result = ex._bound_labels("metric", {"status": 200})
        assert result["status"] == "200"
        assert isinstance(result["status"], str)

    def test_different_metric_names_independent_budgets(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric_a", {"x": f"v{i}"})
        # metric_b should still have a full budget
        result = ex._bound_labels("metric_b", {"x": "fresh"})
        assert result["x"] == "fresh"

    def test_different_label_keys_independent_budgets(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric", {"key_a": f"v{i}"})
        # key_b on the same metric should still have a full budget
        result = ex._bound_labels("metric", {"key_b": "fresh"})
        assert result["key_b"] == "fresh"

    def test_overflow_label_counted_as_admitted(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric", {"path": f"/p{i}"})
        ex._bound_labels("metric", {"path": "/first_overflow"})
        # The overflow bucket itself is in the seen set
        budget_key = ("metric", "path")
        assert OVERFLOW_LABEL_VALUE in ex._seen_label_values[budget_key]

    def test_multiple_labels_in_one_call(self):
        ex = _fresh_exporter()
        result = ex._bound_labels("metric", {"a": "1", "b": "2"})
        assert result["a"] == "1"
        assert result["b"] == "2"

    def test_one_label_overs_one_under(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY):
            ex._bound_labels("metric", {"a": f"v{i}"})
        result = ex._bound_labels("metric", {"a": "/overflow", "b": "still_fresh"})
        assert result["a"] == OVERFLOW_LABEL_VALUE
        assert result["b"] == "still_fresh"


# ---------------------------------------------------------------------------
# Cardinality guard — integration through counter_inc / gauge_set
# ---------------------------------------------------------------------------


class TestCardinalityGuardIntegration:
    def test_counter_inc_enforces_bound_labels(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY + 5):
            ex.counter_inc("reqs", labels={"path": f"/api/{i}"})
        counters = ex.get_counters()
        # All labeled keys should map to either the original value or __other__
        assert any(OVERFLOW_LABEL_VALUE in k for k in counters)

    def test_gauge_set_enforces_bound_labels(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY + 3):
            ex.gauge_set("cpu", float(i), labels={"host": f"h{i}"})
        gauges = ex.get_gauges()
        assert any(OVERFLOW_LABEL_VALUE in k for k in gauges)

    def test_no_metrics_dropped_despite_cardinality_cap(self):
        ex = _fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY + 10):
            ex.counter_inc("loopy", labels={"id": str(i)})
        rendered = ex.render_prometheus()
        distinct_series = sum(1 for line in rendered.splitlines() if "loopy_total{" in line)
        assert distinct_series == MAX_LABEL_VALUES_PER_KEY + 1


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_get_metrics_exporter_returns_same_instance(self):
        a = get_metrics_exporter()
        b = get_metrics_exporter()
        assert a is b

    def test_singleton_is_metrics_exporter(self):
        assert isinstance(get_metrics_exporter(), MetricsExporter)


# ---------------------------------------------------------------------------
# Trace ID functions
# ---------------------------------------------------------------------------


class TestTraceId:
    def test_set_trace_id_returns_string(self):
        tid = set_trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_set_trace_id_explicit_value(self):
        tid = set_trace_id("explicit-trace-123")
        assert tid == "explicit-trace-123"

    def test_get_trace_id_after_set(self):
        set_trace_id("my-trace")
        assert get_trace_id() == "my-trace"

    def test_get_trace_id_defaults_to_unknown(self):
        # Simulate a new thread that hasn't set a trace id
        result: list[str] = []

        def _capture():
            result.append(get_trace_id())

        t = threading.Thread(target=_capture)
        t.start()
        t.join()
        assert result[0] == "unknown"

    def test_trace_id_is_thread_local(self):
        set_trace_id("main-thread")
        result: list[str] = []

        def _capture():
            result.append(get_trace_id())

        t = threading.Thread(target=_capture)
        t.start()
        t.join()
        assert result[0] == "unknown"
        assert get_trace_id() == "main-thread"

    def test_missing_module_imports_no_error(self):
        # Exercise the lazy imports inside the functions
        import importlib

        mod = importlib.import_module("general_ludd.observability.metrics_exporter")
        assert hasattr(mod, "set_trace_id")
        assert hasattr(mod, "get_trace_id")


# ---------------------------------------------------------------------------
# CorrelatedLogAdapter
# ---------------------------------------------------------------------------


class TestCorrelatedLogAdapter:
    def test_process_injects_trace_and_span(self):
        import logging

        adapter = CorrelatedLogAdapter(logging.getLogger("test"), {})
        msg, _kwargs = adapter.process("hello", {})
        assert "trace=" in msg
        assert "span=" in msg
        assert msg.endswith("hello")

    def test_process_preserves_message_text(self):
        import logging

        set_trace_id("known-trace")
        adapter = CorrelatedLogAdapter(logging.getLogger("test"), {})
        msg, _kwargs = adapter.process("original message", {})
        assert "original message" in msg
        assert "known-trace" in msg

    def test_get_correlated_logger_returns_adapter(self):

        obj = get_correlated_logger("test.logger.name")
        assert isinstance(obj, CorrelatedLogAdapter)


# ---------------------------------------------------------------------------
# Registry isolation
# ---------------------------------------------------------------------------


class TestRegistryIsolation:
    def test_custom_registry_does_not_get_default_metrics(self):
        from prometheus_client import CollectorRegistry

        reg = CollectorRegistry(auto_describe=False)
        ex = MetricsExporter(registry=reg)
        # Add a metric to this custom registry
        ex.counter_inc("custom_only")
        # Create a second exporter with the default shared registry
        ex2 = get_metrics_exporter()
        assert "custom_only" not in ex2.get_counters()

    def test_two_custom_registries_are_independent(self):
        from prometheus_client import CollectorRegistry

        reg1 = CollectorRegistry(auto_describe=False)
        reg2 = CollectorRegistry(auto_describe=False)
        ex1 = MetricsExporter(registry=reg1)
        ex2 = MetricsExporter(registry=reg2)
        ex1.counter_inc("only_in_reg1")
        assert "only_in_reg1" in ex1.get_counters()
        assert "only_in_reg1" not in ex2.get_counters()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_render_prometheus_after_histogram(self):
        ex = _fresh_exporter()
        ex.histogram_observe("lat", 0.5)
        out = ex.render_prometheus()
        assert "lat" in out

    def test_get_json_after_histogram(self):
        ex = _fresh_exporter()
        ex.histogram_observe("lat", 0.5)
        result = ex.get_json()
        assert "lat" in result["metrics"]

    def test_mixed_metric_types_in_render(self):
        ex = _fresh_exporter()
        ex.counter_inc("reqs")
        ex.gauge_set("temp", 25.0)
        ex.histogram_observe("lat", 0.1)
        out = ex.render_prometheus()
        assert "reqs" in out
        assert "temp" in out
        assert "lat" in out

    def test_mixed_metric_types_in_json(self):
        ex = _fresh_exporter()
        ex.counter_inc("reqs")
        ex.gauge_set("temp", 25.0)
        ex.histogram_observe("lat", 0.1)
        result = ex.get_json()
        assert "reqs" in result["metrics"]
        assert "temp" in result["metrics"]
        assert "lat" in result["metrics"]

    def test_very_long_metric_name(self):
        long_name = "a" * 200
        ex = _fresh_exporter()
        ex.counter_inc(long_name)
        assert long_name in ex.get_counters()

    def test_special_characters_in_label_value(self):
        ex = _fresh_exporter()
        ex.counter_inc("spec", labels={"key": "value with spaces & special!"})
        counters = ex.get_counters()
        assert any("value with spaces & special!" in k for k in counters)

    def test_unicode_label_value(self):
        ex = _fresh_exporter()
        ex.counter_inc("unicode", labels={"sig": "\N{SNAKE}"})
        counters = ex.get_counters()
        assert any("\N{SNAKE}" in k for k in counters)

    def test_empty_label_value(self):
        ex = _fresh_exporter()
        ex.counter_inc("empty_key", labels={"foo": ""})
        counters = ex.get_counters()
        assert any(
            "foo=" in k and not any(c.isalpha() for c in k.split("foo=", 1)[1][:1] if c != "_") for k in counters
        )
