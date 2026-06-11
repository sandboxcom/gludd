"""Prometheus metrics exporter for General Ludd Agent.

Exports counters, gauges, and histograms for:
- Jobs dispatched/completed/failed per work_type
- Model calls, tokens, costs
- Event loop tick timing
- HTTP request latencies
- Todo queue depths

Also provides log correlation with trace/span IDs for distributed tracing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# In-memory metrics store (no prometheus_client dependency required)
# Each metric is a dict keyed by labels


class MetricsExporter:
    def __init__(self) -> None:
        self._counters: dict[str, dict[str, int]] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._started_at: float = time.monotonic()

    def counter_inc(self, name: str, labels: dict[str, str] | None = None, value: int = 1) -> None:
        key = self._key(name, labels)
        if name not in self._counters:
            self._counters[name] = {}
        self._counters[name][key] = self._counters[name].get(key, 0) + value

    def gauge_set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        self._gauges[key] = value

    def histogram_observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self._key(name, labels)
        if name not in self._histograms:
            self._histograms[name] = []
        self._histograms[name].append(value)
        if len(self._histograms[name]) > 10000:
            self._histograms[name] = self._histograms[name][-5000:]

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return f'{name}{{{",".join(parts)}}}'

    def get_counters(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for counters in self._counters.values():
            result.update(counters)
        return result

    def get_gauges(self) -> dict[str, float]:
        return dict(self._gauges)

    def get_histogram_summary(self, name: str) -> dict[str, float] | None:
        if name not in self._histograms:
            return None
        values = self._histograms[name]
        if not values:
            return None
        sorted_vals = sorted(values)
        return {
            "count": len(values),
            "sum": sum(values),
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "p50": sorted_vals[len(values) // 2],
            "p95": sorted_vals[int(len(values) * 0.95)],
            "p99": sorted_vals[int(len(values) * 0.99)],
        }

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for name, counters in self._counters.items():
            lines.append(f"# HELP {name} Counter")
            lines.append(f"# TYPE {name} counter")
            for key, val in counters.items():
                lines.append(f"{key} {val}")
        for key, val in self._gauges.items():
            base = key.split("{")[0] if "{" in key else key
            lines.append(f"# HELP {base} Gauge")
            lines.append(f"# TYPE {base} gauge")
            lines.append(f"{key} {val}")
        for name in self._histograms:
            summary = self.get_histogram_summary(name)
            if summary:
                lines.append(f"# HELP {name}_count Histogram")
                lines.append(f"# TYPE {name}_count histogram")
                lines.append(f'{name}_count{{}} {summary["count"]}')
                lines.append(f'{name}_sum{{}} {summary["sum"]}')
        lines.append("# HELP gludd_uptime_seconds Uptime")
        lines.append("# TYPE gludd_uptime_seconds gauge")
        lines.append(f"gludd_uptime_seconds {time.monotonic() - self._started_at}")
        return "\n".join(lines) + "\n"

    def get_json(self) -> dict[str, Any]:
        return {
            "counters": self.get_counters(),
            "gauges": self.get_gauges(),
            "histograms": {
                name: self.get_histogram_summary(name)
                for name in self._histograms
            },
            "uptime_seconds": time.monotonic() - self._started_at,
        }


# Singleton
_metrics_exporter: MetricsExporter | None = None


def get_metrics_exporter() -> MetricsExporter:
    global _metrics_exporter
    if _metrics_exporter is None:
        _metrics_exporter = MetricsExporter()
    return _metrics_exporter


# Log correlation helpers
import uuid as _uuid

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
