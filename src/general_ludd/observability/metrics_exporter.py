"""Prometheus metrics exporter for General Ludd Agent.

Uses the prometheus-client library for all metric types and exposition.
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.exposition import generate_latest

logger = logging.getLogger(__name__)

_REGISTRY = CollectorRegistry(auto_describe=False)

# CARDINALITY GUARD (anti-DoS).
#
# Metric label VALUES are frequently built from unbounded, caller-controlled
# inputs (full file paths, project ids, raw error strings, URLs). Prometheus
# creates a brand-new time series for every distinct label-value combination,
# so an attacker (or a careless caller) feeding a flood of unique values would
# grow memory 1:1 with the inputs -> unbounded growth / out-of-memory DoS.
#
# We bound cardinality at the exporter so EVERY caller is protected regardless
# of where the value came from: for each (metric, label-key) pair we remember
# at most MAX_LABEL_VALUES_PER_KEY distinct values. Once that budget is spent,
# any further never-before-seen value is normalized to a single overflow bucket
# (OVERFLOW_LABEL_VALUE). The count is preserved (no event is dropped) but the
# number of distinct series stays bounded instead of exploding.
MAX_LABEL_VALUES_PER_KEY = 50
OVERFLOW_LABEL_VALUE = "__other__"


class MetricsExporter:
    """Bounded prometheus-client metric registry for the daemon process."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Initialize counters, gauges, histograms, and the uptime gauge."""
        self._registry = registry or _REGISTRY
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._started_at: float = time.monotonic()
        # Tracks the set of label values already admitted for each
        # (metric_name, label_key); used to enforce the cardinality budget.
        self._seen_label_values: dict[tuple[str, str], set[str]] = {}

        self._uptime = Gauge(
            "gludd_uptime_seconds",
            "Process uptime in seconds",
            registry=self._registry,
        )

    def _bound_labels(self, name: str, labels: dict[str, str]) -> dict[str, str]:
        """Cap label cardinality per (metric, label-key).

        Returns a new labels dict where any value that would exceed the
        per-key budget is collapsed to ``OVERFLOW_LABEL_VALUE``. Values are
        coerced to ``str`` so callers passing ints/paths/etc. still hit the
        same bounded set. Already-seen values always pass through verbatim so
        genuinely low-cardinality dimensions (method, status class) stay exact.
        """
        bounded: dict[str, str] = {}
        for key, raw in labels.items():
            value = str(raw)
            budget_key = (name, key)
            seen = self._seen_label_values.setdefault(budget_key, set())
            if value in seen:
                bounded[key] = value
            elif len(seen) < MAX_LABEL_VALUES_PER_KEY:
                seen.add(value)
                bounded[key] = value
            else:
                # Budget exhausted: fold this novel value into the overflow
                # bucket so it cannot create a new series. The overflow value
                # itself counts as one of the admitted values.
                seen.add(OVERFLOW_LABEL_VALUE)
                bounded[key] = OVERFLOW_LABEL_VALUE
        return bounded

    def counter_inc(self, name: str, labels: dict[str, str] | None = None, value: int = 1) -> None:
        """Increment a named counter, creating it lazily when first used."""
        counter = self._counters.get(name)
        if counter is None:
            label_keys = sorted(labels.keys()) if labels else []
            counter = Counter(name, name, labelnames=label_keys, registry=self._registry)
            self._counters[name] = counter
        if labels:
            counter.labels(**self._bound_labels(name, labels)).inc(value)
        else:
            counter.inc(value)

    def gauge_set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a named gauge, creating it lazily when first used."""
        gauge = self._gauges.get(name)
        if gauge is None:
            label_keys = sorted(labels.keys()) if labels else []
            gauge = Gauge(name, name, labelnames=label_keys, registry=self._registry)
            self._gauges[name] = gauge
        if labels:
            gauge.labels(**self._bound_labels(name, labels)).set(value)
        else:
            gauge.set(value)

    def histogram_observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observation against a named (optionally labeled) histogram."""
        hist = self._histograms.get(name)
        if hist is None:
            label_keys = sorted(labels.keys()) if labels else []
            hist = Histogram(name, name, labelnames=label_keys, registry=self._registry)
            self._histograms[name] = hist
        if labels:
            hist.labels(**self._bound_labels(name, labels)).observe(value)
        else:
            hist.observe(value)

    def render_prometheus(self) -> str:
        """Return all metrics in the Prometheus text exposition format."""
        self._uptime.set(time.monotonic() - self._started_at)
        return generate_latest(self._registry).decode()

    def get_json(self) -> dict[str, Any]:
        """Return all metrics plus uptime as a JSON-serializable dict."""
        self._uptime.set(time.monotonic() - self._started_at)
        samples: dict[str, list[dict[str, Any]]] = {}
        for metric in self._registry.collect():
            metric_samples: list[dict[str, Any]] = []
            for s in metric.samples:
                metric_samples.append(
                    {
                        "name": s.name,
                        "labels": dict(s.labels),
                        "value": s.value,
                    }
                )
            if metric_samples:
                samples[metric.name] = metric_samples
        return {
            "metrics": samples,
            "uptime_seconds": time.monotonic() - self._started_at,
        }

    def get_counters(self) -> dict[str, int]:
        """Return a flat name-to-value dict of every counter sample."""
        result: dict[str, int] = {}
        for name, counter in self._counters.items():
            for sample in counter.collect():
                for s in sample.samples:
                    if s.labels:
                        label_parts = sorted(s.labels.items())
                        key = f"{name}_" + "_".join(f"{k}={v}" for k, v in label_parts)
                    else:
                        key = name
                    result[key] = int(s.value)
        return result

    def get_gauges(self) -> dict[str, float]:
        """Return a flat name-to-value dict of every gauge and histogram sample."""
        result: dict[str, float] = {}
        for name, gauge in self._gauges.items():
            for sample in gauge.collect():
                for s in sample.samples:
                    if s.labels:
                        label_parts = sorted(s.labels.items())
                        key = f"{name}_" + "_".join(f"{k}={v}" for k, v in label_parts)
                    else:
                        key = name
                    result[key] = float(s.value)
        for name, hist in self._histograms.items():
            for sample in hist.collect():
                for s in sample.samples:
                    if s.labels:
                        label_parts = sorted(s.labels.items())
                        key = f"{name}_" + "_".join(f"{k}={v}" for k, v in label_parts)
                    else:
                        key = name
                    result[key] = float(s.value)
        return result


_metrics_exporter: MetricsExporter | None = None


def get_metrics_exporter() -> MetricsExporter:
    """Return the process-wide metrics exporter singleton."""
    global _metrics_exporter
    if _metrics_exporter is None:
        _metrics_exporter = MetricsExporter()
    return _metrics_exporter


_current_trace_id: dict[int, str] = {}


def set_trace_id(trace_id: str | None = None) -> str:
    """Set (or generate) the trace id for the calling thread and return it."""
    import threading

    tid = trace_id or _uuid.uuid4().hex[:16]
    _current_trace_id[threading.get_ident()] = tid
    return tid


def get_trace_id() -> str:
    """Return the calling thread's trace id, or "unknown" when unset."""
    import threading

    return _current_trace_id.get(threading.get_ident(), "unknown")


class CorrelatedLogAdapter(logging.LoggerAdapter):
    """LoggerAdapter that prefixes records with the current trace and span ids."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        """Prefix the message with correlated trace/span identifiers."""
        trace_id = get_trace_id()
        span_id = _uuid.uuid4().hex[:8]
        return f"[trace={trace_id} span={span_id}] {msg}", kwargs


def get_correlated_logger(name: str) -> logging.LoggerAdapter:
    """Return a CorrelatedLogAdapter wrapping the named logger."""
    return CorrelatedLogAdapter(logging.getLogger(name), {})
