"""Pluggable observability / pipeline integration layer.

This package defines the *contract* (`base`) for connectors that pull telemetry
from external backends — pipelines (CI/CD), logs, metrics, and traces — and a
runtime registry + `Observability` facade that fans queries across whatever
sources have been registered.

The contract is intentionally concrete-connector-free: `base` depends only on a
structural `Source` Protocol, so the facade can be exercised and reasoned about
without importing any specific backend client. Concrete connectors live in
sibling modules and register themselves into a `SourceRegistry` at runtime.
"""

from __future__ import annotations

from general_ludd.connectors.base import (
    LogSource,
    MetricSource,
    NormalizedRecord,
    Observability,
    PipelineSource,
    Source,
    SourceRegistry,
    TraceSource,
    is_safe_endpoint,
    normalized_record,
)

__all__ = [
    "LogSource",
    "MetricSource",
    "NormalizedRecord",
    "Observability",
    "PipelineSource",
    "Source",
    "SourceRegistry",
    "TraceSource",
    "is_safe_endpoint",
    "normalized_record",
]
