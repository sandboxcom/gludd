"""Cardinality-bomb regression tests for MetricsExporter.

Metric label VALUES were built from unbounded, caller-controlled inputs
(full file paths, project_id, raw error strings, URLs). Each distinct value
creates a brand-new Prometheus time series, so a flood of distinct values
grows memory 1:1 with the inputs -> unbounded growth / DoS.

These tests emit a flood of distinct label values and assert the number of
distinct emitted series stays BOUNDED (does not grow 1:1 with the inputs).
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry

from general_ludd.observability.metrics_exporter import (
    MAX_LABEL_VALUES_PER_KEY,
    MetricsExporter,
)


def _series_count(exporter: MetricsExporter, metric_name: str) -> int:
    """Number of distinct (label-set) series emitted for a metric."""
    seen = set()
    for metric in exporter._registry.collect():
        for s in metric.samples:
            # Counters expose _total/_created; restrict to the named metric's
            # primary samples so the count reflects distinct label-sets only.
            if s.name == metric_name or s.name == f"{metric_name}_total":
                seen.add(tuple(sorted(s.labels.items())))
    return len(seen)


class TestCounterCardinalityBounded:
    def test_flood_of_unique_paths_does_not_grow_series_one_to_one(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        n = 10_000
        for i in range(n):
            # Caller-controlled, unbounded: a raw request path / project id.
            exporter.counter_inc(
                "gludd_requests_total", {"path": f"/projects/{i}/file_{i}.py"}
            )

        series = _series_count(exporter, "gludd_requests_total")
        # The whole point: 10k distinct inputs must NOT produce ~10k series.
        assert series <= MAX_LABEL_VALUES_PER_KEY + 1, (
            f"cardinality bomb: {series} distinct series from {n} unique inputs"
        )

    def test_total_count_is_preserved_under_overflow(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        n = 5_000
        for i in range(n):
            exporter.counter_inc("gludd_events_total", {"error": f"boom-{i}"})

        # No event may be dropped: every increment is still counted, just folded
        # into a bounded set of series (allowlisted + an overflow bucket).
        total = 0.0
        for metric in exporter._registry.collect():
            for s in metric.samples:
                if s.name == "gludd_events_total":
                    total += s.value
        assert total == float(n)


class TestHistogramCardinalityBounded:
    def test_flood_of_unique_labels_bounds_histogram_series(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        n = 10_000
        for i in range(n):
            exporter.histogram_observe(
                "gludd_latency_seconds", 0.1, labels={"route": f"/u/{i}"}
            )

        distinct = set()
        for metric in exporter._registry.collect():
            for s in metric.samples:
                if s.name.startswith("gludd_latency_seconds"):
                    distinct.add(s.labels.get("route"))
        # distinct may include the overflow bucket but must be small + bounded.
        assert len(distinct) <= MAX_LABEL_VALUES_PER_KEY + 1


class TestGaugeCardinalityBounded:
    def test_flood_of_unique_labels_bounds_gauge_series(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        n = 10_000
        for i in range(n):
            exporter.gauge_set(
                "gludd_inflight", float(i), labels={"id": f"task-{i}"}
            )

        series = _series_count(exporter, "gludd_inflight")
        assert series <= MAX_LABEL_VALUES_PER_KEY + 1


class TestBoundedLabelsStillUseful:
    def test_low_cardinality_values_are_preserved_verbatim(self):
        exporter = MetricsExporter(registry=CollectorRegistry(auto_describe=False))
        for method in ("GET", "POST", "GET", "DELETE"):
            exporter.counter_inc("gludd_http_requests_total", {"method": method})

        # The metric stays useful: real low-cardinality values are kept as-is.
        labels_seen = set()
        for metric in exporter._registry.collect():
            for s in metric.samples:
                if s.name == "gludd_http_requests_total":
                    labels_seen.add(s.labels.get("method"))
        assert {"GET", "POST", "DELETE"} <= labels_seen
