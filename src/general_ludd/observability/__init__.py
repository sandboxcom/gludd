"""Observability — execution tracing, model comparison, benchmark recording."""

__all__ = (
    "AutoBenchmarkRecorder",
    "ExecutionSpan",
    "ExecutionTrace",
    "ModelComparison",
    "OTelBridge",
    "compute_scores_from_trace",
)

from general_ludd.observability.comparison import ModelComparison
from general_ludd.observability.otel_bridge import OTelBridge
from general_ludd.observability.recorder import (
    AutoBenchmarkRecorder,
    compute_scores_from_trace,
)
from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace
