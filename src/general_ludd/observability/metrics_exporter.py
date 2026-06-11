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


class MetricsExporter:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or _REGISTRY
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._started_at: float = time.monotonic()

        self._uptime = Gauge(
            "gludd_uptime_seconds", "Process uptime in seconds",
            registry=self._registry,
        )

    def counter_inc(self, name: str, labels: dict[str, str] | None = None, value: int = 1) -> None:
        counter = self._counters.get(name)
        if counter is None:
            label_keys = sorted(labels.keys()) if labels else []
            counter = Counter(name, name, labelnames=label_keys, registry=self._registry)
            self._counters[name] = counter
        if labels:
            counter.labels(**labels).inc(value)
        else:
            counter.inc(value)

    def gauge_set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        gauge = self._gauges.get(name)
        if gauge is None:
            label_keys = sorted(labels.keys()) if labels else []
            gauge = Gauge(name, name, labelnames=label_keys, registry=self._registry)
            self._gauges[name] = gauge
        if labels:
            gauge.labels(**labels).set(value)
        else:
            gauge.set(value)

    def histogram_observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        hist = self._histograms.get(name)
        if hist is None:
            label_keys = sorted(labels.keys()) if labels else []
            hist = Histogram(name, name, labelnames=label_keys, registry=self._registry)
            self._histograms[name] = hist
        if labels:
            hist.labels(**labels).observe(value)
        else:
            hist.observe(value)

    def render_prometheus(self) -> str:
        self._uptime.set(time.monotonic() - self._started_at)
        return generate_latest(self._registry).decode()

    def get_json(self) -> dict[str, Any]:
        self._uptime.set(time.monotonic() - self._started_at)
        samples: dict[str, list[dict[str, Any]]] = {}
        for metric in self._registry.collect():
            metric_samples: list[dict[str, Any]] = []
            for s in metric.samples:
                metric_samples.append({
                    "name": s.name,
                    "labels": dict(s.labels),
                    "value": s.value,
                })
            if metric_samples:
                samples[metric.name] = metric_samples
        return {
            "metrics": samples,
            "uptime_seconds": time.monotonic() - self._started_at,
        }

    def get_counters(self) -> dict[str, int]:
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
        return result


_metrics_exporter: MetricsExporter | None = None


def get_metrics_exporter() -> MetricsExporter:
    global _metrics_exporter
    if _metrics_exporter is None:
        _metrics_exporter = MetricsExporter()
    return _metrics_exporter


_current_trace_id: dict[int, str] = {}


def set_trace_id(trace_id: str | None = None) -> str:
    import threading
    tid = trace_id or _uuid.uuid4().hex[:16]
    _current_trace_id[threading.get_ident()] = tid
    return tid


def get_trace_id() -> str:
    import threading
    return _current_trace_id.get(threading.get_ident(), "unknown")


class CorrelatedLogAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        trace_id = get_trace_id()
        span_id = _uuid.uuid4().hex[:8]
        return f"[trace={trace_id} span={span_id}] {msg}", kwargs


def get_correlated_logger(name: str) -> logging.LoggerAdapter:
    return CorrelatedLogAdapter(logging.getLogger(name), {})
